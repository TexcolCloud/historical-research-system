"""Pure card plans, source constraints and repair validation; no I/O."""

import json
from typing import Literal

from pydantic import BaseModel, Field

from hrs_platform.domain.generation_contracts import CardDraft, DigestFact
from hrs_platform.domain.tokens import estimate_request

PURPOSES = {"support", "counter", "qualify"}


class EvidenceQuery(BaseModel):
    query: str = Field(min_length=1, max_length=300)
    purpose: Literal["support", "counter", "qualify"]


class EvidenceAssessment(BaseModel):
    source_unit_ids: list[str]
    findings: list[DigestFact]
    unresolved_questions: list[str]
    sufficient: bool


def coverage(check, identities):
    expected = set(identities)
    actual = set(check.checked_object_ids) | set(check.unverified_object_ids)
    if actual != expected:
        raise ValueError(
            "Review scope must contain only these source unit IDs: "
            + json.dumps(sorted(expected))
            + ". Missing IDs: "
            + json.dumps(sorted(expected - actual))
            + ". Extraneous IDs (context is not a review target): "
            + json.dumps(sorted(actual - expected))
        )
    checked, unverified = check.checked_object_ids, check.unverified_object_ids
    if (
        len(checked) != len(set(checked))
        or len(unverified) != len(set(unverified))
        or set(checked) & set(unverified)
    ):
        raise ValueError("Review targets must be unique and cannot be both checked and unverified.")
    if any(finding.object_id not in expected for finding in check.findings):
        raise ValueError("Review findings must identify a supplied review target.")


TOPIC_PREFLIGHT_TOKENS = 24000

MAX_CARD_TOPICS = 256


class Assignment(BaseModel):
    name: str
    objective: str
    chapter_ids: list[str] = Field(min_length=1)


class ResearchPlan(BaseModel):
    rationale: str
    assignments: list[Assignment] = Field(min_length=1, max_length=64)


class CardTopic(BaseModel):
    name: str
    objective: str
    unit_ids: list[str] = Field(min_length=1)
    queries: list[EvidenceQuery] = Field(default_factory=list, max_length=3)


class CardPlan(BaseModel):
    rationale: str
    topics: list[CardTopic] = Field(min_length=1, max_length=MAX_CARD_TOPICS)


def validate_topics(plan, units):
    for topic in plan.topics:
        if topic.queries and {q.purpose for q in topic.queries} != PURPOSES:
            raise ValueError("A planned evidence search requires support, counter and qualify.")
    assigned = [identity for topic in plan.topics for identity in topic.unit_ids]
    if len(assigned) != len(set(assigned)) or set(assigned) != {unit["unit_id"] for unit in units}:
        raise ValueError(
            "Every read source unit must belong to exactly one card topic; context may be shared."
        )


def validate_plan(plan, chapter_ids):
    assigned = [chapter for task in plan.assignments for chapter in task.chapter_ids]
    if len(assigned) != len(set(assigned)) or set(assigned) != set(chapter_ids):
        raise ValueError("Every chapter must belong to exactly one research assignment.")


def validate_candidate(card, units):
    known = {row["unit_id"]: row for row in units}
    for item in card.items:
        if set(item.source_unit_ids) - known.keys():
            raise ValueError("Candidate refers to unavailable source units.")
        if item.kind == "evidence" and not item.selections:
            raise ValueError("An evidence item requires exact quotations from fixed source units.")
        for selection in item.selections:
            unit = known.get(selection.unit_id)
            if unit is None or unit["text"].count(selection.quote) <= selection.occurrence:
                raise ValueError("An evidence quotation must match the fixed reviewed source exactly.")
        if item.kind == "argument" and not item.evidence_refs:
            raise ValueError("An argument must declare its supporting or limiting evidence.")
    if not any(item.kind == "evidence" for item in card.items):
        raise ValueError("No traceable evidence was produced.")


def validate_check(check, card):
    coverage(check, {item.item_id for item in card.items})


