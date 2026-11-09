"""Source-authorized research workspace operations behind the HTTP boundary."""

from copy import deepcopy
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from hrs.library.catalog import Library
from hrs.library.errors import ServiceError
from hrs.processing.hashing import digest
from hrs.research.models import CardDraft, CardOnlyCardDraft, DossierCardDraft
from hrs.research.notebook import ResearchNotebook
from hrs.storage import models as db


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IdentityDecision(Request):
    action: Literal["merge", "distinct", "split", "alias", "alias_remove"]
    expected_version: int = Field(ge=1)
    target_id: str | None = None
    target_version: int | None = Field(default=None, ge=1)
    mention_ids: list[str] | None = None
    alias: str | None = Field(default=None, max_length=200)
    evidence_refs: list[dict] = Field(default_factory=list, max_length=100)
    reason: str = Field(default="", max_length=2000)


class CardRevision(Request):
    expected_version: int = Field(ge=1)
    card: CardDraft | CardOnlyCardDraft | DossierCardDraft
    reason: str = Field(default="", max_length=2000)


class QuestionCreate(Request):
    title: str = Field(min_length=1, max_length=2000)
    book_ids: list[str] = Field(default_factory=list, max_length=100)
    notes: str = Field(default="", max_length=60000)


class QuestionUpdate(Request):
    expected_version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=2000)
    book_ids: list[str] | None = Field(default=None, max_length=100)
    notes: str | None = Field(default=None, max_length=60000)
    status: Literal["open", "resolved", "archived"] | None = None


class QuestionEvidence(Request):
    expected_version: int = Field(ge=1)
    kind: Literal["source", "card", "entity", "relation"]
    target_id: str = Field(min_length=1)
    decision: Literal["saved", "adopted", "excluded"] = "saved"
    note: str = Field(default="", max_length=60000)
    start: int = Field(default=0, ge=0)
    end: int | None = Field(default=None, gt=0)
    text_sha256: str | None = None
    evidence_ids: list[str] | None = Field(default=None, max_length=100)


class SelectionUpdate(Request):
    expected_version: int = Field(ge=1)
    decision: Literal["saved", "adopted", "excluded"] | None = None
    note: str | None = Field(default=None, max_length=60000)


class Version(Request):
    expected_version: int = Field(ge=1)


