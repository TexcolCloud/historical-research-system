"""Application service for native research and its SQL read projections."""

from typing import Literal
from urllib.parse import urlencode

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import and_, func, not_, or_, select

from hrs.api.schemas import FootnoteLink, ReadingTarget, SearchHit
from hrs.library.books import get_run
from hrs.library.deletion import require_active
from hrs.library.errors import ServiceError
from hrs.library.footnotes import export_footnotes, resolve_footnotes
from hrs.library.native_workspace import require_workspace
from hrs.processing.workspace import identity
from hrs.research.bridge import ResearchBridge
from hrs.research.card_notes import CardNotes
from hrs.research.models import (
    CardDossier,
    CardDraft,
    CardOnlyCardDraft,
    CardOnlyVerificationSubmission,
    DossierCardDraft,
    DossierVerificationSubmission,
    EntityMention,
    EventDraft,
    EvidenceSpan,
    IdentityLink,
    ResearchDate,
    SourceValue,
    VerificationSubmission,
)
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


class StartResearch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision_of: str | None = None
    reason: str | None = Field(default=None, max_length=2000)


class ResearchDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["accept", "edit", "reject"]
    expected_version: int = Field(ge=1)
    text: str | None = Field(default=None, max_length=60000)

    @model_validator(mode="after")
    def edit_has_text(self):
        if self.action == "edit" and (not self.text or not self.text.strip()):
            raise ValueError("编辑决定需要修订后的提要。")
        if self.action != "edit" and self.text is not None:
            raise ValueError("只有编辑决定可以提供修订提要。")
        return self


class ResearchFootnoteResponse(FootnoteLink):
    text: str
    reading_target: ReadingTarget | None = None


class ResearchEvidenceResponse(EvidenceSpan):
    model_config = ConfigDict(extra="allow")
    reading_target: ReadingTarget | None = None
    pages: list[int] = Field(default_factory=list)
    footnotes: list[ResearchFootnoteResponse] = Field(default_factory=list)
    text_sha256: str
    book_id: str | None = None
    publication_sha256: str | None = None
    source_run_id: str | None = None
    original_article_id: str | None = None


class ResearchMentionResponse(EntityMention):
    kind: Literal["person", "organization", "place", "event", "document"]
    entity_id: str
    canonical_id: str | None = None
    source_mention_id: str | None = None


class ResearchEventResponse(EventDraft):
    entity_id: str
    canonical_id: str | None = None
    source_mention_id: str | None = None


class ResearchHumanDecision(BaseModel):
    action: Literal["accept", "edit", "reject"]
    at: float
    previous_version: int


class CardReadingIdentity(BaseModel):
    display_id: str = ""
    authors: str = ""
    formation_date: ResearchDate = Field(default_factory=ResearchDate)
    publication_date: ResearchDate = Field(default_factory=ResearchDate)
    pages: list[int] = Field(default_factory=list)
    printed_pages: str = ""
    source_citation: str = ""
    citation_completeness: Literal["complete", "partial", "unknown"] = "unknown"
    dossier_available: bool = False


class ResearchCardResponse(CardDraft, CardReadingIdentity):
    model_config = ConfigDict(extra="allow")
    book_id: str
    run_id: str
    state: str
    version: int
    approval_type: str | None = None
    book_title: str = ""
    checks: VerificationSubmission | CardOnlyVerificationSubmission | DossierVerificationSubmission
    dossier: CardDossier | None = None
    card_only: bool = False
    processing_mode: Literal["source_cards", "legacy_research"] = "legacy_research"
    human_notes: str = ""
    notes_version: int = 0
    notes_updated_at: float | None = None
    human_decision: ResearchHumanDecision | None = None
    source_current: bool = True
    needs_update: bool = False
    source_status: Literal["current", "revision_pending", "stale"] = "current"
    research_current: bool = True
    superseded: bool = False
    evidence: list[ResearchEvidenceResponse]
    mentions: list[ResearchMentionResponse]
    events: list[ResearchEventResponse]
    replaces_card_id: str | None = None
    lineage_id: str | None = None
    publication_sha256: str = ""
    source_run_id: str = ""
    article_sha256: str | None = None
    created_at: float | None = None
    linked_card_id: str | None = None
    derived_relations: "ResearchDerivedRelationsResponse" = Field(
        default_factory=lambda: ResearchDerivedRelationsResponse()
    )


