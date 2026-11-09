"""Frozen research submission contracts. Source ownership is checked by the workflow."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, RootModel, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceSpan(Contract):
    id: str = Field(min_length=1)
    article_id: str = Field(min_length=1)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quote: str = Field(min_length=1)
    footnote_ids: list[str] = Field(default_factory=list)


class ResearchDate(Contract):
    text: str = ""
    normalized: str | None = None
    certainty: Literal["stated", "inferred", "unknown", "disputed"] = "unknown"
    evidence_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def stated_dates_have_evidence(self):
        if self.certainty in {"stated", "inferred"} and (
            not self.text.strip() or not self.evidence_ids
        ):
            raise ValueError("Stated and inferred dates require source wording and evidence")
        if self.normalized and (not self.text.strip() or not self.evidence_ids):
            raise ValueError("Normalized dates require source wording and evidence")
        return self


class CardItem(Contract):
    id: str = Field(min_length=1)
    kind: Literal["section", "evidence", "argument", "issue", "paper_use"]
    title: str = ""
    text: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)
    epistemic_state: Literal["stated", "inferred", "unknown", "disputed"] = "stated"
    attribution: str = ""
    limitations: list[str] = Field(default_factory=list)


class SourceValue(Contract):
    """Source-backed fact: certainty=unknown requires an empty value; explain absent facts in note.

    A vague but explicit source place is still stated, with its precision limits in note.
    """

    value: str = Field(
        default="",
        description="When certainty=unknown, use value=''. Do not put missing facts or uncertainty labels here.",
    )
    certainty: Literal["stated", "inferred", "unknown", "disputed"] = Field(
        default="unknown",
        description="Unknown means no factual value is supplied. A broad but explicit source value may be stated; retain evidence and describe its precision in note.",
    )
    evidence_ids: list[str] = Field(default_factory=list)
    note: str = Field(
        default="",
        description="Explain missing facts, uncertainty, attribution and precision limits here; an unknown value remains empty.",
    )

    @model_validator(mode="after")
    def supported_value(self):
        if self.certainty != "unknown" and (not self.value.strip() or not self.evidence_ids):
            raise ValueError("Known metadata requires its source wording and evidence")
        if self.certainty == "unknown" and self.value.strip():
            raise ValueError("Unknown metadata cannot silently carry a factual value")
        return self


class DocumentMetadata(Contract):
    title_origin: Literal["source", "supplied", "system", "unknown"] = "unknown"
    original_title: SourceValue = Field(default_factory=SourceValue)
    authors: SourceValue = Field(default_factory=SourceValue)
    recipients: SourceValue = Field(default_factory=SourceValue)
    editors: SourceValue = Field(default_factory=SourceValue)
    publication: SourceValue = Field(default_factory=SourceValue)
    volume: SourceValue = Field(default_factory=SourceValue)
    archive_id: SourceValue = Field(default_factory=SourceValue)
    formation_place: SourceValue = Field(default_factory=SourceValue)
    purpose: SourceValue = Field(default_factory=SourceValue)
    testimony: SourceValue = Field(default_factory=SourceValue)


class AliasDraft(Contract):
    name: str = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)


class MentionLocation(Contract):
    article_id: str = Field(min_length=1)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quote: str = Field(min_length=1)


class ParticipantRole(Contract):
    mention_id: str = Field(min_length=1)
    role: str = Field(min_length=1)
    description: str = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)


class CoverageUnit(Contract):
    id: str = Field(min_length=1)
    article_id: str = Field(min_length=1)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    summary: str = Field(min_length=1)
    mention_ids: list[str] = Field(default_factory=list)
    event_ids: list[str] = Field(default_factory=list)
    relation_ids: list[str] = Field(default_factory=list)
    no_mentions_reason: str = ""
    no_events_reason: str = ""
    no_relations_reason: str = ""


class ResearchQuestion(Contract):
    id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    status: Literal["planned", "answered", "partial", "conflicting", "unresolved"] = "planned"
    proposed_queries: list[str] = Field(default_factory=list)
    search_receipt_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    evaluation: str = ""
    missing_evidence: list[str] = Field(default_factory=list)
    stop_reason: str = ""


class QuestionFinding(Contract):
    id: str = Field(min_length=1)
    question_id: str = Field(min_length=1)
    definition_version: int = Field(ge=1)
    text: str = Field(min_length=1)
    status: Literal["answered", "partial", "unresolved"]
    evidence_ids: list[str] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)


class CrossCardRelation(Contract):
    id: str = Field(min_length=1)
    target_card_id: str = Field(min_length=1)
    target_card_version: int = Field(ge=1)
    target_publication_sha256: str = Field(min_length=64, max_length=64)
    kind: Literal[
        "reply_to",
        "reprint_of",
        "same_source",
        "support",
        "counter",
        "limit",
        "background",
        "association",
    ]
    reason: str = Field(min_length=1)
    certainty: Literal["stated", "inferred", "disputed"] = "inferred"
    source_evidence_ids: list[str] = Field(
        min_length=1,
        description="IDs in this output card.evidence citing this card's own primary source; "
        "not evidence IDs taken from a counterpart card.",
    )
    target_evidence_ids: list[str] = Field(
        min_length=1,
        description="IDs in this output card.evidence citing the exact frozen counterpart source. "
        "Copy the actual frozen-source span into this card under a new local evidence ID; "
        "counterpart-card evidence IDs belong to a separate namespace and cannot be reused as references.",
    )


class IndependentFinding(Contract):
    id: str = Field(min_length=1)
    kind: Literal["person", "organization", "place", "event", "relation"]
    article_id: str = Field(min_length=1)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quote: str = Field(min_length=1)
    description: str = Field(min_length=1)
    required: bool = True
    event_status: Literal["reported", "planned", "conditional", "negated", "unknown"]


class CoverageAssessment(Contract):
    finding_id: str = Field(min_length=1)
    status: Literal["covered", "missing", "not_applicable"]
    candidate_ids: list[str] = Field(
        default_factory=list,
        description="Qualified card_id/object_id values only, from mentions/events/relations of the same kind as this independent finding, with overlapping evidence in its source article. Items, related entities of another kind and bare object IDs cannot establish coverage. If missing, use status=missing with a precise reason and decision=pending.",
    )
    reason: str = Field(min_length=1)


class IndependentCoverage(Contract):
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    summary: str = Field(min_length=1)


class ReadingInventory(Contract):
    coverage: list[IndependentCoverage] = Field(min_length=1)
    findings: list[IndependentFinding] = Field(default_factory=list)
    no_findings_reason: str = ""


class IndependentObservation(Contract):
    article_id: str = Field(min_length=1)
    text_sha256: str = Field(min_length=64, max_length=64)
    summary: str = Field(min_length=1)
    inventory: ReadingInventory


class CardOnlyIndependentFinding(IndependentFinding):
    kind: Literal["person", "organization", "place", "document", "event", "relation", "fact"]
    event_status: Literal["reported", "planned", "conditional", "negated", "unknown"] = "unknown"
    relation_kind: Literal["alias", "other"] = Field(
        default="other",
        description="Only source-observed alias/name equivalence uses kind=relation, relation_kind=alias. "
        "This permits an explicitly evidenced retained alias to cover that relationship without a "
        "duplicate edge. Other relationships cannot use an alias to establish coverage.",
    )


class CardOnlyReadingInventory(ReadingInventory):
    findings: list[CardOnlyIndependentFinding] = Field(default_factory=list)


class CardOnlyIndependentObservation(IndependentObservation):
    inventory: CardOnlyReadingInventory


class EntityMention(Contract):
    """A source mention, never a name-only identity merge across publications."""

    id: str = Field(min_length=1)
    kind: Literal["person", "organization", "place", "event"]
    name: str = Field(min_length=1)
    identity_basis: str = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)
    identity_key: str = ""
    aliases: list[AliasDraft] = Field(default_factory=list)
    locations: list[MentionLocation] = Field(default_factory=list)


class EventDraft(Contract):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    date: ResearchDate = Field(default_factory=ResearchDate)
    participant_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(min_length=1)
    identity_key: str = ""
    action: str = ""
    place: SourceValue = Field(default_factory=SourceValue)
    participant_roles: list[ParticipantRole] = Field(default_factory=list)
    event_status: Literal["reported", "planned", "conditional", "negated", "unknown"] = "unknown"
    status_note: str = ""


class RelationDraft(Contract):
    id: str = Field(min_length=1)
    subject_id: str = Field(min_length=1)
    predicate: str = Field(min_length=1)
    object_id: str = Field(min_length=1)
    role: Literal["support", "limit", "counter", "background", "association"] = "association"
    reason: str = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)


class IdentityLink(Contract):
    local_mention_id: str = Field(min_length=1)
    entity_id: str = Field(min_length=1)
    relation: Literal["possible_same_as", "same_as"] = "possible_same_as"
    evidence_ids: list[str] = Field(min_length=1)
    reason: str = Field(min_length=1)


class CardDraft(Contract):
    id: str = Field(min_length=1)
    article_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    document_type: str = ""
    source_layer: str = ""
    formation_date: ResearchDate = Field(default_factory=ResearchDate)
    event_date: ResearchDate = Field(default_factory=ResearchDate)
    items: list[CardItem] = Field(default_factory=list)
    evidence: list[EvidenceSpan] = Field(min_length=1)
    mentions: list[EntityMention] = Field(default_factory=list)
    events: list[EventDraft] = Field(default_factory=list)
    relations: list[RelationDraft] = Field(default_factory=list)
    identity_links: list[IdentityLink] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    alternatives: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    no_argument_reason: str | None = None
    metadata: DocumentMetadata | None = None
    coverage: list[CoverageUnit] = Field(default_factory=list)
    research_plan: list[ResearchQuestion] = Field(default_factory=list)
    cross_relations: list[CrossCardRelation] = Field(default_factory=list)
    question_findings: list[QuestionFinding] = Field(default_factory=list)

    @model_validator(mode="after")
    def references_are_local(self):
        collections = (
            self.items,
            self.evidence,
            self.mentions,
            self.events,
            self.relations,
            self.coverage,
            self.research_plan,
            self.cross_relations,
            self.question_findings,
        )
        identifiers = [item.id for items in collections for item in items]
        if any("/" in identity for identity in [self.id, *identifiers]):
            raise ValueError(
                "Local card and item IDs cannot contain the verification path separator /"
            )
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Card item, evidence, mention, event and relation IDs must be unique")
        evidence_ids = {item.id for item in self.evidence}
        owners = [
            *self.items,
            *self.mentions,
            *self.events,
            *self.relations,
            *self.identity_links,
            self.formation_date,
            self.event_date,
            *(event.date for event in self.events),
            *(event.place for event in self.events),
            *(role for event in self.events for role in event.participant_roles),
            *(alias for mention in self.mentions for alias in mention.aliases),
            *self.research_plan,
            *self.question_findings,
            *(
                [
                    getattr(self.metadata, key)
                    for key in DocumentMetadata.model_fields
                    if key != "title_origin"
                ]
                if self.metadata
                else []
            ),
        ]
        if any(set(item.evidence_ids) - evidence_ids for item in owners):
            raise ValueError("Every evidence reference must identify this card's evidence")
        if any(
            item.kind in {"evidence", "argument"} and not item.evidence_ids for item in self.items
        ):
            raise ValueError("Evidence and argument items require evidence references")
        targets = {item.id for item in [*self.items, *self.mentions, *self.events]}
        if any(
            rel.subject_id not in targets or rel.object_id not in targets for rel in self.relations
        ):
            raise ValueError("Relationship endpoints must identify local items, mentions or events")
        if any(
            set(event.participant_ids) - {item.id for item in self.mentions}
            for event in self.events
        ):
            raise ValueError("Event participants must identify local source mentions")
        if any(
            link.local_mention_id not in {item.id for item in [*self.mentions, *self.events]}
            for link in self.identity_links
        ):
            raise ValueError("Identity links must identify local source mentions or events")
        for relation_index, relation in enumerate(self.cross_relations):
            for field in ("source_evidence_ids", "target_evidence_ids"):
                for evidence_index, evidence_id in enumerate(getattr(relation, field)):
                    if evidence_id not in evidence_ids:
                        raise ValueError(
                            "Cross-card relationships require both source-bound evidence sides: "
                            f"relation {self.id[:128]}/{relation.id[:128]} "
                            f"target {relation.target_card_id[:128]}@{relation.target_card_version}; "
                            f"cross_relations[{relation_index}].{field}[{evidence_index}] "
                            f"references missing ID {evidence_id[:128]} in this output card.evidence. "
                            "Counterpart evidence IDs are a separate namespace."
                        )
        mention_ids = {item.id for item in self.mentions}
        if any(
            role.mention_id not in mention_ids
            for event in self.events
            for role in event.participant_roles
        ):
            raise ValueError("Event roles must identify retained source mentions")
        if any(
            finding.status != "unresolved" and not finding.evidence_ids
            for finding in self.question_findings
        ):
            raise ValueError(
                "Question findings require source evidence or explicit unresolved status"
            )
        return self


class EnrichmentDisposition(Contract):
    mode: Literal["not_needed"]
    category: Literal["no_named_entities_or_external_claims"]
    reason: str = Field(min_length=20)
    evidence_ids: list[str] = Field(
        min_length=1, description="Source-bound card_id/evidence_id basis"
    )


class DraftSubmission(Contract):
    input_hash: str = Field(min_length=64, max_length=64)
    cards: list[CardDraft] = Field(min_length=1)
    research_disposition: EnrichmentDisposition | None = None


class CompleteMention(EntityMention):
    identity_key: str = Field(min_length=1)
    locations: list[MentionLocation] = Field(min_length=1)


class CompleteEvent(EventDraft):
    identity_key: str = Field(min_length=1)
    action: str = Field(min_length=1)
    status_note: str = Field(min_length=1)
    event_status: Literal["reported", "planned", "conditional", "negated", "unknown"]
    date: ResearchDate = Field(
        description="Use an explicit unknown date when the source supplies none."
    )
    place: SourceValue = Field(description="Explicit source place or unknown value='' with a note.")
    participant_roles: list[ParticipantRole] = Field(
        description="One evidenced role per retained participant, or [] if none is identified."
    )


class CompleteMetadata(DocumentMetadata):
    title_origin: Literal["source", "supplied", "system", "unknown"]
    original_title: SourceValue
    authors: SourceValue
    recipients: SourceValue
    editors: SourceValue
    publication: SourceValue
    volume: SourceValue
    archive_id: SourceValue
    formation_place: SourceValue
    purpose: SourceValue
    testimony: SourceValue


class CompleteCardDraft(CardDraft):
    """Policy-v5 native write contract; legacy cards keep their original sparse schema."""

    metadata: CompleteMetadata = Field(
        description="Fill every field with cited facts or an explicit unknown note."
    )
    coverage: list[CoverageUnit] = Field(min_length=1)
    research_plan: list[ResearchQuestion] = Field(
        min_length=1,
        description="Author proposes source-bound questions here without calling research_plan. Only enrich/repair persist and evaluate this plan with that tool.",
    )
    mentions: list[CompleteMention] = Field(default_factory=list)
    events: list[CompleteEvent] = Field(default_factory=list)


class CardOnlyMention(CompleteMention):
    """Policy-v6 source mentions; older entity enums retain their frozen meanings."""

    kind: Literal["person", "organization", "place", "document", "event"]


class CardOnlyCardDraft(CardDraft):
    """One complete source card, without a mandatory research-question workflow."""

    metadata: CompleteMetadata = Field(
        description="Retain every metadata field. Missing facts remain explicit unknown values; "
        "do not invent facts or repeat the same source limitation in each field."
    )
    coverage: list[CoverageUnit] = Field(min_length=1)
    mentions: list[CardOnlyMention] = Field(default_factory=list)
    events: list[CompleteEvent] = Field(default_factory=list)


class EvaluatedResearchQuestion(ResearchQuestion):
    status: Literal["answered", "partial", "conflicting", "unresolved"]
    evaluation: str = Field(min_length=1)
    stop_reason: str = Field(min_length=1)


class CompleteEnrichedCardDraft(CompleteCardDraft):
    research_plan: list[EvaluatedResearchQuestion] = Field(
        min_length=1,
        description="The exact final plan persisted by research_plan for this enrich/repair task.",
    )


class CompleteDraftSubmission(DraftSubmission):
    cards: list[CompleteCardDraft] = Field(min_length=1, max_length=1)


class CompleteEnrichedDraftSubmission(DraftSubmission):
    cards: list[CompleteEnrichedCardDraft] = Field(min_length=1, max_length=1)


class CardOnlyDraftSubmission(DraftSubmission):
    cards: list[CardOnlyCardDraft] = Field(min_length=1, max_length=1)


class CardPatchValue(Contract):
    op: Literal["add", "replace"]
    path: str = Field(min_length=1, max_length=512)
    value: JsonValue


class CardPatchRemove(Contract):
    op: Literal["remove"]
    path: str = Field(min_length=1, max_length=512)


class NativeDeltaSubmission(Contract):
    mode: Literal["delta"]
    input_hash: str = Field(min_length=64, max_length=64)
    base_sha256: str = Field(min_length=64, max_length=64)
    changes: list[Annotated[CardPatchValue | CardPatchRemove, Field(discriminator="op")]] = Field(
        min_length=1,
        max_length=128,
        description="RFC6902 add/replace/remove under /cards/<existing index>/<writable CardDraft field>. Root card id/article_id and read-only source/approval identities cannot change.",
    )
    research_disposition: EnrichmentDisposition | None = None


class IncrementalDraftSubmission(
    RootModel[CompleteEnrichedDraftSubmission | NativeDeltaSubmission]
):
    """Prefer a frozen-baseline delta; the original complete submission remains compatible."""


class CardOnlyIncrementalSubmission(RootModel[CardOnlyDraftSubmission | NativeDeltaSubmission]):
    """A v6 repair retains the same source-card contract after delta materialization."""


class VerificationSubmission(Contract):
    input_hash: str = Field(min_length=64, max_length=64)
    decision: Literal["approve", "pending"]
    checked_card_ids: list[str] = Field(min_length=1)
    checked_item_ids: list[str] = Field(
        default_factory=list,
        description="Checked card_id/item_id values from candidate.required_checks",
    )
    checked_evidence_ids: list[str] = Field(
        default_factory=list,
        description="Checked card_id/evidence_id values from candidate.required_checks",
    )
    checked_relation_ids: list[str] = Field(
        default_factory=list,
        description="Checked card_id/relation_id values from candidate.required_checks",
    )
    checked_identity_links: list[str] = Field(
        default_factory=list,
        description="Checked card_id/link_index values from candidate.required_checks",
    )
    checked_research_disposition: bool = False
    checked_search_receipt_ids: list[str] = Field(
        default_factory=list,
        description="Every server search receipt ID in candidate.required_checks",
    )
    issues: list[str] = Field(default_factory=list)
    checked_mechanical_evidence_ids: list[str] = Field(
        default_factory=list,
        description="Every decoded mechanical evidence receipt ID in candidate.required_checks; semantic review remains separate",
    )
    checked_metadata_fields: list[str] = Field(default_factory=list)
    checked_entity_ids: list[str] = Field(default_factory=list)
    checked_event_ids: list[str] = Field(default_factory=list)
    checked_coverage_ids: list[str] = Field(default_factory=list)
    checked_research_question_ids: list[str] = Field(default_factory=list)
    checked_cross_relation_ids: list[str] = Field(default_factory=list)
    checked_question_finding_ids: list[str] = Field(default_factory=list)
    coverage_assessments: list[CoverageAssessment] = Field(default_factory=list)
    checked_inventory_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def issues_match_decision(self):
        if self.decision == "pending" and not self.issues:
            raise ValueError("A pending verification must preserve its unresolved issues")
        if self.decision == "approve" and self.issues:
            raise ValueError("Unresolved issues cannot receive native approval")
        return self


class CardOnlyCoverageAssessment(CoverageAssessment):
    candidate_ids: list[str] = Field(
        default_factory=list,
        description="Qualified source-backed candidate IDs. Entity/event/relation findings use "
        "objects of the same kind with overlapping evidence; an explicitly retained, evidenced alias "
        "may cover a relation_kind=alias source finding without a duplicate relation edge. Important fact "
        "findings may cite card_id/item_id, card_id/metadata/field, card_id/formation_date or "
        "card_id/event_date. An event finding still requires an event with unchanged modality. "
        "Missing important facts require status=missing, a precise reason and decision=pending.",
    )


class CardOnlyVerificationSubmission(VerificationSubmission):
    coverage_assessments: list[CardOnlyCoverageAssessment] = Field(default_factory=list)


def is_card_only(policy):
    return policy.get("version", 1) >= 6 and bool(policy.get("card_only"))


def card_model(policy):
    return CardOnlyCardDraft if is_card_only(policy) else CardDraft


def observation_model(policy):
    return CardOnlyIndependentObservation if is_card_only(policy) else IndependentObservation


def submission_model(policy, stage, *, frozen_name=None):
    """One selection point for displayed schemas and native write validation."""
    if stage in {"verify", "reverify"}:
        expected = (
            CardOnlyVerificationSubmission if is_card_only(policy) else VerificationSubmission
        )
    elif is_card_only(policy):
        expected = (
            CardOnlyIncrementalSubmission
            if stage == "repair" and policy.get("incremental_updates", False)
            else CardOnlyDraftSubmission
        )
    elif policy.get("version", 1) >= 5 and policy.get("complete_research"):
        expected = CompleteDraftSubmission
        if stage in {"enrich", "repair"}:
            expected = (
                IncrementalDraftSubmission
                if policy.get("incremental_updates", False)
                else CompleteEnrichedDraftSubmission
            )
    else:
        expected = DraftSubmission
    if (
        not is_card_only(policy)
        and frozen_name == "DraftSubmission"
        and stage not in {"verify", "reverify"}
    ):
        return DraftSubmission  # Already-leased old packets retain their displayed structural contract.
    if (
        not is_card_only(policy)
        and frozen_name == "CompleteEnrichedDraftSubmission"
        and stage in {"enrich", "repair"}
    ):
        return CompleteEnrichedDraftSubmission
    if frozen_name is not None and frozen_name != expected.__name__:
        raise ValueError("Frozen native submission contract does not match its policy/stage")
    return expected
