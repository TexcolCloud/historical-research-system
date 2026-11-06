"""Schemas retained for historical card reading and export."""

from typing import Literal

from pydantic import Field, model_validator

from hrs.library.contracts import Contract, HistoricalDate


class QuoteSelection(Contract):
    unit_id: str
    quote: str = Field(min_length=1)
    occurrence: int = Field(default=0, ge=0)


class DraftItem(Contract):
    item_id: str
    kind: Literal["section", "evidence", "argument", "issue", "paper_use"]
    title: str
    section: str = ""
    text: str = ""
    attribution: str = ""
    epistemic_state: Literal[
        "stated", "inferred", "unknown", "undetermined", "not_found", "disputed", "not_applicable"
    ] = "stated"
    evidence_refs: list[str] = Field(default_factory=list)
    source_unit_ids: list[str] = Field(default_factory=list)
    selections: list[QuoteSelection] = Field(default_factory=list)
    interpretation: str = ""
    context: str = ""
    limitations: list[str] = Field(default_factory=list)
    alternatives: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    replaces_item_ids: list[str] = Field(default_factory=list)
    table_reading: str | None = None


class DraftRelation(Contract):
    argument_id: str
    evidence_id: str
    role: Literal["support", "limit", "counter", "background"]
    reason: str
    used_scope: str


class DraftEntity(Contract):
    kind: Literal["person", "organization", "place", "event"]
    original_name: str
    evidence_refs: list[str]
    identity_basis: str


class CardMetadata(Contract):
    title: str
    document_type: str
    source_layer: str
    formation_date: HistoricalDate
    event_date: HistoricalDate
    tags: list[str]
    entities: list[DraftEntity]
    evidence_relations: list[DraftRelation]
    no_argument_reason: str | None


class CardDraft(CardMetadata):
    items: list[DraftItem]

    @model_validator(mode="after")
    def references_identify_candidate_items(self):
        by_id = {item.item_id: item for item in self.items}
        if len(by_id) != len(self.items):
            raise ValueError("Candidate item_id values must be unique")
        references = [ref for item in self.items for ref in item.evidence_refs]
        references += [ref for entity in self.entities for ref in entity.evidence_refs]
        references += self.formation_date.basis + self.event_date.basis
        references += [relation.evidence_id for relation in self.evidence_relations]
        invalid = sorted(
            {ref for ref in references if ref not in by_id or by_id[ref].kind != "evidence"}
        )
        if invalid:
            raise ValueError(
                "All evidence_refs, date basis and relation evidence_id values must be evidence item_id values in this candidate, not source unit IDs or labels. Invalid: "
                + ", ".join(invalid)
            )
        if any(
            relation.argument_id not in by_id or by_id[relation.argument_id].kind != "argument"
            for relation in self.evidence_relations
        ):
            raise ValueError(
                "Relation argument_id must identify an argument item in this candidate"
            )
        return self