class ResearchWorkspace:
    def __init__(self, research):
        self.research = research

    @property
    def notebook(self):
        return ResearchNotebook(self.research.bridge.store)

    def books(self, book_ids):
        try:
            ids = list(dict.fromkeys(str(UUID(str(value))) for value in book_ids))
        except ValueError as error:
            raise ServiceError(400, "研究范围包含无效书籍ID。") from error
        if len(ids) > 100:
            raise ServiceError(400, "研究范围最多选择100本书。")
        for identity in ids:
            self.research.bridge.source_run(identity)
        return ids

    def current_card(self, card_id, version=None, *, cards=None):
        if cards is not None and card_id in cards:
            card = cards[card_id]
        else:
            card = self.research.card(card_id, include_relations=False)
            if cards is not None:
                cards[card_id] = card
        if card is None:
            raise ServiceError(404, "研究依据不可用。")
        if (
            not card["source_current"]
            or card["superseded"]
            or card["state"] not in {"verified", "human_accepted"}
            or version is not None
            and card["version"] != version
        ):
            raise ServiceError(409, "研究依据已变化或尚未核验，请重新读取。")
        return card

    def evidence_refs(self, refs, cards=None):
        cards = cards if cards is not None else {}
        result = []
        for ref in refs:
            card = self.current_card(ref["card_id"], ref.get("card_version"), cards=cards)
            span = next((e for e in card["evidence"] if e["id"] == ref["evidence_id"]), None)
            if span is None:
                raise ServiceError(409, "关联依据不属于指定卡片。")
            result.append({**ref, "card_version": card["version"], "evidence": span})
        return result

    def current_relation(self, relation, *, cards=None):
        """Authorize the relation's own immutable proof, not just its graph endpoints."""
        cards = cards if cards is not None else {}
        owner = self.current_card(relation["card_id"], relation.get("card_version"), cards=cards)
        refs = relation.get("evidence_refs", [])
        if refs:
            refs = self.evidence_refs(refs, cards)
            evidence = [ref["evidence"] for ref in refs]
        else:
            identities = set(
                relation.get("evidence_ids", [])
                + relation.get("source_evidence_ids", [])
                + relation.get("target_evidence_ids", [])
            )
            evidence = [span for span in owner["evidence"] if span["id"] in identities]
            if len(evidence) != len(identities):
                raise ServiceError(409, "关系依据不属于当前卡片版本。")
        if not evidence:
            raise ServiceError(409, "研究关系尚无可核验的原文依据。")
        if relation.get("target_card_id"):
            target = self.current_card(
                relation["target_card_id"], relation.get("target_card_version"), cards=cards
            )
            if relation.get("target_publication_sha256") not in {
                None,
                target.get("publication_sha256"),
            }:
                raise ServiceError(409, "关系对方的发布版本已变化。")
            source_id = relation.get("source_id", owner["id"])
            if source_id != owner["id"]:
                self.current_card(source_id, cards=cards)
        return {
            **relation,
            "evidence_refs": refs,
            "evidence": evidence,
            "source_current": True,
            "target_current": True,
            "needs_update": False,
        }

    def identity_decision(self, entity_id, request):
        entity = self.research.entity(entity_id)
        target = self.research.entity(request.target_id) if request.target_id else None
        values = request.model_dump(exclude={"action", "expected_version"})
        values["reason"] = request.reason or f"用户在本地依据所选原文作出{request.action}身份决定"
        refs = request.evidence_refs or [
            {
                "card_id": mention["card_id"],
                "card_version": self.current_card(mention["card_id"])["version"],
                "evidence_id": evidence["id"],
            }
            for owner in [entity, *([target] if target else [])]
            for mention in owner.get("source_mentions", owner.get("mentions", [owner]))[:1]
            for evidence in mention.get("evidence", [])[:1]
        ]
        values["evidence_refs"] = [
            {key: ref[key] for key in ("card_id", "card_version", "evidence_id")}
            for ref in self.evidence_refs(refs)
        ]
        self.research.invoke(
            self.research.workflow.identity_decision,
            entity_id,
            request.action,
            request.expected_version,
            **values,
        )
        affected = {
            m["run_id"]
            for e in [entity, *([target] if target else [])]
            for m in e.get("source_mentions", e.get("mentions", [e]))
        }
        for run_id in affected:
            self.research.bridge.projection.sync(self.research.workflow.status(run_id), force=True)
        return self.research.entity(entity_id)

    def card_revision(self, card_id, request):
        card = self.research.card(card_id)
        if not card["source_current"] or card["superseded"]:
            raise ServiceError(409, "旧来源卡片不能覆盖当前版本，请先更新研究。")
        status = self.research.invoke(
            self.research.workflow.card_revision,
            card_id,
            request.expected_version,
            request.card.model_dump(),
            reason=request.reason or "用户提交结构化卡片修订，等待独立核验",
        )
        self.research.bridge.projection.sync(status)
        return status

    def pending(self, *, book_id=None, kind=None, offset=0, limit=50):
        result = self.research.invoke(
            self.research.workflow.pending_items, book_id=book_id, limit=500
        )
        cache, items = {}, []
        for item in result["items"]:
            if item.get("kind") == "relationship":
                item = {**item, "kind": "relation"}
            if kind and item.get("kind") != kind:
                continue
            if item.get("card_id"):
                try:
                    if item["card_id"] not in cache:
                        cache[item["card_id"]] = self.research.card(item["card_id"])
                except ServiceError as error:
                    if error.status_code == 404:
                        continue
                    raise
                card = cache[item["card_id"]]
                if not card["source_current"] or card["superseded"]:
                    continue
            else:
                try:
                    self.research.require_run(item["run_id"])
                except ServiceError as error:
                    if error.status_code == 404:
                        continue
                    raise
            items.append(item)
        return {
            "items": items[offset : offset + limit],
            "total": len(items),
            "offset": offset,
            "limit": limit,
            "truncated": result.get("truncated", False),
        }

    def reading(self, chapter_id, *, expected_sha256=None):
        library = Library(self.research.settings, self.research.engine)
        library.outputs.objects = self.research.bridge.objects
        chapter = library.chapter(chapter_id)
        self.research.evidence.require_workspace(
            {"id": chapter["run_id"], **self._run(chapter["run_id"])}
        )
        if expected_sha256 and expected_sha256 != chapter["text_sha256"]:
            raise ServiceError(409, "阅读原文已换版，请重新读取。")
        result = self.research.invoke(
            self.research.workflow.reading_annotations,
            str(chapter_id),
            chapter["text_sha256"],
            limit=200,
        )
        cards, annotations = {}, []
        for item in result["annotations"]:
            try:
                if item["card_id"] not in cards:
                    cards[item["card_id"]] = self.current_card(
                        item["card_id"], item.get("card_version")
                    )
            except ServiceError as error:
                if error.status_code in {404, 409}:
                    continue
                raise
            start, end = item["start"], item["end"]
            if not 0 <= start < end <= len(chapter["text"]):
                continue
            quote = chapter["text"][start:end]
            if quote != item.get("quote", item["name"]):
                continue
            annotations.append(
                {
                    **item,
                    "quote": quote,
                    "entity_id": item.get("canonical_id", item["entity_id"]),
                    "text_sha256": chapter["text_sha256"],
                    "source_current": True,
                }
            )
        from hrs.research.projection import card_summary

        for summary in result.get("cards", []):
            try:
                if summary["id"] not in cards:
                    cards[summary["id"]] = self.current_card(summary["id"])
            except ServiceError as error:
                if error.status_code not in {404, 409}:
                    raise
        return {
            "chapter_id": str(chapter_id),
            "article_id": str(chapter_id),
            "run_id": str(chapter["run_id"]),
            "text_sha256": chapter["text_sha256"],
            "source_current": True,
            "annotations": annotations,
            "cards": [card_summary(card) for card in cards.values()],
            "truncated": result.get("truncated", False),
        }

    def _run(self, run_id):
        with self.research.engine.connect() as connection:
            row = (
                connection.execute(select(db.runs).where(db.runs.c.id == run_id))
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise ServiceError(404, "原文版本不存在。")
        return dict(row)

    def graph(self, entity_id, *, hops=1, limit=50):
        root = self.research.entity(entity_id)
        result = self.research.invoke(
            self.research.workflow.graph, entity_id, hops=hops, limit=limit
        )
        cards, nodes = {}, []
        for node in result["nodes"]:
            try:
                if node.get("kind") == "card":
                    card_id = node.get("card_id", node["id"])
                    card = self.current_card(card_id, cards=cards)
                    node = {
                        **node,
                        "card_id": card_id,
                        "book_id": card["book_id"],
                        "name": card["title"],
                    }
                else:
                    identity = node.get("canonical_id") or node["id"]
                    node = root if identity == root["id"] else self.research.entity(identity)
            except ServiceError as error:
                if error.status_code in {404, 409}:
                    continue
                raise
            nodes.append(node)
        ids = {node["id"] for node in nodes}
        edges = []
        for edge in result["edges"]:
            if edge["source_id"] not in ids or edge["target_id"] not in ids:
                continue
            try:
                edges.append(self.current_relation(edge, cards=cards))
            except ServiceError as error:
                if error.status_code not in {404, 409}:
                    raise
        return {**result, "nodes": nodes, "edges": edges}

    def timeline(self, *, entity_id=None, book_id=None, limit=100):
        if entity_id:
            self.research.entity(entity_id)
        result = self.research.invoke(
            self.research.workflow.timeline, entity_id=entity_id, book_id=book_id, limit=limit
        )
        items = []
        for item in result["items"]:
            try:
                card = self.current_card(item["card_id"], item.get("card_version"))
            except ServiceError as error:
                if error.status_code in {404, 409}:
                    continue
                raise
            evidence = [e for e in card["evidence"] if e["id"] in item.get("evidence_ids", [])]
            items.append({**item, "evidence": evidence})
        return {**result, "items": items}

    def questions(self, **options):
        result = self.research.invoke(self.notebook.questions, **options)
        counts = self.notebook.summary_counts([item["id"] for item in result["items"]])
        return {**result, "items": [{**item, **counts[item["id"]]} for item in result["items"]]}

    def catalogue(self, *, book_id=None, book_ids=None, query=None, kind=None, offset=0, limit=50):
        selected = list(map(str, book_ids)) if book_ids is not None else None
        result = self.research.invoke(
            self.research.workflow.catalogue_entities,
            book_id=str(book_id) if book_id else None,
            book_ids=selected,
            query=query,
            kind=kind,
            offset=offset,
            limit=limit,
        )
        items = []
        for entry in result["items"]:
            try:
                entity = self.research.entity(entry["id"])
            except ServiceError as error:
                if error.status_code == 404:
                    continue
                raise
            items.append(
                {
                    key: value
                    for key, value in entity.items()
                    if key
                    not in {
                        "evidence",
                        "source_mentions",
                        "mentions",
                        "relationships",
                        "participations",
                        "events",
                    }
                }
            )
        return {**result, "items": items}

    def question(self, question_id):
        result = self.research.invoke(self.notebook.question, question_id)
        # Retain prior snapshots while preventing a stale quote from linking to new text.
        result = deepcopy(result)
        run_ids = [run["run_id"] for run in result.get("runs", [])]
        if run_ids:
            from hrs.processing.workspace import identity

            with self.research.engine.connect() as connection:
                books = dict(
                    connection.execute(
                        select(db.research_runs.c.id, db.research_runs.c.book_id)
                        .join(db.books)
                        .where(
                            db.research_runs.c.id.in_(run_ids),
                            db.research_runs.c.workspace_id
                            == identity(self.research.bridge.store)["workspace_id"],
                            db.books.c.state.not_in(["deleted", "deleting", "delete_failed"]),
                        )
                    ).all()
                )
            for run in result["runs"]:
                if run["run_id"] in books:
                    run["book_id"] = str(books[run["run_id"]])
        metadata, cards = {}, {}
        for selection in result.get("selections", []):
            if selection["kind"] != "source":
                selection["record_current"] = self._record_current(selection, cards)
        for item in [*result.get("selections", []), *result.get("findings", [])]:
            owner_current = True
            if item.get("card_id"):
                if item["card_id"] not in cards:
                    try:
                        cards[item["card_id"]] = self.research.card(
                            item["card_id"], include_relations=False
                        )
                    except ServiceError as error:
                        if error.status_code not in {404, 409}:
                            raise
                        cards[item["card_id"]] = None
                owner = cards[item["card_id"]]
                owner_current = bool(
                    owner
                    and owner["source_current"]
                    and not owner["superseded"]
                    and owner["version"] == item.get("card_version", owner["version"])
                )
                item["card_current"] = item.get("card_current", True) and owner_current
            for span in item.get("evidence", []):
                target = span.get("reading_target") or {}
                chapter_id = target.get("chapter_id") or span.get("article_id")
                if chapter_id not in metadata:
                    metadata[chapter_id] = self._source_metadata(chapter_id) if chapter_id else None
                current = metadata[chapter_id]
                matches = bool(
                    current
                    and all(
                        span.get(key) == current[key]
                        for key in ("source_run_id", "publication_sha256", "content_sha256")
                    )
                )
                span["source_current"] = matches
                span["available"] = current is not None
                if not matches:
                    span.update(retained_reading_target=target, reading_target=None)
        return result

    def _record_current(self, selection, cards):
        reference, versions = selection.get("record_ref"), selection.get("version_refs")
        if not reference or not versions:
            return False  # Old selections preserve notes but have no frozen derived identity.
        try:
            for version in versions:
                self.current_card(version["card_id"], version["card_version"], cards=cards)
            identity = reference["id"]
            if reference["kind"] == "card":
                record = self.current_card(identity, reference.get("version"), cards=cards)
            elif reference["kind"] == "entity":
                record = self.research.entity(identity)
            else:
                record = self.research.invoke(self.research.workflow.relation, identity)
                self.current_relation(record, cards=cards)
            return (
                record["id"] == identity
                and ("version" not in reference or record.get("version") == reference["version"])
                and ("sha256" not in reference or digest(record) == reference["sha256"])
            )
        except ServiceError as error:
            if error.status_code not in {404, 409}:
                raise
            return False

    def _source_metadata(self, chapter_id):
        from hrs.library.native_workspace import workspace_runs

        try:
            chapter_id = str(UUID(str(chapter_id)))
        except ValueError:
            return None
        with self.research.engine.connect() as connection:
            row = (
                connection.execute(
                    select(db.chapters.c.content, db.runs.c.id, db.runs.c.result)
                    .join(db.runs, db.runs.c.id == db.chapters.c.run_id)
                    .join(db.books, db.books.c.id == db.chapters.c.book_id)
                    .where(
                        db.chapters.c.id == chapter_id,
                        db.books.c.state.not_in(["deleted", "deleting", "delete_failed"]),
                        workspace_runs(self.research.bridge.store),
                    )
                )
                .mappings()
                .one_or_none()
            )
        if (
            row is None
            or (row["result"] or {}).get("published") is not True
            or (row["result"] or {}).get("content_acceptance") == "workflow_test"
        ):
            return None
        return {
            "source_run_id": str(row["id"]),
            "publication_sha256": row["result"].get("codex_publication_sha256"),
            "content_sha256": row["content"]["sha256"],
        }

    def _snapshot_evidence(self, spans):
        result, metadata = [], {}
        for original in spans:
            span = deepcopy(original)
            target = span.get("reading_target") or {}
            article_id = target.get("chapter_id") or span["article_id"]
            if article_id not in metadata:
                metadata[article_id] = self._source_metadata(article_id)
            current = metadata[article_id]
            if current is None:
                raise ServiceError(409, "证据来源已删除或未发布。")
            if any(
                span.get(key) is not None and span[key] != current[key]
                for key in ("source_run_id", "publication_sha256", "content_sha256")
            ):
                raise ServiceError(409, "证据来源在选材期间已变化，请重新读取。")
            result.append({**span, **current, "source_current": True})
        return result

    def create_question(self, request):
        created = self.research.invoke(
            self.notebook.create, request.title, self.books(request.book_ids), notes=request.notes
        )
        return self.question(created["id"])

    def update_question(self, question_id, request):
        values = request.model_dump(exclude={"expected_version"}, exclude_none=True)
        if "book_ids" in values:
            values["book_ids"] = self.books(values["book_ids"])
        self.research.invoke(self.notebook.update, question_id, request.expected_version, **values)
        return self.question(question_id)

    def select_evidence(self, question_id, request):
        self.research.invoke(self.notebook.question, question_id)
        cards, binding, target_id = {}, {}, request.target_id
        if request.kind == "source":
            if request.end is None or not request.text_sha256:
                raise ServiceError(400, "选择原文需要完整范围及版本哈希。")
            source = self.research.evidence.read_source(
                request.target_id,
                request.start,
                request.end,
                expected_sha256=request.text_sha256,
                max_chars=60000,
            )
            quote = source.pop("text")
            evidence = [{**source, "quote": quote}]
            title = "原文摘录"
        elif request.kind == "card":
            card = self.current_card(request.target_id, cards=cards)
            ids = request.evidence_ids
            evidence = [e for e in card["evidence"] if ids is None or e["id"] in ids]
            if ids and len(evidence) != len(set(ids)):
                raise ServiceError(409, "所选证据不属于当前卡片。")
            title = card["title"]
            binding["record_ref"] = {"kind": "card", "id": card["id"], "version": card["version"]}
        elif request.kind == "entity":
            entity = self.research.entity(request.target_id)
            evidence = entity["evidence"]
            title = entity["name"]
            target_id = entity["id"]
            binding["record_ref"] = {
                "kind": "entity",
                "id": target_id,
                "version": entity["version"],
            }
            for mention in entity["source_mentions"]:
                self.current_card(mention["card_id"], mention["card_version"], cards=cards)
        else:
            relation = self.research.invoke(self.research.workflow.relation, request.target_id)
            evidence = self.current_relation(relation, cards=cards)["evidence"]
            title = relation.get("predicate") or relation.get("kind", "研究关系")
            binding["record_ref"] = {
                "kind": "relation",
                "id": relation["id"],
                "sha256": digest(relation),
            }
        if binding:
            binding["version_refs"] = [
                {"card_id": identity, "card_version": card["version"]}
                for identity, card in sorted(cards.items())
            ]
        self.research.invoke(
            self.notebook.select,
            question_id,
            request.expected_version,
            {
                "kind": request.kind,
                "target_id": target_id,
                "title": title,
                "decision": request.decision,
                "note": request.note,
                "evidence": self._snapshot_evidence(evidence),
                **binding,
            },
            trusted_evidence=True,
        )
        return self.question(question_id)

    def update_selection(self, question_id, selection_id, request):
        self.research.invoke(
            self.notebook.set_selection,
            question_id,
            selection_id,
            request.expected_version,
            **request.model_dump(exclude={"expected_version"}, exclude_none=True),
        )
        return self.question(question_id)

    def start_question(self, question_id, request):
        raise ServiceError(410, "研究问题自动查证已停用；史料卡在原文索引完成后自动制作。")

    def available_books(self):
        """Freeze the current workspace's available corpus before the shared retrieval call."""
        from hrs.library.native_workspace import workspace_runs
        from hrs.research.sources import indexed_version

        with self.research.engine.connect() as connection:
            sources = (
                connection.execute(
                    select(db.runs.c.book_id, db.runs.c.result)
                    .join(db.books)
                    .where(
                        db.runs.c.kind == "book",
                        workspace_runs(self.research.bridge.store),
                        db.books.c.state.not_in(["deleted", "deleting", "delete_failed"]),
                    )
                )
                .mappings()
                .all()
            )
        return sorted({str(source["book_id"]) for source in sources if indexed_version(source)})
