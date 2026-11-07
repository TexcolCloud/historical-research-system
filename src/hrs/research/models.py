"""Frozen research submission contracts. Source ownership is checked by the workflow."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class EntityMention(Contract):
    """A source mention, never a name-only identity merge across publications."""

    id: str = Field(min_length=1)
    kind: Literal["person", "organization", "place", "event"]
    name: str = Field(min_length=1)
    identity_basis: str = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)


class EventDraft(Contract):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    date: ResearchDate = Field(default_factory=ResearchDate)
    participant_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(min_length=1)


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

    @model_validator(mode="after")
    def references_are_local(self):
        collections = (self.items, self.evidence, self.mentions, self.events, self.relations)
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

    @model_validator(mode="after")
    def issues_match_decision(self):
        if self.decision == "pending" and not self.issues:
            raise ValueError("A pending verification must preserve its unresolved issues")
        if self.decision == "approve" and self.issues:
            raise ValueError("Unresolved issues cannot receive native approval")
        return self
