"""Research application operations over native state and library projections."""

from dataclasses import replace

from sqlalchemy import func, select

from hrs.library.books import get_run
from hrs.library.deletion import require_active
from hrs.library.errors import ServiceError
from hrs.library.native_workspace import require_workspace
from hrs.processing.workspace import identity
from hrs.research.bridge import ResearchBridge
from hrs.research.card_notes import CardNotes
from hrs.research.catalogue_query import CatalogueQuery, CatalogueScope
from hrs.research.exporting import export_card
from hrs.research.projection import catalogue_card, located_card
from hrs.research.sources import (
    ResearchEvidence,
    current_card_ids,
    current_external_sources,
    current_source_conditions,
    indexed_version,
    visible_research_run,
)
from hrs.storage import models as db


class Research:
    def __init__(self, settings, engine):
        self.settings, self.engine = settings, engine
        self.bridge = ResearchBridge(settings, engine)
        self.workflow = self.bridge.workflow
        self.catalogue_query = CatalogueQuery(engine, identity(self.bridge.store)["workspace_id"])
        self.evidence = ResearchEvidence(settings, engine)
        from hrs.research.workspace import ResearchWorkspace

        self.workspace = ResearchWorkspace(self)

    def invoke(self, operation, *args, **kwargs):
        try:
            return operation(*args, **kwargs)
        except (KeyError, FileNotFoundError) as error:
            raise ServiceError(404, "研究记录不存在。") from error
        except ValueError as error:
            raise ServiceError(409, str(error)) from error

    def require_run(self, run_id):
        status = self.invoke(self.workflow.status, run_id)
        require_workspace(
            self.engine, get_run(self.engine, status["source_run_id"]), self.bridge.store
        )
        with self.engine.begin() as connection:
            require_active(connection, status["book_id"])
        return status

    def overview(self, book_id, *, include_cards=True, include_entities=True, offset=0, limit=100):
        with self.engine.connect() as connection:
            book = (
                connection.execute(
                    select(db.books).where(
                        db.books.c.id == str(book_id),
                        db.books.c.state.not_in(["deleted", "deleting", "delete_failed"]),
                    )
                )
                .mappings()
                .one_or_none()
            )
            if book is None:
                raise ServiceError(404, "书籍不存在。")
            source_runs = (
                connection.execute(
                    select(db.runs)
                    .where(
                        db.runs.c.book_id == str(book_id),
                        db.runs.c.kind == "book",
                    )
                    .order_by(db.runs.c.created_at.desc())
                )
                .mappings()
                .all()
            )
        version = next((v for run in source_runs if (v := indexed_version(run))), None)
        scope = CatalogueScope(book_id=book_id)
        cards = (
            self.catalogue_query.list_cards(scope, offset=offset, limit=limit)
            if include_cards
            else {"items": [], "total": self.catalogue_query.count_cards(scope)}
        )
        entities = (
            self.catalogue_query.list_entities(scope, offset=offset, limit=limit)
            if include_entities
            else {"items": [], "total": self.catalogue_query.count_entities(scope)}
        )
        stale = self.catalogue_query.count_stale_cards(scope)
        revision_pending = any(
            (run["result"] or {}).get("codex_revision_pending") for run in source_runs
        )
        with self.engine.connect() as connection:
            policy_version = connection.scalar(
                select(func.max(db.research_runs.c.summary["policy"]["version"].as_integer()))
                .select_from(
                    db.research_runs.join(db.runs, db.runs.c.id == db.research_runs.c.source_run_id)
                )
                .where(
                    db.research_runs.c.book_id == str(book_id),
                    db.research_runs.c.workspace_id == identity(self.bridge.store)["workspace_id"],
                    db.research_runs.c.state == "completed",
                    func.coalesce(db.research_runs.c.summary["purpose"].astext, "book") == "book",
                    *current_source_conditions(db.research_runs, db.runs),
                )
            )
        from hrs.research.policy import POLICY

        needs_upgrade = policy_version is not None and policy_version < POLICY["version"]
        return {
            "book_id": str(book_id),
            "book_title": book["title"],
            "runs": self.workflow.list_runs(book_id=str(book_id)),
            "cards": cards["items"],
            "cards_count": cards["total"],
            "entities": entities["items"],
            "entities_count": entities["total"],
            "indexed": version is not None,
            "source_version": version["publication_sha256"] if version else None,
            "stale_cards_count": stale,
            "source_revision_pending": revision_pending,
            "needs_update": revision_pending or bool(stale and not policy_version) or needs_upgrade,
            "needs_upgrade": needs_upgrade,
            "research_policy_version": policy_version,
            "required_research_policy_version": POLICY["version"],
        }

    def list_cards(self, book_id, *, offset=0, limit=100):
        return self.catalogue_query.list_cards(
            CatalogueScope(book_id=book_id), offset=offset, limit=limit
        )

    def list_entities(self, book_id, *, offset=0, limit=100):
        return self.catalogue_query.list_entities(
            CatalogueScope(book_id=book_id), offset=offset, limit=limit
        )

    def card(self, card_id, *, include_relations=True):
        card = self.invoke(self.workflow.card, card_id)
        snapshot = self.workflow.snapshot(card["run_id"])
        with self.engine.connect() as connection:
            source = (
                connection.execute(
                    select(
                        db.runs,
                        current_external_sources(db.research_runs).label("external_current"),
                        db.research_runs.c.summary["created_at"]
                        .as_float()
                        .label("research_created"),
                        db.research_runs.c.summary["purpose"].astext.label("research_purpose"),
                        db.research_runs.c.summary["policy"]["card_only"]
                        .as_boolean()
                        .label("card_only"),
                    )
                    .select_from(
                        db.runs.join(db.books, db.books.c.id == db.runs.c.book_id).outerjoin(
                            db.research_runs, db.research_runs.c.id == card["run_id"]
                        )
                    )
                    .where(
                        db.runs.c.id == card["source_run_id"],
                        db.runs.c.book_id == card["book_id"],
                        db.books.c.state.not_in(["deleted", "deleting", "delete_failed"]),
                    )
                )
                .mappings()
                .one_or_none()
            )
        if source is None:
            raise ServiceError(404, "研究来源已删除或不可用。")
        result = source["result"] or {}
        require_workspace(self.engine, source, self.bridge.store)
        current = (
            result.get("published") is True
            and result.get("content_acceptance") != "workflow_test"
            and snapshot.get("content_verified", True)
            and snapshot["publication_sha256"] == result.get("codex_publication_sha256")
            and snapshot["chapters_sha256"]
            == (result.get("codex_reading_projection") or {}).get("chapters_sha256")
        )
        current = current and bool(source["external_current"])
        revision = bool(result.get("codex_revision_pending"))
        with self.engine.connect() as connection:
            visible = connection.scalar(
                select(visible_research_run(identity(self.bridge.store)["workspace_id"]))
                .select_from(db.books)
                .where(db.books.c.id == card["book_id"])
            )
            newer = None
            if visible not in {None, card["run_id"]}:
                newer = connection.scalar(
                    select(db.research_runs.c.summary["created_at"].as_float()).where(
                        db.research_runs.c.id == visible
                    )
                )
        superseded = bool(
            newer and newer > (source["research_created"] or card.get("created_at", 0))
        )
        purpose = source["research_purpose"] or "book"
        if purpose in {"book", "card_revision"} or card.get("replaces_card_id"):
            with self.engine.connect() as connection:
                lineage_replacement = connection.scalar(
                    select(db.research_cards.c.id)
                    .join(db.research_runs)
                    .where(
                        db.research_cards.c.id.in_(
                            current_card_ids(identity(self.bridge.store)["workspace_id"])
                        ),
                        db.research_cards.c.id != card["id"],
                        func.coalesce(
                            db.research_cards.c.summary["lineage_id"].astext, db.research_cards.c.id
                        )
                        == (card.get("lineage_id") or card["id"]),
                        db.research_runs.c.summary["created_at"].as_float()
                        > (source["research_created"] or card.get("created_at", 0)),
                    )
                    .limit(1)
                )
            superseded |= lineage_replacement is not None
        else:
            superseded = False
        research_current = not superseded
        result = {
            **catalogue_card(
                located_card(card, snapshot, self.workflow.sources(card["run_id"])), self.workflow
            ),
            "source_current": current,
            "needs_update": not current or revision,
            "source_status": "stale"
            if not current
            else "revision_pending"
            if revision
            else "current",
            "research_current": research_current,
            "superseded": superseded,
            "card_only": bool(source["card_only"] or card.get("card_only")),
            "processing_mode": "source_cards"
            if source["card_only"] or card.get("card_only")
            else "legacy_research",
        }
        if include_relations:
            result["derived_relations"] = self.card_relations(card_id)
        notes = CardNotes(self.bridge.store).read(card)
        result.update(
            human_notes=notes["text"],
            notes_version=notes["version"],
            notes_updated_at=notes["updated_at"],
        )
        return result

    def card_notes(self, card_id):
        return CardNotes(self.bridge.store).read(self.card(card_id, include_relations=False))

    def update_card_notes(self, card_id, request):
        card = self.card(card_id, include_relations=False)
        return self.invoke(
            CardNotes(self.bridge.store).save, card, request.expected_version, request.text
        )

    def card_relations(self, card_id):
        result = self.invoke(self.workflow.card_relationships, card_id, limit=100)
        cards, items = {}, []
        for relation in result["items"]:
            if not relation.get("target_card_id"):
                continue
            try:
                owner_id = relation["card_id"]
                if owner_id not in cards:
                    cards[owner_id] = self.card(owner_id, include_relations=False)
                owner = cards[owner_id]
                if owner["state"] not in {"verified", "human_accepted"}:
                    continue
                evidence_ids = set(
                    relation.get("source_evidence_ids", [])
                    + relation.get("target_evidence_ids", [])
                )
                spans = [span for span in owner["evidence"] if span["id"] in evidence_ids]
                target_id = relation.get("target_card_id", relation["target_id"])
                if target_id not in cards:
                    try:
                        cards[target_id] = self.card(target_id, include_relations=False)
                    except ServiceError as error:
                        if error.status_code not in {404, 409}:
                            raise
                        cards[target_id] = None
                target = cards[target_id]
                target_current = (
                    target is not None
                    and target["source_current"]
                    and not target["superseded"]
                    and target["version"] == relation.get("target_card_version", target["version"])
                )
                source_current = owner["source_current"]
                try:
                    self.workspace.current_relation(relation, cards=cards)
                except ServiceError as error:
                    if error.status_code not in {404, 409}:
                        raise
                    source_current = False
                items.append(
                    {
                        **relation,
                        "evidence": spans,
                        "source_current": source_current,
                        "target_current": target_current,
                        "needs_update": not source_current or not target_current,
                    }
                )
            except ServiceError as error:
                if error.status_code not in {404, 409}:
                    raise
        return {**result, "items": items}

    def entity(self, entity_id):
        entity = self.invoke(self.workflow.entity, entity_id)
        cards = {}

        def locate(mention):
            card_id = mention["card_id"]
            card = self.workspace.current_card(card_id, mention.get("card_version"), cards=cards)
            ids = set(mention.get("evidence_ids", []))
            evidence = [span for span in card["evidence"] if span["id"] in ids]
            return {
                **mention,
                "card_version": card["version"],
                "evidence": evidence,
                "card_title": card["title"],
                "reading_target": next(
                    (e["reading_target"] for e in evidence if e["reading_target"]), None
                ),
                "source_current": True,
                "needs_update": card["needs_update"],
                "source_status": card["source_status"],
                "research_current": True,
                "superseded": False,
            }

        def retained(items):
            rows = []
            for item in items:
                try:
                    rows.append(locate(item))
                except ServiceError as error:
                    if error.status_code not in {404, 409}:
                        raise
            return rows

        members = retained(entity.get("source_mentions") or entity.get("mentions") or [entity])
        if not members:
            raise ServiceError(404, "实体的来源已删除、换版或尚未核验。")
        canonical = entity.get("canonical_id") or entity["id"]
        supported = {
            (mention["card_id"], mention["card_version"], span["id"])
            for mention in members
            for span in mention["evidence"]
        }
        aliases, historical = [], []
        for alias in [*entity.get("aliases", []), *entity.get("historical_aliases", [])]:
            refs = alias.get("evidence_refs", [])
            live = [
                ref
                for ref in refs
                if (ref["card_id"], ref["card_version"], ref["evidence_id"]) in supported
            ]
            old = [ref for ref in refs if ref not in live]
            old.extend(ref for ref in alias.get("historical_evidence_refs", []) if ref not in old)
            projected = {
                **alias,
                "evidence_refs": live if live else refs,
                "historical_evidence_refs": old,
                "source_current": bool(live),
            }
            (aliases if live else historical).append(projected)
        withdrawn = [
            {**alias, "source_current": False} for alias in entity.get("withdrawn_aliases", [])
        ]
        result = {
            **entity,
            **members[0],
            "id": canonical,
            "canonical_id": canonical,
            "name": entity["name"],
            "kind": entity["kind"],
            "version": entity.get("version", 1),
            "aliases": aliases,
            "historical_aliases": historical,
            "withdrawn_aliases": withdrawn,
            "canonical": {
                "id": canonical,
                "name": entity["name"],
                "kind": entity["kind"],
                "version": entity.get("version", 1),
                "aliases": aliases,
                "historical_aliases": historical,
                "withdrawn_aliases": withdrawn,
            },
            "mentions": members,
            "source_mentions": members,
            "evidence": [e for mention in members for e in mention["evidence"]],
            "identity_links": retained(entity.get("identity_links", [])),
        }
        relationships = []
        for relation in entity.get("relationships", entity.get("relations", [])):
            try:
                current = self.workspace.current_relation(relation, cards=cards)
                card = cards[relation["card_id"]]
                participant = next(
                    (
                        mention["name"]
                        for mention in card.get("mentions", [])
                        if mention.get("entity_id") == relation.get("target_id")
                    ),
                    "",
                )
                relationships.append(
                    {
                        **current,
                        "participant_name": relation.get("participant_name") or participant,
                    }
                )
            except ServiceError as error:
                if error.status_code not in {404, 409}:
                    raise
        result["relationships"] = relationships
        result["participations"] = [edge for edge in relationships if edge.get("event_id")]
        events = {}
        for edge in result["participations"]:
            event_id = edge["event_id"]
            events.setdefault(
                event_id,
                {
                    "id": event_id,
                    "name": edge.get("event_name", edge.get("name", "")),
                    "date": edge.get("date"),
                    "action": edge.get("action", ""),
                    "place": edge.get("place"),
                    "card_id": edge["card_id"],
                    "event_status": edge.get("event_status", "unknown"),
                    "status_note": edge.get("status_note", ""),
                    "evidence": edge["evidence"],
                    "participants": [],
                },
            )
            events[event_id]["participants"].append(
                {
                    "entity_id": edge.get("target_id"),
                    "name": edge.get("participant_name", ""),
                    "role": edge.get("role", edge.get("predicate", "participant")),
                }
            )
        result["events"] = list(events.values())
        return result

    def start(self, book_id, request):
        if request.revision_of is None:
            raise ServiceError(409, "史料卡会在原文索引完成后自动制作，请查看进度或恢复现有任务。")
        if not request.reason or not request.reason.strip():
            raise ServiceError(400, "更新已有卡片需要说明本次修订原因。")
        return self.invoke(self.bridge.start, book_id, **request.model_dump())

    def control(self, run_id, action):
        self.require_run(run_id)
        status = self.invoke(getattr(self.workflow, action), run_id)
        self.bridge.projection.sync(status)
        return status

    def decide(self, card_id, request):
        card = self.card(card_id)
        if not card["source_current"] or card["superseded"]:
            raise ServiceError(409, "来源已换版，请重新整理研究卡片后再作人工决定。")
        self.require_run(card["run_id"])
        self.invoke(
            self.workflow.decision,
            card_id,
            request.action,
            request.expected_version,
            edited_text=request.text,
        )
        self.bridge.projection.sync(self.workflow.status(card["run_id"]))
        return self.card(card_id)

    def search(self, query, book_id=None, *, book_ids=None, semantic=True, limit=8):
        import logging

        from hrs.research.discovery import ResearchDiscovery

        selected = book_ids if book_ids is not None else [str(book_id)] if book_id else None
        result = self.evidence.search(
            query, book_id, book_ids=book_ids, semantic=semantic, limit=limit
        )
        try:
            discovery = ResearchDiscovery(
                self.settings, self.engine, self.bridge.objects, self.bridge.store
            )
            found = discovery.search(query, book_ids=selected, semantic=semantic, limit=limit)
        except Exception:  # noqa: BLE001 - derived index failure does not hide available originals
            logging.getLogger(__name__).exception(
                "Research discovery unavailable; source results retained"
            )
            found = {
                "available": False,
                "backend": "unavailable",
                "index": {"state": "unavailable"},
                "records": [],
            }
        available = found["backend"] != "unavailable"
        scope = CatalogueScope(
            book_ids=tuple(selected) if selected is not None else None,
            query=None if available else query,
        )
        card_scope = replace(
            scope,
            record_ids=tuple(r["record_id"] for r in found["records"] if r["record_kind"] == "card")
            if available
            else None,
        )
        entity_scope = replace(
            scope,
            record_ids=tuple(
                r["record_id"] for r in found["records"] if r["record_kind"] == "entity"
            )
            if available
            else None,
        )
        cards = self.catalogue_query.list_cards(card_scope, limit=limit)["items"]
        entities = self.catalogue_query.list_entities(entity_scope, limit=limit)["items"]
        indexed_entities = {}
        for entity in entities:
            canonical = entity.get("canonical_id") or entity["id"]
            indexed_entities.setdefault(canonical, {**entity, "id": canonical})
        ranks = {row["record_id"]: index for index, row in enumerate(found["records"])}
        cards.sort(key=lambda row: ranks.get(row["id"], len(ranks)))
        ranked_entities = sorted(
            indexed_entities.values(), key=lambda row: ranks.get(row["id"], len(ranks))
        )
        return {
            **result,
            "cards": cards,
            "entities": ranked_entities,
            "research_backend": found["backend"] if available else "sql_literal_fallback",
            "research_available": found["available"],
            "research_index": found["index"],
        }

    def export(self, card_id, *, base_url=""):
        card = self.card(card_id)
        snapshot = self.workflow.snapshot(card["run_id"])
        articles = {
            article["id"]: article
            for article in [*snapshot["articles"], *self.workflow.sources(card["run_id"])]
        }
        return export_card(card, snapshot, articles, base_url=base_url)
