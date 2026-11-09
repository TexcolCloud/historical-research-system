"""Retain content-addressed research evidence and queryable SQL read projections."""

import copy
import json

from sqlalchemy import delete, func, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from hrs.library.deletion import require_active
from hrs.library.reading_locations import reading_target
from hrs.processing.hashing import digest
from hrs.storage import models as db

PROJECTION_POLICY = 4


def reading_identity(card, article=None):
    """Compact reader labels, kept separate from authoritative source/card identities."""
    dossier = card.get("dossier") or {}
    citation = dossier.get("citation") or {}
    metadata = card.get("metadata") or {}
    parts = (article or {}).get("parts", [])
    pages = (
        sorted({page for part in parts for page in part.get("source", {}).get("pages", [])})
        or (article or {}).get("pages", [])
        or card.get("pages", [])
    )
    return {
        "display_id": "CARD-" + card["id"][:12].upper() if card.get("id") else "",
        "authors": (metadata.get("authors") or {}).get("value", ""),
        "formation_date": card.get("formation_date") or {"certainty": "unknown"},
        "publication_date": citation.get("publication_date") or {"certainty": "unknown"},
        "pages": pages,
        "printed_pages": (citation.get("printed_pages") or {}).get("value", ""),
        "source_citation": (citation.get("full") or {}).get("value")
        or (metadata.get("publication") or {}).get("value", ""),
        "citation_completeness": citation.get("completeness", "unknown"),
        "dossier_available": bool(dossier),
    }


def located_card(card, snapshot, external_sources=()):
    card = copy.deepcopy(card)
    articles = {article["id"]: article for article in [*snapshot["articles"], *external_sources]}
    card.update(reading_identity(card, articles.get(card.get("article_id"))))
    for evidence in card.get("evidence", []):
        article = articles[evidence["article_id"]]
        chapter = {
            **article,
            "id": article.get("original_article_id", article["id"]),
            "run_id": article.get("source_run_id", snapshot["source_run_id"]),
        }
        evidence["reading_target"] = reading_target(chapter, evidence["start"], evidence["end"])
        evidence["text_sha256"] = article["text_sha256"]
        evidence.update(
            source_run_id=chapter["run_id"],
            book_id=article.get("book_id") or evidence.get("book_id") or snapshot.get("book_id"),
            original_article_id=chapter["id"],
            publication_sha256=article.get("publication_sha256")
            or evidence.get("publication_sha256")
            or snapshot.get("publication_sha256"),
            content_sha256=(article.get("content") or {}).get(
                "sha256", evidence.get("content_sha256")
            ),
        )
        for note in evidence.get("footnotes", []):
            start, end = note["note"]["start"], note["note"]["end"]
            note["text"] = article["text"][start:end]
            note["reading_target"] = reading_target(chapter, start, end)
        for part in evidence.get("sources", []):
            part.pop("text", None)
    card["book_title"] = snapshot.get("book_title", "")
    for mention in [*card.get("mentions", []), *card.get("events", [])]:
        mention["entity_id"] = digest([card["id"], mention["id"]])[:32]
    return card


def catalogue_card(card, workflow):
    """Resolve only this card's indexed mentions without loading all book identities."""
    from hrs.research.catalogue import ResearchCatalogue

    mentions = [*card.get("mentions", []), *card.get("events", [])]
    mapped = ResearchCatalogue(workflow.ledger).annotate_mentions(
        [{"id": mention["entity_id"]} for mention in mentions]
    )
    for mention, identity in zip(mentions, mapped, strict=True):
        mention["source_mention_id"] = mention["entity_id"]
        if identity.get("canonical_id"):
            mention["entity_id"] = identity["canonical_id"]
            mention["canonical_id"] = identity["canonical_id"]
    return card


