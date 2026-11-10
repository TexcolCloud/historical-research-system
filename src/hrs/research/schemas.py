"""HTTP response/request contracts; independent of application orchestration."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from hrs.api.schemas import FootnoteLink, ReadingTarget, SearchHit
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
