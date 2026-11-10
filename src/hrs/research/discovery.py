"""Rebuildable hybrid discovery over generated cards and source-bound entity records.

This index returns leads, never original quotations or historical approval.
"""

import json
import logging
import threading
import time

from filelock import FileLock, Timeout
from opensearchpy import helpers
from sqlalchemy import func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from hrs.library.deletion import require_active
from hrs.processing.hashing import digest
from hrs.processing.workspace import identity
from hrs.research.models import DOSSIER_SOURCE_FIELDS
from hrs.research.sources import current_card_ids, current_source_conditions
from hrs.search.compute import (
    EMBEDDING_IDENTITY,
    RetrievalCompute,
    normalizer,
    query_vector,
    search_client,
)
from hrs.search.indexing import BookIndexer
from hrs.search.inputs import ranking_windows
from hrs.search.ranking import fuse
from hrs.storage import models as db

DISCOVERY_RULE = "research-derived-1400-v3"
logger = logging.getLogger(__name__)


def discovery_records(cards, entities, *, workspace_id, generation):
    rows = []
    for kind, records in (("card", cards), ("entity", entities)):
        for record in records:
            if record.get("state") not in {"verified", "human_accepted"}:
                continue
            title = record.get("title") or record.get("name", "")
            fields = [title, record.get("summary", ""), record.get("description", "")]
            fields.extend(record.get("tags", []))
            fields.extend(
                " ".join(filter(None, [item.get("title"), item.get("text")]))
                for item in record.get("items", [])
            )
            fields.extend(
                alias if isinstance(alias, str) else alias.get("name", "")
                for alias in record.get("aliases", [])
            )
            fields.extend(record.get("limitations", []))
            dossier = record.get("dossier") or {}
            fields.extend(
                text
                for key in DOSSIER_SOURCE_FIELDS
                for text in (
                    (dossier.get(key) or {}).get("value", ""),
                    (dossier.get(key) or {}).get("note", ""),
                )
            )
            fields.extend(
                text
                for unit in dossier.get("argument_units", [])
                for text in (
                    unit["title"],
                    unit["claim"],
                    unit["applicability_limit"],
                    unit["corroboration_needed"],
                )
            )
            fields.extend(
                (value or {}).get("value", "")
                for key, value in (dossier.get("citation") or {}).items()
                if key in {"full", "short", "printed_pages"}
            )
            fields.extend(record.get("questions", []))
            fields.extend(
                " ".join(filter(None, [value.get("value"), value.get("note")]))
                for value in (record.get("metadata") or {}).values()
                if isinstance(value, dict)
            )
            fields.extend(
                " ".join(
                    filter(None, [event.get("name"), event.get("description"), event.get("action")])
                )
                for event in record.get("events", [])
            )
            fields.extend(
                " ".join(filter(None, [relation.get("kind"), relation.get("reason")]))
                for relation in record.get("cross_relations", [])
            )
            text = "\n".join(filter(None, fields))
            for position, start in enumerate(range(0, len(text), 1280)):
                chunk = text[start : start + 1400]
                rows.append(
                    {
                        "id": digest([generation, kind, record["id"], position]),
                        "record_id": record.get("canonical_id", record["id"])
                        if kind == "entity"
                        else record["id"],
                        "record_kind": kind,
                        "record_version": record.get("version", 1),
                        "owner_card_id": record.get("card_id", record["id"]),
                        "workspace_id": workspace_id,
                        "book_id": record["book_id"],
                        "research_run_id": record["run_id"],
                        "source_run_id": record.get("source_run_id"),
                        "publication_sha256": record.get("publication_sha256"),
                        "article_id": record.get("article_id"),
                        "article_sha256": record.get("article_sha256"),
                        "generation": generation,
                        "title": title,
                        "text": chunk,
                        "derived": True,
                        "source_evidence": False,
                    }
                )
    return rows


