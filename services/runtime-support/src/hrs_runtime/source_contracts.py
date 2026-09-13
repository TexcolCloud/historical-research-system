"""Fail-closed checks of public source contracts, without importing another service."""

import hashlib
from datetime import datetime
from typing import Literal, NotRequired, TypedDict, TypeGuard


class SourceRead(TypedDict):
    snapshot_id: str
    source_span_ref: str
    start: int
    end: int
    offset_unit: Literal["unicode_code_point"]
    text: str
    text_sha256: str


UsageState = Literal["allowed", "limited", "blocked", "unknown"]


class UsageReference(TypedDict):
    snapshot_id: str
    source_span_ref: str
    start: int
    end: int
    expected_text_sha256: NotRequired[str | None]


class FieldDecision(TypedDict):
    field: str
    status: UsageState


class UsageDecision(TypedDict):
    reference: UsageReference
    status: UsageState
    storage_availability: Literal["verified", "unavailable"]
    fields: list[FieldDecision]


class UsageObservation(TypedDict):
    purpose: str
    requested_fields: list[str]
    policy_version: Literal["ingestion-usage-2"]
    checked_at: str
    observed_change_cursor: str
    items: list[UsageDecision]
    historical_truth_approved: NotRequired[Literal[False]]


def source_matches(source: object, reference: object) -> TypeGuard[SourceRead]:
    if not isinstance(source, dict) or not isinstance(reference, dict):
        return False
    if any(not isinstance(source.get(key), str) or not source[key]
           or source[key] != reference.get(key) for key in ("snapshot_id", "source_span_ref")):
        return False
    if any(type(source.get(key)) is not int or type(reference.get(key)) is not int
           or source[key] != reference[key] for key in ("start", "end")):
        return False
    text = source.get("text")
    if (source.get("offset_unit") != "unicode_code_point" or not isinstance(text, str)
        or source["start"] < 0 or source["end"] < source["start"]
        or len(text) != source["end"] - source["start"]):
        return False
    try:
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    except UnicodeError:
        return False
    expected = reference.get("expected_text_sha256", reference.get("text_sha256"))
    return bool(source.get("text_sha256") == digest and (expected is None or expected == digest))


def usage_matches(value: object, references: list[dict[str, object]], purpose: str, fields: list[str]) -> TypeGuard[UsageObservation]:
    """Validate correspondence, not permission: blocked/unknown remain valid decisions."""
    if not isinstance(value, dict) or value.get("purpose") != purpose or value.get("requested_fields") != fields:
        return False
    if value.get("policy_version") != "ingestion-usage-2" or value.get("historical_truth_approved", False) is not False:
        return False
    if not isinstance(value.get("checked_at"), str):
        return False
    try:
        if datetime.fromisoformat(value["checked_at"].replace("Z", "+00:00")).tzinfo is None:
            return False
    except (KeyError, ValueError, TypeError, AttributeError):
        return False
    if not isinstance(value.get("observed_change_cursor"), str) or not value["observed_change_cursor"]:
        return False
    rows = value.get("items")
    if not isinstance(rows, list) or len(rows) != len(references):
        return False
    states = ("allowed", "limited", "blocked", "unknown")
    for row, requested in zip(rows, references, strict=True):
        start, end = requested.get("start"), requested.get("end")
        if (any(not isinstance(requested.get(key), str) or not requested[key] for key in ("snapshot_id", "source_span_ref"))
            or type(start) is not int or type(end) is not int or start < 0 or end < start):
            return False
        expected_hash = requested.get("expected_text_sha256", requested.get("text_sha256"))
        if expected_hash is not None and (not isinstance(expected_hash, str) or len(expected_hash) != 64
                                         or any(c not in "0123456789abcdef" for c in expected_hash)):
            return False
        if not isinstance(row, dict) or row.get("status") not in states:
            return False
        actual = row.get("reference")
        if not isinstance(actual, dict) or actual.get("kind", "source_segment") != "source_segment":
            return False
        for key in ("snapshot_id", "source_span_ref", "start", "end", "expected_text_sha256"):
            expected = requested.get(key, requested.get("text_sha256") if key == "expected_text_sha256" else None)
            if actual.get(key) != expected or (key in {"start", "end"} and type(actual.get(key)) is not int):
                return False
        decisions = row.get("fields")
        if (row.get("storage_availability") not in ("verified", "unavailable")
            or not isinstance(decisions, list) or len(decisions) != len(fields)):
            return False
        if any(not isinstance(item, dict) or item.get("field") != field or item.get("status") not in states
               for item, field in zip(decisions, fields, strict=True)):
            return False
    return True
