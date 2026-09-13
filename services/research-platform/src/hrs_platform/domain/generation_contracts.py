from typing import Literal

from pydantic import Field, model_validator

from .base import Contract, HistoricalDate


class QuoteSelection(Contract):
    unit_id: str
    quote: str = Field(min_length=1)
    occurrence: int = Field(default=0, ge=0)


class UnitReading(Contract):
    unit_id: str
    main_records: list[str]
    attribution: str
    dates_quantities_actors: list[str]
    negations_and_limits: list[str]
    note_table_dependencies: list[str]
    candidate_quotes: list[str]
    uncertainties: list[str]


class ResearchQuestion(Contract):
    question: str
    known_evidence: str
    missing: str
    source_unit_ids: list[str]
    within_primary: bool = True


class ReadingRecord(Contract):
    readings: list[UnitReading]
    themes: list[str]
    structure: list[str]
    questions: list[ResearchQuestion]
    boundary_observations: list[str]


class DigestFact(Contract):
    category: Literal[
        "event",
        "quantity",
        "actor",
        "attribution",
        "context",
        "limitation",
        "alternative",
        "source_criticism",
    ]
    text: str
    source_unit_ids: list[str]


class ReadingDigest(Contract):
    covered_record_indexes: list[int]
    themes: list[str]
    structure: list[str]
    facts: list[DigestFact]
    quotation_candidates: list[QuoteSelection]
    questions: list[ResearchQuestion]
    boundary_observations: list[str]


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


class CardDraft(Contract):
    title: str
    document_type: str
    source_layer: str
    formation_date: HistoricalDate
    event_date: HistoricalDate
    tags: list[str]
    entities: list[DraftEntity]
    items: list[DraftItem]
    evidence_relations: list[DraftRelation]
    no_argument_reason: str | None

    @model_validator(mode="after")
    def references_identify_candidate_items(self):
        by_id = {item.item_id: item for item in self.items}
        if len(by_id) != len(self.items):
            raise ValueError("Candidate item_id values must be unique")
        references = [ref for item in self.items for ref in item.evidence_refs]
        references += [ref for entity in self.entities for ref in entity.evidence_refs]
        references += self.formation_date.basis + self.event_date.basis
        references += [relation.evidence_id for relation in self.evidence_relations]
        invalid = sorted({ref for ref in references if ref not in by_id or by_id[ref].kind != "evidence"})
        if invalid:
            raise ValueError(
                "All evidence_refs, date basis and relation evidence_id values must be evidence item_id values in this candidate, not source unit IDs or labels. Invalid: "
                + ", ".join(invalid)
            )
        if any(
            relation.argument_id not in by_id or by_id[relation.argument_id].kind != "argument"
            for relation in self.evidence_relations
        ):
            raise ValueError("Relation argument_id must identify an argument item in this candidate")
        return self


class CheckFinding(Contract):
    object_id: str
    severity: Literal["serious", "material", "minor", "information"]
    code: str
    explanation: str
    source_unit_ids: list[str]
    required_change: str


class SemanticCheck(Contract):
    checked_object_ids: list[str]
    findings: list[CheckFinding]
    unverified_object_ids: list[str]
    conclusion: Literal["pass", "needs_revision", "insufficient_evidence"]
    reasoning_summary: str


class ImageItemCheck(Contract):
    item_id: str
    result: Literal["verified", "mismatch", "unreadable", "not_located", "not_checked"]
    observed_text: str
    quantities_headers_units_notes: list[str]
    discrepancies: list[str]
    scope: str


class OriginalPageObservation(Contract):
    image_ids: list[str]
    printed_page: str
    bibliographic_details: list[str]
    layout_and_reading_order: list[str]
    article_boundaries: list[str]
    author_editor_information: list[str]
    limitations: list[str]


class ImageCheck(Contract):
    items: list[ImageItemCheck]
    image_ids: list[str]
    limitations: list[str]
    page_observations: list[OriginalPageObservation] = Field(default_factory=list)