class EditableCardResponse(BaseModel):
    card: CardDraft | CardOnlyCardDraft | DossierCardDraft
    version: int
    source_current: bool
    superseded: bool
    card_only: bool = False
    processing_mode: Literal["source_cards", "legacy_research"] = "legacy_research"


class CardNotesResponse(BaseModel):
    text: str
    version: int
    updated_at: float | None = None


class CardNotesUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=0)
    text: str = Field(max_length=60000)


class ResearchCardSummary(CardReadingIdentity):
    id: str
    book_id: str
    run_id: str
    article_id: str
    title: str
    summary: str
    state: str
    version: int
    approval_type: str | None
    tags: list[str]
    document_type: str
    source_layer: str
    article_title: str
    evidence_count: int
    item_count: int
    entity_count: int
    event_count: int = 0
    relation_count: int = 0
    cross_relation_count: int = 0
    coverage_count: int = 0
    reading_target: ReadingTarget | None = None
    source_current: bool = True
    needs_update: bool = False
    source_status: Literal["current", "revision_pending", "stale"] = "current"
    research_current: bool = True
    superseded: bool = False
    replaces_card_id: str | None = None
    lineage_id: str | None = None
    card_only: bool = False
    processing_mode: Literal["source_cards", "legacy_research"] = "legacy_research"


class ResearchTaskResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    task_id: str
    stage: str
    state: str
    error: str | None = None
    message: str | None = None


class ResearchRunResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    run_id: str
    book_id: str
    source_run_id: str
    publication_sha256: str
    input_hash: str
    state: str
    version: int
    counts: dict[str, int]
    tasks: list[ResearchTaskResponse]
    card_ids: list[str] = Field(default_factory=list)
    card_only: bool = False
    processing_mode: Literal["source_cards", "legacy_research"] = "legacy_research"


class ResearchIdentityLinkResponse(IdentityLink):
    model_config = ConfigDict(extra="allow")
    source_entity_id: str
    card_id: str
    card_title: str = ""
    approval_type: str | None = None
    evidence: list[ResearchEvidenceResponse] = Field(default_factory=list)
    reading_target: ReadingTarget | None = None


class ResearchEvidenceRefResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    card_id: str
    card_version: int
    evidence_id: str
    evidence: ResearchEvidenceResponse | None = None


class ResearchAliasResponse(BaseModel):
    name: str
    evidence_refs: list[ResearchEvidenceRefResponse] = Field(default_factory=list)
    source_current: bool = True
    approval_type: str | None = None
    historical_evidence_refs: list[ResearchEvidenceRefResponse] = Field(default_factory=list)


class CanonicalEntityResponse(BaseModel):
    id: str
    name: str
    kind: str
    version: int = 1
    aliases: list[ResearchAliasResponse] = Field(default_factory=list)
    historical_aliases: list[ResearchAliasResponse] = Field(default_factory=list)
    withdrawn_aliases: list[ResearchAliasResponse] = Field(default_factory=list)


class ResearchSourceMentionResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    canonical_id: str | None = None
    local_id: str = ""
    name: str
    kind: str
    card_id: str
    card_version: int = 1
    card_title: str = ""
    book_id: str
    run_id: str
    article_id: str = ""
    publication_sha256: str = ""
    state: str
    identity_basis: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[ResearchEvidenceRefResponse] = Field(default_factory=list)
    evidence: list[ResearchEvidenceResponse] = Field(default_factory=list)
    reading_target: ReadingTarget | None = None
    source_current: bool = True


class ResearchRelationshipResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    source_id: str
    target_id: str
    kind: str = "association"
    predicate: str = ""
    reason: str = ""
    role: str = ""
    card_id: str
    card_version: int = 1
    target_card_id: str | None = None
    target_card_version: int | None = None
    book_id: str = ""
    run_id: str = ""
    approval_type: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[ResearchEvidenceRefResponse] = Field(default_factory=list)
    evidence: list[ResearchEvidenceResponse] = Field(default_factory=list)
    source_evidence_ids: list[str] = Field(default_factory=list)
    target_evidence_ids: list[str] = Field(default_factory=list)
    target_publication_sha256: str | None = None
    source_current: bool = True
    target_current: bool = True
    needs_update: bool = False