class ResearchProjection:
    def __init__(self, engine, objects, workflow):
        self.engine, self.objects, self.workflow = engine, objects, workflow
        self.snapshot_refs, self.external_refs = {}, {}
        self.card_refs, self.entity_refs = {}, {}

    def put(self, value, source_run_id):
        return self.objects.put_bytes(
            json.dumps(value, ensure_ascii=False, sort_keys=True).encode(), run_id=source_run_id
        )

    def retained(self, cache, key, value, source_run_id):
        if key not in cache:
            cache[key] = self.put(value, source_run_id)
        return cache[key]

    def sync(self, status, *, force=False):
        run_id = status["run_id"]
        snapshot = self.workflow.snapshot(run_id)
        external = self.workflow.sources(run_id)
        cards = [
            catalogue_card(located_card(card, snapshot, external), self.workflow)
            for card in self.workflow.list_cards(run_id=run_id)
        ]
        entities = self.workflow.list_entities(run_id=run_id)
        by_card = {card["id"]: card for card in cards}
        for entity in entities:
            card = by_card.get(entity.get("card_id"))
            if card:
                ids = set(entity.get("evidence_ids", []))
                entity["evidence"] = [e for e in card["evidence"] if e["id"] in ids]
                entity["reading_target"] = next(
                    (e["reading_target"] for e in entity["evidence"] if e["reading_target"]), None
                )
        snapshot_key = (snapshot["source_run_id"], status["input_hash"])
        if snapshot_key not in self.snapshot_refs:
            self.snapshot_refs[snapshot_key] = self.put(snapshot, snapshot["source_run_id"])
        source_refs = []
        for article in external:
            source_key = (snapshot["source_run_id"], article["id"], article["text_sha256"])
            if source_key not in self.external_refs:
                self.external_refs[source_key] = self.put(article, snapshot["source_run_id"])
            source_refs.append(self.external_refs[source_key])
        run_ref = self.put(
            {
                "status": status,
                "snapshot": self.snapshot_refs[snapshot_key],
                "external_sources": source_refs,
            },
            snapshot["source_run_id"],
        )
        cited = {span["article_id"] for card in cards for span in card["evidence"]}
        source_versions = [
            {
                "book_id": article["book_id"],
                "source_run_id": article["source_run_id"],
                "article_id": article["original_article_id"],
                "publication_sha256": article["publication_sha256"],
                "content_sha256": (article.get("content") or {}).get("sha256"),
            }
            for article in external
            if article["id"] in cited
        ]
        card_rows = [
            {
                "id": card["id"],
                "run_id": run_id,
                "book_id": snapshot["book_id"],
                "title": card["title"],
                "kind": card.get("document_type") or "research",
                "state": card.get("state", "draft"),
                "version": card.get("version", 1),
                "search_text": json.dumps(card, ensure_ascii=False),
                "summary": card_summary(card),
                "content": self.retained(
                    self.card_refs,
                    (run_id, card["id"], card["version"], digest(card)),
                    card,
                    snapshot["source_run_id"],
                ),
            }
            for card in cards
        ]
        entity_rows = [
            {
                "id": entity["id"],
                "run_id": run_id,
                "book_id": snapshot["book_id"],
                "name": entity["name"],
                "kind": entity["kind"],
                "search_text": json.dumps(entity, ensure_ascii=False),
                "summary": {
                    key: value
                    for key, value in entity.items()
                    if key not in {"evidence", "relations", "mentions", "identity_links"}
                },
                "content": self.retained(
                    self.entity_refs,
                    (
                        run_id,
                        entity["id"],
                        entity.get("state"),
                        by_card[entity["card_id"]]["version"],
                        digest(entity),
                    ),
                    entity,
                    snapshot["source_run_id"],
                ),
            }
            for entity in entities
        ]
        discovery_input = digest(
            [
                [(row["id"], row["content"]["sha256"]) for row in card_rows],
                [(row["id"], row["content"]["sha256"]) for row in entity_rows],
            ]
        )
        with self.engine.begin() as connection:
            require_active(connection, snapshot["book_id"])
            previous = (
                connection.execute(
                    select(
                        db.research_runs.c.version,
                        db.research_runs.c.summary["projection_policy"]
                        .as_integer()
                        .label("policy"),
                        db.research_runs.c.summary["discovery_input_sha256"].astext.label(
                            "discovery_input"
                        ),
                    )
                    .where(db.research_runs.c.id == run_id)
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if previous and (
                previous["version"] > status["version"]
                or previous["version"] == status["version"]
                and previous["policy"] == PROJECTION_POLICY
                and previous["discovery_input"] == discovery_input
                and not force
            ):
                return
            artifacts_changed = False
            for table, rows in (
                (db.research_cards, card_rows),
                (db.research_entities, entity_rows),
            ):
                retained = dict(
                    connection.execute(
                        select(table.c.id, table.c.content["sha256"].astext).where(
                            table.c.run_id == run_id
                        )
                    ).all()
                )
                artifacts_changed |= retained != {
                    row["id"]: row["content"]["sha256"] for row in rows
                }
            row = {
                "id": run_id,
                "book_id": snapshot["book_id"],
                "source_run_id": snapshot["source_run_id"],
                "workspace_id": snapshot["workspace_id"],
                "state": status["state"],
                "version": status["version"],
                "input_hash": status["input_hash"],
                "summary": {
                    **status,
                    "chapters_sha256": snapshot["chapters_sha256"],
                    "external_source_versions": source_versions,
                    "projection_policy": PROJECTION_POLICY,
                    "discovery_input_sha256": discovery_input,
                },
                "content": run_ref,
                "updated_at": func.now(),
            }
            if not self.upsert(connection, db.research_runs, row):
                return
            for table, rows in (
                (db.research_cards, card_rows),
                (db.research_entities, entity_rows),
            ):
                connection.execute(
                    delete(table).where(
                        table.c.run_id == run_id, table.c.id.not_in([row["id"] for row in rows])
                    )
                )
                for row in rows:
                    self.upsert(connection, table, row)
            from hrs.research.discovery import enqueue_discovery

            enqueue_discovery(connection, snapshot["source_run_id"], run_id, discovery_input)
            if previous is None or previous["version"] < status["version"] or artifacts_changed:
                connection.execute(
                    insert(db.events).values(
                        book_id=snapshot["book_id"],
                        run_id=snapshot["source_run_id"],
                        kind="research.changed",
                        payload={
                            "research_run_id": run_id,
                            "state": status["state"],
                            "version": status["version"],
                            "counts": status["counts"],
                            "artifacts_changed": artifacts_changed,
                        },
                    )
                )

    @staticmethod
    def upsert(connection, table, row):
        statement = pg_insert(table).values(**row)
        result = connection.execute(
            statement.on_conflict_do_update(
                index_elements=[table.c.id],
                set_={key: value for key, value in row.items() if key != "id"},
                **(
                    {"where": table.c.version <= statement.excluded.version}
                    if table is db.research_runs
                    else {}
                ),
            ).returning(table.c.id)
        )
        return result.scalar_one_or_none() is not None

    def versions(self):
        with self.engine.connect() as connection:
            return dict(
                connection.execute(
                    select(db.research_runs.c.id, db.research_runs.c.version).where(
                        db.research_runs.c.summary["projection_policy"].as_integer()
                        == PROJECTION_POLICY
                    )
                ).all()
            )


def card_summary(card):
    fields = (
        "id",
        "book_id",
        "run_id",
        "article_id",
        "title",
        "summary",
        "state",
        "version",
        "approval_type",
        "tags",
        "document_type",
        "source_layer",
        "article_title",
        "replaces_card_id",
        "lineage_id",
    )
    return {
        **{key: card.get(key) for key in fields},
        **reading_identity(card),
        "card_only": bool(card.get("card_only")),
        "processing_mode": "source_cards" if card.get("card_only") else "legacy_research",
        "evidence_count": len(card.get("evidence", [])),
        "item_count": len(card.get("items", [])),
        "entity_count": len(card.get("mentions", [])) + len(card.get("events", [])),
        "event_count": len(card.get("events", [])),
        "relation_count": len(card.get("relations", [])),
        "cross_relation_count": len(card.get("cross_relations", [])),
        "coverage_count": len(card.get("coverage", [])),
        "reading_target": next(
            (
                span["reading_target"]
                for span in card.get("evidence", [])
                if span.get("reading_target")
            ),
            None,
        ),
    }