def apply_card_repair(original, patch):
    """Reassemble a complete candidate before reference and semantic validation."""
    items = {item.item_id: item.model_dump(mode="json") for item in original.items}
    changed = [item.item_id for item in patch.items]
    removed = set(patch.remove_item_ids)
    if (
        len(changed) != len(set(changed))
        or len(removed) != len(patch.remove_item_ids)
        or removed - items.keys()
        or removed & set(changed)
    ):
        raise ValueError("Card repair has duplicate, unknown or conflicting item IDs.")
    for identity in removed:
        del items[identity]
    items.update((item.item_id, item.model_dump(mode="json")) for item in patch.items)
    metadata = (
        patch.metadata.model_dump(mode="json")
        if patch.metadata is not None
        else original.model_dump(mode="json", exclude={"items"})
    )
    return CardDraft.model_validate({**metadata, "items": list(items.values())})


def validate_final_candidate(candidate, original, units):
    validate_candidate(candidate, units)
    frozen = lambda card: [
        item.model_dump(mode="json", exclude={"attribution", "context", "limitations"})
        for item in card.items
        if item.kind == "evidence"
    ]
    if frozen(candidate) != frozen(original):
        raise ValueError(
            "Final narrative revision must preserve every visually checked quotation, anchor, text and interpretation exactly; only attribution, context and limitations may change."
        )


def quote_range(unit, selection):
    start = -1
    for _ in range(selection.get("occurrence", 0) + 1):
        start = unit["text"].find(selection["quote"], start + 1)
        if start < 0:
            raise ValueError("Quotation is absent from the reviewed source.")
    end = start + len(selection["quote"])
    return start, end


def quote_pages(unit, selection):
    start, end = quote_range(unit, selection)
    pages = sorted(
        {
            page
            for source in unit["sources"]
            if source["unit_start"] < end and source["unit_end"] > start
            for page in source["pages"]
        }
    )
    if not pages:
        raise ValueError("Quotation has no original page provenance.")
    return pages


def visual_groups(candidate, units):
    groups = {}
    for item in candidate["items"]:
        pages = sorted(
            {
                page
                for selection in item["selections"]
                for page in quote_pages(units[selection["unit_id"]], selection)
            }
        )
        if not pages:
            continue
        key = "pages-" + ",".join(map(str, pages))
        groups.setdefault(key, {"key": key, "pages": pages, "items": []})["items"].append(item)
    return list(groups.values())


def split_topic(topic, all_units):
    units = [unit for unit in all_units if unit["unit_id"] in topic.unit_ids]
    if len(units) < 2:
        return []
    boundaries = [
        i
        for i in range(1, len(units))
        if (units[i].get("chapter_id"), units[i].get("section_path"))
        != (units[i - 1].get("chapter_id"), units[i - 1].get("section_path"))
    ]
    weights = [estimate_request(unit)["input_tokens"] for unit in units]
    middle = min(boundaries or range(1, len(units)), key=lambda i: abs(sum(weights[:i]) - sum(weights) / 2))
    return [
        CardTopic(
            name=f"{topic.name} · {i + 1}",
            objective=topic.objective,
            unit_ids=[unit["unit_id"] for unit in group],
        )
        for i, group in enumerate((units[:middle], units[middle:]))
    ]


def card_stage(run, name):
    revision = (run.get("result") or {}).get("card_revision", 0)
    return f"{name}:revision:{revision}" if revision else name


def repair_sources(previous, units, chosen):
    if not previous:
        return
    known = {unit["unit_id"] for unit in units}
    for check in [previous, *previous.get("source_checks", [])]:
        for finding in check.get("findings", []):
            chosen.update(set(finding.get("source_unit_ids", [])) & known)
            if finding.get("object_id") in known:
                chosen.add(finding["object_id"])
        chosen.update(set(check.get("unverified_object_ids", [])) & known)
        # A failed verdict without located findings still has an explicit checked scope.
        if check.get("conclusion") != "pass" and not check.get("findings"):
            chosen.update(set(check.get("checked_object_ids", [])) & known)