class ResearchDerivedRelationsResponse(BaseModel):
    items: list[ResearchRelationshipResponse] = Field(default_factory=list)
    truncated: bool = False


class ResearchParticipationResponse(ResearchRelationshipResponse):
    event_id: str
    event_name: str = ""
    participant_name: str = ""
    date: ResearchDate | None = None
    action: str = ""
    place: SourceValue | None = None
    event_status: str = "unknown"
    status_note: str = ""


class ResearchParticipantResponse(BaseModel):
    entity_id: str
    name: str = ""
    role: str = "participant"


class ResearchEventDetailResponse(BaseModel):
    id: str
    name: str
    date: ResearchDate | None = None
    action: str = ""
    place: SourceValue | None = None
    event_status: str = "unknown"
    status_note: str = ""
    card_id: str
    evidence: list[ResearchEvidenceResponse] = Field(default_factory=list)
    participants: list[ResearchParticipantResponse] = Field(default_factory=list)


class ResearchIdentityDecisionResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    entity_id: str
    target_id: str | None = None
    action: str
    approval_type: str
    reason: str = ""
    evidence_refs: list[ResearchEvidenceRefResponse] = Field(default_factory=list)
    at: float


class ResearchEntityResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    name: str
    kind: str
    card_id: str
    book_id: str
    run_id: str
    state: str
    identity_basis: str = ""
    description: str = ""
    date: ResearchDate | None = None
    card_title: str = ""
    mentions: list["ResearchEntityResponse"] = Field(default_factory=list)
    identity_links: list[ResearchIdentityLinkResponse] = Field(default_factory=list)
    evidence: list[ResearchEvidenceResponse] = Field(default_factory=list)
    reading_target: ReadingTarget | None = None
    source_current: bool = True
    needs_update: bool = False
    source_status: Literal["current", "revision_pending", "stale"] = "current"
    research_current: bool = True
    superseded: bool = False
    canonical_id: str | None = None
    version: int = 1
    canonical: CanonicalEntityResponse | None = None
    aliases: list[ResearchAliasResponse] = Field(default_factory=list)
    historical_aliases: list[ResearchAliasResponse] = Field(default_factory=list)
    withdrawn_aliases: list[ResearchAliasResponse] = Field(default_factory=list)
    source_mentions: list[ResearchSourceMentionResponse] = Field(default_factory=list)
    participations: list[ResearchParticipationResponse] = Field(default_factory=list)
    events: list[ResearchEventDetailResponse] = Field(default_factory=list)
    relationships: list[ResearchRelationshipResponse] = Field(default_factory=list)
    identity_decisions: list[ResearchIdentityDecisionResponse] = Field(default_factory=list)


class ResearchBookResponse(BaseModel):
    book_id: str
    book_title: str
    indexed: bool
    source_version: str | None
    runs: list[ResearchRunResponse]
    cards: list[ResearchCardSummary]
    cards_count: int
    entities: list[ResearchEntityResponse]
    entities_count: int
    needs_update: bool = False
    stale_cards_count: int = 0
    source_revision_pending: bool = False
    needs_upgrade: bool = False
    research_policy_version: int | None = None
    required_research_policy_version: int = 6


class ResearchCardPage(BaseModel):
    items: list[ResearchCardSummary]
    total: int
    offset: int
    limit: int


class ResearchEntityPage(BaseModel):
    items: list[ResearchEntityResponse]
    total: int
    offset: int
    limit: int


class ResearchTargetCardResponse(BaseModel):
    id: str
    version: int
    title: str
    publication_sha256: str


class ResearchSourceHit(SearchHit):
    research_cards: list[ResearchTargetCardResponse] = Field(default_factory=list)
    cards_omitted: int = 0


class ResearchSearchResponse(BaseModel):
    query: str
    backend: Literal["hybrid", "lexical"]
    available: bool
    hits: list[ResearchSourceHit]
    evidence_status: str
    metrics: dict
    cards: list[ResearchCardSummary]
    entities: list[ResearchEntityResponse]
    research_backend: Literal["hybrid", "lexical", "sql_literal_fallback"]
    research_available: bool = False
    research_index: dict = Field(default_factory=dict)