def enqueue_discovery(connection, source_run_id, research_run_id, input_sha256, *, rebuild=False):
    statement = pg_insert(db.outbox).values(
        dedup_key=f"index-research:{research_run_id}:{input_sha256}",
        run_id=source_run_id,
        kind="index_research",
        payload={"research_run_id": research_run_id, "input_sha256": input_sha256},
    )
    connection.execute(
        statement.on_conflict_do_update(
            index_elements=[db.outbox.c.dedup_key],
            set_={"delivered": False, "created_at": func.now()},
        )
        if rebuild
        else statement.on_conflict_do_nothing(index_elements=[db.outbox.c.dedup_key])
    )


class ResearchDiscovery:
    def __init__(self, settings, engine, objects, store):
        self.settings, self.engine, self.objects, self.store = settings, engine, objects, store
        self.name = settings.opensearch_index + "-research"
        self.client = search_client(settings.opensearch_url)
        self.runtime = RetrievalCompute(settings)
        self.workspace_id = identity(store)["workspace_id"]

    def index(self, run_id, expected_input):
        with self.engine.connect() as connection:
            row = (
                connection.execute(select(db.research_runs).where(db.research_runs.c.id == run_id))
                .mappings()
                .one_or_none()
            )
            if row is None or row["summary"].get("discovery_input_sha256") != expected_input:
                return {"obsolete": True}
            card_rows = (
                connection.execute(
                    select(db.research_cards).where(db.research_cards.c.run_id == run_id)
                )
                .mappings()
                .all()
            )
            entity_rows = (
                connection.execute(
                    select(db.research_entities).where(db.research_entities.c.run_id == run_id)
                )
                .mappings()
                .all()
            )
        if row["workspace_id"] != self.workspace_id:
            raise ValueError("Research discovery workspace differs from source projection")
        generation = digest([DISCOVERY_RULE, self.workspace_id, expected_input, EMBEDDING_IDENTITY])
        cards = [json.loads(self.objects.read_bytes(item["content"])) for item in card_rows]
        entities = [json.loads(self.objects.read_bytes(item["content"])) for item in entity_rows]
        records = discovery_records(
            cards, entities, workspace_id=self.workspace_id, generation=generation
        )
        indexer = BookIndexer(self.settings, self.engine)
        saved = indexer.embed_cached(
            row["source_run_id"],
            [{**record, "retrieval_text": record["text"]} for record in records],
            {},
        )
        indexer.ensure_index(self.name)
        self.client.indices.put_mapping(
            index=self.name,
            body={
                "properties": {
                    key: {"type": "keyword"}
                    for key in (
                        "record_id",
                        "record_kind",
                        "research_run_id",
                        "workspace_id",
                        "owner_card_id",
                    )
                }
            },
        )
        helpers.bulk(
            self.client,
            [
                {
                    "_index": self.name,
                    "_id": record["id"],
                    "_source": {
                        **record,
                        "text_search": normalizer().convert(record["text"]),
                        "title_search": normalizer().convert(record["title"]),
                    },
                }
                for record in saved["chunks"]
            ],
            chunk_size=100,
            refresh="wait_for",
        )
        with self.engine.begin() as connection:
            require_active(connection, row["book_id"])
            current = (
                connection.execute(
                    select(db.research_runs)
                    .where(db.research_runs.c.id == run_id)
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if (
                current is None
                or current["summary"].get("discovery_input_sha256") != expected_input
            ):
                return {"obsolete": True}
            statement = pg_insert(db.research_discovery_indexes).values(
                id=run_id, input_sha256=expected_input, generation=generation, chunks=len(records)
            )
            connection.execute(
                statement.on_conflict_do_update(
                    index_elements=[db.research_discovery_indexes.c.id],
                    set_={
                        "input_sha256": expected_input,
                        "generation": generation,
                        "chunks": len(records),
                        "updated_at": func.now(),
                    },
                )
            )
            connection.execute(
                insert(db.events).values(
                    book_id=row["book_id"],
                    run_id=row["source_run_id"],
                    kind="research.indexed",
                    payload={
                        "research_run_id": run_id,
                        "generation": generation,
                        "chunks": len(records),
                    },
                )
            )
        return {"generation": generation, "chunks": len(records)}

    def scope(self, book_ids=None):
        conditions = [
            db.research_runs.c.workspace_id == self.workspace_id,
            db.books.c.state.not_in(["deleted", "deleting", "delete_failed"]),
            db.research_runs.c.id.in_(
                select(db.research_cards.c.run_id).where(
                    db.research_cards.c.id.in_(current_card_ids(self.workspace_id))
                )
            ),
            *current_source_conditions(db.research_runs, db.runs, indexed=True),
        ]
        if book_ids is not None:
            conditions.append(db.research_runs.c.book_id.in_(book_ids))
        relation = (
            db.research_runs.join(db.runs, db.runs.c.id == db.research_runs.c.source_run_id)
            .join(db.books, db.books.c.id == db.research_runs.c.book_id)
            .outerjoin(
                db.research_discovery_indexes,
                db.research_discovery_indexes.c.id == db.research_runs.c.id,
            )
        )
        with self.engine.connect() as connection:
            rows = (
                connection.execute(
                    select(
                        db.research_runs.c.id,
                        db.research_runs.c.summary,
                        db.research_discovery_indexes.c.input_sha256,
                        db.research_discovery_indexes.c.generation,
                    )
                    .select_from(relation)
                    .where(*conditions)
                )
                .mappings()
                .all()
            )
            card_ids = list(
                connection.scalars(
                    select(db.research_cards.c.id).where(
                        db.research_cards.c.id.in_(current_card_ids(self.workspace_id)),
                        *(
                            [db.research_cards.c.book_id.in_(book_ids)]
                            if book_ids is not None
                            else []
                        ),
                    )
                )
            )
        ready = [
            r
            for r in rows
            if r["input_sha256"] == r["summary"].get("discovery_input_sha256")
            and r["generation"]
            == digest([DISCOVERY_RULE, self.workspace_id, r["input_sha256"], EMBEDDING_IDENTITY])
        ]
        return {
            "card_ids": card_ids,
            "generations": [r["generation"] for r in ready],
            "runs": [r["id"] for r in ready],
            "total_runs": len(rows),
            "ready_runs": len(ready),
            "state": "ready" if len(ready) == len(rows) else "partial" if ready else "building",
        }

    def search(self, query, *, book_ids=None, semantic=True, limit=8):
        scope = self.scope(book_ids)
        card_ids = scope.pop("card_ids")
        if scope["total_runs"] == 0:
            return {
                "available": True,
                "backend": "hybrid" if semantic else "lexical",
                "index": scope,
                "records": [],
            }
        if not scope["generations"] or not self.client.indices.exists(index=self.name):
            return {"available": False, "backend": "unavailable", "index": scope, "records": []}
        filters = [
            {"term": {"workspace_id": self.workspace_id}},
            {"terms": {"generation": scope["generations"]}},
            {"terms": {"owner_card_id": card_ids}},
        ]
        lexical = self.client.search(
            index=self.name,
            body={
                "size": max(30, limit),
                "query": {
                    "bool": {
                        "filter": filters,
                        "must": [
                            {
                                "multi_match": {
                                    "query": normalizer().convert(query),
                                    "fields": ["title_search^2", "text_search"],
                                }
                            }
                        ],
                    }
                },
                "_source": {"excludes": ["vector"]},
            },
        )["hits"]["hits"]
        if semantic:
            vector = query_vector(
                str(self.settings.models_root),
                self.settings.retrieval_device,
                self.settings.retrieval_endpoint,
                digest(EMBEDDING_IDENTITY),
                query,
            )
            semantic_hits = self.client.search(
                index=self.name,
                body={
                    "size": max(30, limit),
                    "query": {
                        "knn": {
                            "vector": {
                                "vector": vector,
                                "k": max(30, limit),
                                "filter": {"bool": {"filter": filters}},
                            }
                        }
                    },
                    "_source": {"excludes": ["vector"]},
                },
            )["hits"]["hits"]
            rows = fuse(lexical, semantic_hits)[: max(30, limit)]
            if rows:
                texts, owners = ranking_windows(
                    query,
                    [r["text"] for r in rows],
                    self.runtime.tokenizer("BAAI/bge-reranker-v2-m3"),
                )
                scores = self.runtime.compute("rerank", texts, query=query)["scores"]
                ranked = [float("-inf")] * len(rows)
                for owner, score in zip(owners, scores, strict=True):
                    ranked[owner] = max(ranked[owner], score)
                rows = [dict(r, score=s) for r, s in zip(rows, ranked, strict=True)]
        else:
            rows = [dict(r["_source"], score=r["_score"]) for r in lexical]
        seen, selected = set(), []
        for row in sorted(rows, key=lambda r: r["score"], reverse=True):
            key = row["record_kind"], row["record_id"]
            if key not in seen:
                selected.append(row)
                seen.add(key)
            if len(selected) >= limit:
                break
        return {
            "available": scope["state"] == "ready",
            "backend": "hybrid" if semantic else "lexical",
            "index": scope,
            "records": selected,
        }

    def reconcile(self):
        """A missing index or changed derivation rule is rebuilt from retained projections."""
        exists = self.client.indices.exists(index=self.name)
        with self.engine.begin() as connection:
            rows = (
                connection.execute(
                    select(
                        db.research_runs.c.id,
                        db.research_runs.c.source_run_id,
                        db.research_runs.c.summary,
                        db.research_discovery_indexes.c.generation,
                    )
                    .select_from(db.research_runs.outerjoin(db.research_discovery_indexes))
                    .where(
                        db.research_runs.c.workspace_id == self.workspace_id,
                        db.research_runs.c.id.in_(
                            select(db.research_cards.c.run_id).where(
                                db.research_cards.c.id.in_(current_card_ids(self.workspace_id))
                            )
                        ),
                    )
                )
                .mappings()
                .all()
            )
            for row in rows:
                expected = row["summary"].get("discovery_input_sha256")
                if expected and (
                    not exists
                    or row["generation"]
                    != digest([DISCOVERY_RULE, self.workspace_id, expected, EMBEDDING_IDENTITY])
                ):
                    enqueue_discovery(
                        connection, row["source_run_id"], row["id"], expected, rebuild=True
                    )


class DiscoveryWorker:
    """One bounded projection worker; durable outbox survives cancellation and process exit."""

    def __init__(self, discovery):
        self.discovery = discovery
        self.stop_event = threading.Event()
        self.thread = None
        self.retry_at = {}
        self.reconcile_at = 0

    def start(self):
        self.thread = threading.Thread(
            target=self.loop, name="research-discovery-index", daemon=True
        )
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=10)

    def loop(self):
        try:
            with FileLock(self.discovery.store.root / ".research-discovery.lock", timeout=0):
                while not self.stop_event.is_set():
                    try:
                        self.tick()
                    except Exception:
                        logger.exception("Research discovery index retained for retry")
                    self.stop_event.wait(2)
        except Timeout:
            logger.info("Another process owns research discovery indexing")

    def tick(self):
        if time.monotonic() >= self.reconcile_at:
            self.reconcile_at = time.monotonic() + 60
            self.discovery.reconcile()
        with self.discovery.engine.connect() as connection:
            events = (
                connection.execute(
                    select(db.outbox)
                    .join(db.runs)
                    .where(
                        db.outbox.c.kind == "index_research",
                        db.outbox.c.delivered.is_(False),
                        db.runs.c.result["codex_workspace_id"].astext
                        == self.discovery.workspace_id,
                    )
                    .order_by(db.outbox.c.id)
                    .limit(10)
                )
                .mappings()
                .all()
            )
        for event in events:
            if self.stop_event.is_set():
                return
            if self.retry_at.get(event["id"], 0) > time.monotonic():
                continue
            try:
                self.discovery.index(
                    event["payload"]["research_run_id"], event["payload"]["input_sha256"]
                )
                with self.discovery.engine.begin() as connection:
                    connection.execute(
                        update(db.outbox)
                        .where(db.outbox.c.id == event["id"])
                        .values(delivered=True)
                    )
                self.retry_at.pop(event["id"], None)
            except Exception:
                self.retry_at[event["id"]] = time.monotonic() + 30
                logger.exception("Research discovery event %s retained for retry", event["id"])
