"""One issue definition for worker instructions and submission validation.

Source uncertainty is a valid content result. Missing or unusable metadata is a
separate contract problem; legacy records are normalized without changing them.
"""

import math
from dataclasses import dataclass, field
from typing import Literal

TextPrecision = Literal["character", "phrase", "insertion", "page"]
TextStatus = Literal["located", "missing", "ambiguous"]


@dataclass
class ReviewAnchor:
    exact: str
    prefix: str = ""
    suffix: str = ""


@dataclass
class ReviewTextSpan:
    anchor: ReviewAnchor
    focus: str
    start: int | None = None
    end: int | None = None
    text_status: TextStatus = "missing"
    text_precision: TextPrecision = "page"
    offset_unit: Literal["unicode_codepoint"] = "unicode_codepoint"
    location_diagnostics: list[str] = field(default_factory=list)
    insertion_offset: int | None = None
    unlocated_reason: str = ""


@dataclass
class ReviewLocation:
    id: str
    page: int
    anchor: ReviewAnchor
    focus: str
    boxes: list[list[float]]
    image_precision: Literal["line", "region", "page"]
    message: str
    source_reading: str
    candidate: str
    suggestion: str
    text_sha256: str = ""
    image_sha256: str = ""
    start: int | None = None
    end: int | None = None
    text_status: TextStatus = "missing"
    text_precision: TextPrecision = "page"
    offset_unit: Literal["unicode_codepoint"] = "unicode_codepoint"
    location_diagnostics: list[str] = field(default_factory=list)
    text_spans: list[ReviewTextSpan] = field(default_factory=list)
    location: str = ""
    insertion_offset: int | None = None
    unlocated_reason: str = ""


ISSUE_FIELDS = {
    "explanation": "a nonempty explanation in Chinese, separate from the text to highlight",
    "anchor": "{exact,prefix,suffix}: exact literal candidate Markdown with unchanged adjacent context",
    "focus": "a nonempty literal substring of anchor.exact; never a category such as 'Personal name'",
    "source_quote": "only the reading supported by inspected original pixels; retain explicit uncertainty",
    "candidate_quote": "the exact retained candidate text being questioned",
    "boxes": "optional [[left,top,right,bottom]], finite 0..1 coordinates relative to the FULL retained page",
    "image_precision": "line or region when boxes exist; page when no trustworthy box is available",
    "insertion_offset": "for missing text, the Unicode-codepoint insertion offset within anchor.exact instead of focus",
    "unlocated_reason": "required when no trustworthy literal text anchor exists; never invent a position",
    "occurrences": "optional distinct {anchor,focus,...} locations for repeated problems; specify each literal anchor separately",
}

ISSUE_INSTRUCTIONS = (
    "Review issue contract version 1: include issue_contract_version:1 in review/verify results.\n"
    + "\n".join(f"- {name}: {description}." for name, description in ISSUE_FIELDS.items())
    + "\nExample: {explanation:'姓名中间字待核',anchor:{exact:'赵某甲',prefix:'调来',suffix:'。'},"
    "focus:'某',source_quote:'赵〔待辨〕甲',candidate_quote:'赵某甲',boxes:[[0.2,0.3,0.4,0.35]],"
    "image_precision:'line'}. Unlocated example: {explanation:'原件缺损，候选未保留该段',"
    "unlocated_reason:'缺损段无法与候选可靠对应',boxes:[],image_precision:'page'}.\n"
    "Use the same issue fields in review and independent verification. Keep actual unreadable/disputed "
    "content pending; fix a contract error by correcting metadata, never by granting approval."
)


def valid_box(value):
    return (
        isinstance(value, list)
        and len(value) == 4
        and all(type(v) in (int, float) and math.isfinite(v) and 0 <= v <= 1 for v in value)
        and value[0] < value[2]
        and value[1] < value[3]
    )


def _nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def _validate_location(location):
    if not isinstance(location, dict):
        raise ValueError("Each issue occurrence must be a location object")
    anchor = location.get("anchor", {})
    if not isinstance(anchor, dict) or any(
        key in anchor and not isinstance(anchor[key], str) for key in ("exact", "prefix", "suffix")
    ):
        raise ValueError("Issue anchor requires literal string exact/prefix/suffix fields")
    exact = anchor.get("exact", "")
    if not exact:
        if not _nonempty(location.get("unlocated_reason")):
            raise ValueError("Issue requires a literal anchor or a nonempty unlocated_reason")
    elif "insertion_offset" in location:
        insertion = location["insertion_offset"]
        if type(insertion) is not int or not 0 <= insertion <= len(exact):
            raise ValueError(
                "Issue insertion_offset must be a Unicode-codepoint offset within anchor.exact"
            )
    elif not _nonempty(location.get("focus")) or location["focus"] not in exact:
        raise ValueError(
            "Issue focus must be a nonempty literal substring of anchor.exact, not a description"
        )
    for name in ("source_quote", "candidate_quote", "suggestion", "unlocated_reason"):
        if name in location and not isinstance(location[name], str):
            raise ValueError(f"Issue {name} must be a string")
    boxes = location.get("boxes", [])
    if not isinstance(boxes, list) or not all(valid_box(box) for box in boxes):
        raise ValueError("Issue boxes must use finite normalized FULL-page coordinates")
    precision = location.get("image_precision", "region" if boxes else "page")
    if precision not in ({"line", "region"} if boxes else {"line", "region", "page"}):
        raise ValueError("Issue image_precision must be line/region, or page without boxes")


def validate_issues(issues, *, strict=False):
    """Validate new structured submissions while accepting meaningful legacy issues."""
    if not isinstance(issues, list):
        raise ValueError("Review issues must be a list")
    for issue in issues:
        if isinstance(issue, str) and issue.strip():
            # Retained string-only tasks still hand source uncertainty to humans.
            continue
        if not isinstance(issue, dict) or not issue:
            raise ValueError(
                "Each review issue must contain a nonempty explanation or legacy reading"
            )
        explanation = next(
            (
                issue[k]
                for k in ("explanation", "issue", "description", "reason", "message")
                if _nonempty(issue.get(k))
            ),
            "",
        )
        legacy_reading = any(_nonempty(issue.get(k)) for k in ("candidate_quote", "source_quote"))
        if not explanation and (strict or not legacy_reading):
            raise ValueError("Each review issue requires a nonempty explanation")
        if not strict:
            continue
        occurrences = issue.get("occurrences", issue.get("locations"))
        if occurrences is not None:
            if not isinstance(occurrences, list) or not occurrences:
                raise ValueError("Issue occurrences must be a nonempty list of explicit locations")
            for occurrence in occurrences:
                if not isinstance(occurrence, dict):
                    raise ValueError("Each issue occurrence must be a location object")
                _validate_location({**issue, **occurrence})
        else:
            _validate_location(issue)