class Research:
    def __init__(self, settings, engine):
        self.settings, self.engine = settings, engine
        self.bridge = ResearchBridge(settings, engine)
        self.workflow = self.bridge.workflow
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
        cards = self.records(
            db.research_cards, book_id, offset=offset, limit=limit, include=include_cards
        )
        entities = self.records(
            db.research_entities, book_id, offset=offset, limit=limit, include=include_entities
        )
        stale = self.records(db.research_cards, book_id, include=False, stale_only=True)["total"]
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
        from hrs.processing.research_workflow import POLICY

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

    def records(
        self,
        table,
        book_id=None,
        *,
        offset=0,
        limit=100,
        include=True,
        query=None,
        stale_only=False,
        book_ids=None,
        record_ids=None,
    ):
        workspace_id = identity(self.bridge.store)["workspace_id"]
        conditions = [
            db.research_runs.c.workspace_id == workspace_id,
            db.books.c.state.not_in(["deleted", "deleting", "delete_failed"]),
        ]
        if stale_only:
            conditions.append(
                not_(
                    func.coalesce(
                        and_(*current_source_conditions(db.research_runs, db.runs)), False
                    )
                )
            )
        else:
            conditions.extend(current_source_conditions(db.research_runs, db.runs))
            conditions.append(
                table.c.id.in_(current_card_ids(workspace_id))
                if table is db.research_cards
                else table.c.summary["card_id"].astext.in_(current_card_ids(workspace_id))
            )
        if table is db.research_cards:
            conditions.append(table.c.state != "excluded")
        if book_id:
            conditions.append(table.c.book_id == str(book_id))
        if book_ids is not None:
            conditions.append(table.c.book_id.in_(book_ids))
        if record_ids is not None:
            conditions.append(
                or_(
                    table.c.id.in_(record_ids),
                    table.c.summary["canonical_id"].astext.in_(record_ids),
                )
                if table is db.research_entities
                else table.c.id.in_(record_ids)
            )
        if query:
            # The cards/entities lane is explicitly SQL literal search; source hits use hybrid search.
            conditions.append(
                table.c.search_text.ilike(
                    "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%",
                    escape="\\",
                )
            )
        relation = (
            table.join(db.research_runs)
            .join(db.runs, db.runs.c.id == db.research_runs.c.source_run_id)
            .join(db.books, db.books.c.id == table.c.book_id)
        )
        with self.engine.connect() as connection:
            total = connection.scalar(select(func.count()).select_from(relation).where(*conditions))
            rows = (
                connection.execute(
                    select(table.c.summary, db.runs.c.result["codex_revision_pending"].as_boolean())
                    .select_from(relation)
                    .where(*conditions)
                    .order_by(table.c.id)
                    .offset(offset)
                    .limit(limit)
                ).all()
                if include
                else []
            )
        items = [
            {
                **summary,
                "source_current": not stale_only,
                "needs_update": stale_only or bool(revision),
                "source_status": "stale"
                if stale_only
                else "revision_pending"
                if revision
                else "current",
            }
            for summary, revision in rows
        ]
        return {"items": items, "total": total, "offset": offset, "limit": limit}

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
        cards = self.records(
            db.research_cards,
            book_ids=selected,
            query=None if available else query,
            record_ids=[r["record_id"] for r in found["records"] if r["record_kind"] == "card"]
            if available
            else None,
            limit=limit,
        )["items"]
        entities = self.records(
            db.research_entities,
            book_ids=selected,
            query=None if available else query,
            record_ids=[r["record_id"] for r in found["records"] if r["record_kind"] == "entity"]
            if available
            else None,
            limit=limit,
        )["items"]
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
        if card.get("dossier"):
            from hrs.research.dossier_export import export_dossier

            return export_dossier(
                card, articles, base_url=base_url, render_evidence=evidence_markdown
            )
        lines = [f"# {card['title']}", ""]
        evidence_anchors = {
            span["id"]: f"research-evidence-{index}"
            for index, span in enumerate(card["evidence"], 1)
        }
        names = {
            item["id"]: item.get("name") or item.get("title") or item["id"]
            for item in [*card.get("items", []), *card.get("mentions", []), *card.get("events", [])]
        }

        def field(label, value):
            lines.append(
                f"- **{label}**：{value if value is not None and value != '' else '未记录'}"
            )

        def references(identities):
            return (
                "、".join(
                    f"[{identity}](#{evidence_anchors[identity.rsplit('/', 1)[-1]]})"
                    if identity.rsplit("/", 1)[-1] in evidence_anchors
                    else f"`{identity}`"
                    for identity in identities
                )
                or "未列出"
            )

        def named(identity):
            return f"{names.get(identity, identity)}（`{identity}`）"

        def date(label, value):
            value = value or {}
            lines.extend([f"### {label}", ""])
            field("原文表述", value.get("text"))
            field("标准化日期", value.get("normalized"))
            field("确定性", value.get("certainty", "unknown"))
            field("日期依据", references(value.get("evidence_ids", [])))
            lines.append("")

        lines.extend(["## 卡片与来源版本", ""])
        for label, value in (
            ("卡片 ID（id）", card["id"]),
            ("卡片版本（version）", card.get("version")),
            ("原任务内 ID（local_id）", card.get("local_id")),
            ("卡片沿革（lineage_id）", card.get("lineage_id")),
            ("替代此前卡片（replaces_card_id）", card.get("replaces_card_id")),
            (
                "已保留修订（revision_versions）",
                "、".join(map(str, card.get("revision_versions", []))),
            ),
            ("研究任务（run_id）", card["run_id"]),
            ("原文任务（source_run_id）", card.get("source_run_id", snapshot.get("source_run_id"))),
            ("书籍（book_id）", card.get("book_id", snapshot.get("book_id"))),
            ("书名", card.get("book_title") or snapshot.get("book_title") or snapshot.get("title")),
            ("文章（article_id）", card.get("article_id")),
            ("原文篇名", card.get("article_title")),
            ("原生文档（document_id）", snapshot.get("document_id")),
            ("工作区（workspace_id）", snapshot.get("workspace_id")),
            (
                "发布版本（publication_sha256）",
                card.get("publication_sha256", snapshot.get("publication_sha256")),
            ),
            ("章节版本（chapters_sha256）", snapshot.get("chapters_sha256")),
            ("文章版本（article_sha256）", card.get("article_sha256")),
            ("原件 SHA256", (snapshot.get("source") or {}).get("sha256")),
            ("状态", card.get("state", "draft")),
            ("认证方式", card.get("approval_type", "none")),
            ("来源状态", card.get("source_status", "current")),
            ("是否需要更新", "是" if card.get("needs_update") else "否"),
            ("研究修订是否已替代", "是" if card.get("superseded") else "否"),
            ("创建时间（Unix 秒）", card.get("created_at")),
        ):
            field(label, value)
        lines.append("")
        if not card.get("source_current", True):
            lines.extend(["历史卡片：来源已换版，需要重新整理；以下保留原版本哈希供追溯。", ""])
        elif card.get("needs_update"):
            lines.extend(["来源正在修订；此卡片对应此前已发布版本。", ""])
        if card.get("superseded"):
            lines.extend(["此研究修订已有后续版本；以下保留原卡片内容。", ""])
        lines.extend(["## 史料属性与年代", ""])
        field("文献类型（document_type）", card.get("document_type"))
        field("史料层次（source_layer）", card.get("source_layer"))
        field("标签（tags）", "、".join(card.get("tags", [])))
        lines.append("")
        date("成文时间（formation_date）", card.get("formation_date"))
        date("事件时间（event_date）", card.get("event_date"))
        metadata = card.get("metadata") or {}
        if metadata:
            lines.extend(["## 文献身份与形成信息（metadata）", ""])
            labels = {
                "original_title": "原题",
                "authors": "作者",
                "recipients": "收受者",
                "editors": "编者",
                "publication": "出版与版本",
                "volume": "卷次",
                "archive_id": "档号",
                "formation_place": "成文地点",
                "purpose": "写作目的",
                "testimony": "记载性质",
            }
            field("标题性质（title_origin）", metadata.get("title_origin"))
            for key, value in metadata.items():
                if key == "title_origin":
                    continue
                lines.extend([f"### {labels.get(key, key)}（{key}）", ""])
                field("原文记载", value.get("value") or "未详")
                field("认识状态", value.get("certainty"))
                field("原文依据", references(value.get("evidence_ids", [])))
                field("说明与局限", value.get("note"))
                lines.append("")
        lines.extend(["## 提要", "", card["summary"], "", "## 卡片内容", ""])
        kinds = {
            "section": "原文结构",
            "evidence": "史料要点",
            "argument": "论点与论证",
            "issue": "研究问题",
            "paper_use": "论文用途",
        }
        for item in card.get("items", []):
            lines.extend(
                [
                    f"### {item['title'] or kinds.get(item['kind'], item['kind'])}",
                    "",
                    item["text"],
                    "",
                ]
            )
            field("条目 ID", item["id"])
            field("类型", f"{kinds.get(item['kind'], item['kind'])}（{item['kind']}）")
            field("认识状态", item.get("epistemic_state"))
            field("归属 / 发言者", item.get("attribution"))
            field("依据", references(item.get("evidence_ids", [])))
            field("条目使用边界", "；".join(item.get("limitations", [])))
            lines.append("")
        lines.extend(["## 人物、机构、地点与实体记载（mentions）", ""])
        for mention in card.get("mentions", []):
            lines.extend([f"### {mention['name']}", ""])
            field("本卡实体", named(mention["id"]))
            field("实体类型", mention["kind"])
            field("实体 ID", mention.get("entity_id"))
            field("身份依据", mention["identity_basis"])
            field("原文依据", references(mention["evidence_ids"]))
            for alias in mention.get("aliases", []):
                field("别名", alias["name"])
                field("别名依据", references(alias.get("evidence_ids", [])))
            for location in mention.get("locations", []):
                field(
                    "原文提及位置",
                    f"{location['article_id']} {location['start']}–{location['end']}",
                )
                field("原文写法", location.get("quote"))
            lines.append("")
        lines.extend(["## 事件（events）", ""])
        for event in card.get("events", []):
            lines.extend([f"### {event['name']}", "", event["description"], ""])
            field("事件 ID", event["id"])
            field("实体 ID", event.get("entity_id"))
            field("参与者", "、".join(named(identity) for identity in event["participant_ids"]))
            field("原文依据", references(event["evidence_ids"]))
            field("具体行为（action）", event.get("action"))
            field("材料中的事件性质（event_status）", event.get("event_status", "unknown"))
            field("事件性质说明（status_note）", event.get("status_note"))
            place = event.get("place") or {}
            field("事件地点", place.get("value") or "未详")
            field("地点认识状态", place.get("certainty"))
            field("地点依据", references(place.get("evidence_ids", [])))
            field("地点说明", place.get("note"))
            for role in event.get("participant_roles", []):
                field("参与者角色", f"{named(role['mention_id'])}：{role['role']}")
                field("角色记载", role["description"])
                field("角色依据", references(role["evidence_ids"]))
            lines.append("")
            date(f"{event['name']}的日期", event.get("date"))
        lines.extend(["## 关系网络（relations）", ""])
        for relation in card.get("relations", []):
            lines.extend(
                [
                    f"### {named(relation['subject_id'])} → {relation['predicate']} → {named(relation['object_id'])}",
                    "",
                ]
            )
            field("关系 ID", relation["id"])
            field("关系作用", relation["role"])
            field("关联理由", relation["reason"])
            field("原文依据", references(relation["evidence_ids"]))
            lines.append("")
        lines.extend(["## 跨卡身份关联（identity_links）", ""])
        for link in card.get("identity_links", []):
            field("本卡实体", named(link["local_mention_id"]))
            field("目标实体", link["entity_id"])
            field(
                "身份关系",
                "可能同一身份（possible_same_as）"
                if link["relation"] == "possible_same_as"
                else "同一身份（same_as）",
            )
            field("关联理由", link["reason"])
            field("原文依据", references(link["evidence_ids"]))
            lines.append("")
        if card.get("cross_relations"):
            lines.extend(["## 跨卡文献与证据关系（cross_relations）", ""])
            for relation in card["cross_relations"]:
                lines.extend([f"### {relation['kind']} · {relation['id']}", ""])
                for key in (
                    "target_card_id",
                    "target_card_version",
                    "target_publication_sha256",
                    "certainty",
                    "reason",
                ):
                    field(key, relation.get(key))
                field("本卡来源版本", card.get("publication_sha256"))
                field("本篇原文依据", references(relation["source_evidence_ids"]))
                field("对方原文依据", references(relation["target_evidence_ids"]))
                lines.extend(
                    [
                        f"[查看对方卡片]({base_url}/#/research?{urlencode({'card': relation['target_card_id']})})",
                        "",
                    ]
                )
        derived = card.get("derived_relations") or {}
        if derived.get("items"):
            lines.extend(["## 后续补充的双向关系（derived_relations）", ""])
            for relation in derived["items"]:
                lines.extend([f"### {relation.get('kind', 'association')} · {relation['id']}", ""])
                for key in (
                    "card_id",
                    "card_version",
                    "source_id",
                    "target_id",
                    "target_card_version",
                    "target_publication_sha256",
                    "certainty",
                    "reason",
                    "needs_update",
                ):
                    field(key, relation.get(key))
                for span in relation.get("evidence", []):
                    lines.extend(
                        [
                            "",
                            f"**冻结证据 {span['id']}** · {span['article_id']} {span['start']}–{span['end']}",
                            "",
                            span["quote"],
                            "",
                        ]
                    )
                    field("来源发布版本", span.get("publication_sha256"))
                    field("原文 SHA256", span.get("text_sha256"))
                    for note in span.get("footnotes", []):
                        field("源脚注 " + note["id"], note.get("text"))
                lines.extend(
                    [
                        f"[查看独立证据包]({base_url}/#/research?{urlencode({'card': relation['card_id']})})",
                        "",
                    ]
                )
            if derived.get("truncated"):
                lines.extend(["补充关系有省略，请在工作台继续查阅。", ""])
        if card.get("coverage"):
            lines.extend(["## 全文提取覆盖（coverage）", ""])
            for unit in card["coverage"]:
                lines.extend(
                    [
                        f"### {unit['id']} · {unit['article_id']} {unit['start']}–{unit['end']}",
                        "",
                        unit["summary"],
                        "",
                    ]
                )
                for key in ("mention_ids", "event_ids", "relation_ids"):
                    field(key, "、".join(unit.get(key, [])))
                for key in ("no_mentions_reason", "no_events_reason", "no_relations_reason"):
                    field(key, unit.get(key))
                lines.append("")
        if card.get("research_plan"):
            lines.extend(["## 补证计划与证据评价（research_plan）", ""])
            for plan in card["research_plan"]:
                lines.extend([f"### {plan['question']}", ""])
                for key in ("id", "status", "evaluation", "stop_reason"):
                    field(key, plan.get(key))
                for key in ("proposed_queries", "search_receipt_ids", "missing_evidence"):
                    field(key, "；".join(plan.get(key, [])))
                field("实际采用依据", references(plan.get("evidence_ids", [])))
                lines.append("")
        if card.get("question_findings"):
            lines.extend(["## 研究问题结论（question_findings）", ""])
            for finding in card["question_findings"]:
                for key in ("id", "question_id", "definition_version", "status"):
                    field(key, finding.get(key))
                lines.extend(["", finding["text"], ""])
                field("原文依据", references(finding.get("evidence_ids", [])))
                field("未解决事项", "；".join(finding.get("unresolved", [])))
                lines.append("")
        for heading, key in (
            ("使用边界（limitations）", "limitations"),
            ("备选解释（alternatives）", "alternatives"),
            ("待核问题（questions）", "questions"),
        ):
            lines.extend([f"## {heading}", "", *(f"- {value}" for value in card.get(key, [])), ""])
        field("未提出论点的理由（no_argument_reason）", card.get("no_argument_reason"))
        lines.extend(["", "## 核验与人工决定", ""])
        checks = card.get("checks") or {}
        field("独立核验结论", checks.get("decision"))
        field("核验输入哈希", checks.get("input_hash"))
        for key, label in (
            ("checked_card_ids", "核验卡片"),
            ("checked_item_ids", "核验条目"),
            ("checked_evidence_ids", "核验引文"),
            ("checked_relation_ids", "核验关系"),
            ("checked_identity_links", "核验身份关联"),
            ("checked_search_receipt_ids", "核验检索回执"),
            ("checked_mechanical_evidence_ids", "核验字符回执"),
            ("checked_metadata_fields", "核验文献信息"),
            ("checked_entity_ids", "核验人物实体"),
            ("checked_event_ids", "核验事件"),
            ("checked_coverage_ids", "核验全文覆盖"),
            ("checked_research_question_ids", "核验补证计划"),
            ("checked_cross_relation_ids", "核验跨卡关系"),
            ("checked_question_finding_ids", "核验研究问题结论"),
        ):
            if card.get("card_only") and key in {
                "checked_search_receipt_ids",
                "checked_research_question_ids",
                "checked_cross_relation_ids",
                "checked_question_finding_ids",
            }:
                continue
            field(label, "、".join(checks.get(key, [])))
        if not card.get("card_only"):
            field("补充研究判定已核验", checks.get("checked_research_disposition"))
        field("未解决事项", "；".join(checks.get("issues", [])))
        decision = card.get("human_decision") or {}
        field("人工决定", decision.get("action"))
        field("人工决定所据此前版本", decision.get("previous_version"))
        field("人工决定时间（Unix 秒）", decision.get("at"))
        disposition = card.get("research_disposition") or {}
        if disposition:
            lines.extend(["", "### 补充研究判定", ""])
            for key in ("mode", "category", "reason"):
                field(key, disposition.get(key))
            field("判定依据", references(disposition.get("evidence_ids", [])))
        if card.get("human_notes"):
            lines.extend(["", "## 个人备注（用户记录，非原文）", "", card["human_notes"], ""])
            field("备注版本", card["notes_version"])
        lines.append("")
        lines.extend(["## 原文证据", ""])
        for index, span in enumerate(card["evidence"], 1):
            target = span.get("reading_target") or {}
            pages = ",".join(map(str, span.get("pages", [])))
            query = urlencode(
                {
                    "tab": "reader",
                    "section": "text",
                    "chapter": target.get("chapter_id", span["article_id"]),
                    "physicalPage": target.get("physical_page", ""),
                    "start": target.get("start", span["start"]),
                    "end": target.get("end", span["end"]),
                    "textSha": span["text_sha256"],
                    "run": target.get("run_id", span.get("source_run_id", card["source_run_id"])),
                }
            )
            href = f"{base_url}/#/books/{span.get('book_id', card['book_id'])}?{query}"
            lines.extend(
                [
                    f'<a id="research-evidence-{index}"></a>',
                    "",
                    f"### {span['id']} · 物理页 {pages}",
                    "",
                    evidence_markdown(span, articles[span["article_id"]], f"research-{index}"),
                    "",
                    f"文章：{span['article_id']}；范围：{span['start']}–{span['end']}；"
                    f"原文 SHA256：{span['text_sha256']}",
                    "",
                    f"原图 SHA256：{target.get('image_sha256', '')}",
                    "",
                    f"来源书籍：{span.get('book_id', card['book_id'])}；原文任务（source_run_id）："
                    f"{span.get('source_run_id', target.get('run_id', card.get('source_run_id')))}；"
                    f"原文章 ID：{span.get('original_article_id', target.get('chapter_id', span['article_id']))}",
                    "",
                    f"来源发布版本（publication_sha256）：{span.get('publication_sha256', card.get('publication_sha256', snapshot.get('publication_sha256', '未记录')))}",
                    "",
                    "源脚注 ID：" + "、".join(span.get("footnote_ids", [])),
                    "",
                    f"[阅读原文]({href})",
                    "",
                ]
            )
        return "\n".join(lines)


def evidence_markdown(span, article, prefix):
    """Reuse the source export's note ownership and namespacing for each quoted range."""
    start, end = span["start"], span["end"]
    parts = [
        {
            "text": article["text"][max(start, part["start"]) : min(end, part["end"])],
            "source": part["source"],
        }
        for part in article["parts"]
        if part["start"] < end and part["end"] > start
    ]
    for note in span.get("footnotes", []):
        left, right = note["note"]["start"], note["note"]["end"]
        if start <= left < right <= end:
            continue
        owner = next(part for part in article["parts"] if part["start"] <= left < part["end"])
        parts.append({"text": "\n\n" + article["text"][left:right], "source": owner["source"]})
    text = "".join(part["text"] for part in parts)
    return export_footnotes(text, resolve_footnotes(text, parts), prefix, parts=parts)
