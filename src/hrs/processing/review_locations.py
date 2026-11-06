"""Locate fallible review observations without changing source Markdown or raw issues."""

import hashlib
import json
from difflib import SequenceMatcher

from hrs.processing.review_contract import valid_box


def _field(issue, *keys):
    return next((issue[k] for k in keys if isinstance(issue.get(k), str)), "")


def _text_location(text, issue):
    diagnostics = []
    anchor = issue.get("anchor", {})
    if not isinstance(anchor, dict):
        diagnostics.append("invalid_anchor")
        anchor = {}
    exact = anchor.get("exact") or _field(issue, "candidate_quote", "candidate_text", "candidate")
    if not isinstance(exact, str):
        diagnostics.append("invalid_anchor")
        exact = ""
    context = {}
    for key in ("prefix", "suffix"):
        value = anchor.get(key, "")
        if not isinstance(value, str):
            diagnostics.append("invalid_anchor_context")
            value = ""
        context[key] = value
    matches, cursor = [], 0
    while exact and (at := text.find(exact, cursor)) >= 0:
        if (not context["prefix"] or text[:at].endswith(context["prefix"])) and (
            not context["suffix"] or text[at + len(exact) :].startswith(context["suffix"])
        ):
            matches.append(at)
        cursor = at + 1

    supplied_focus = _field(issue, "focus")
    focus = supplied_focus or exact
    focus_valid = bool(focus) and exact.count(focus) == 1
    if "focus" in issue and (not isinstance(issue["focus"], str) or focus not in exact):
        diagnostics.append("invalid_focus")
    elif focus and not focus_valid:
        diagnostics.append("ambiguous_focus")
    elif not supplied_focus:
        diagnostics.append("missing_focus")
    insertion = issue.get("insertion_offset")
    if type(insertion) is not int or not 0 <= insertion <= len(exact):
        if "insertion_offset" in issue:
            diagnostics.append("invalid_insertion_offset")
        insertion = None
    reference = _field(issue, "source_quote", "source_text", "source_reading", "original", "source")
    # Uncertainty placeholders describe unreadable pixels, not a trustworthy edit span.
    uncertain = any(
        marker in reference.lower() for marker in ("〔", "unreadable", "待辨", "待核", "不清")
    )
    if (
        (not supplied_focus or not focus_valid)
        and reference
        and exact
        and len(exact) <= 500
        and not uncertain
    ):
        comparison = SequenceMatcher(None, exact, reference, autojunk=False)
        differences = [op for op in comparison.get_opcodes() if op[0] != "equal"]
        if len(differences) == 1 and comparison.ratio() >= 0.4:
            _, left, right, _, _ = differences[0]
            focus = exact[left:right]
            focus_valid = bool(focus) and exact.count(focus) == 1
            if left == right:
                insertion = left
            diagnostics.append("inferred_text_difference")

    start = end = None
    status = "missing" if not matches else "ambiguous"
    precision = "page"
    if len(matches) == 1:
        status = "located"
        if insertion is not None:
            start = end = matches[0] + insertion
            focus, precision = "", "insertion"
        elif focus_valid:
            start = matches[0] + exact.index(focus)
            end = start + len(focus)
            precision = "character" if len(focus) == 1 else "phrase"
        else:
            # A unique literal anchor is safe even when model focus metadata is not.
            start, end = matches[0], matches[0] + len(exact)
            focus, precision = exact, "phrase"
            diagnostics.append("unique_anchor_fallback")
    elif matches:
        diagnostics.append("ambiguous_text_anchor")
    else:
        diagnostics.append(
            "anchor_context_mismatch" if exact and exact in text else "text_anchor_missing"
        )
    if not focus_valid and insertion is None:
        focus = exact
    result = {
        "anchor": {"exact": exact, **context},
        "focus": focus,
        "start": start,
        "end": end,
        "text_status": status,
        "text_precision": precision,
        "offset_unit": "unicode_codepoint",
        "location_diagnostics": list(dict.fromkeys(diagnostics)),
        "unlocated_reason": _field(issue, "unlocated_reason"),
    }
    if insertion is not None:
        result["insertion_offset"] = insertion
    return result


def _image_location(issue):
    diagnostics = []
    proposed = issue.get("boxes", [issue["box"]] if "box" in issue else [])
    boxes = [box for box in proposed if valid_box(box)] if isinstance(proposed, list) else []
    if not isinstance(proposed, list) or len(boxes) != len(proposed):
        diagnostics.append("invalid_boxes")
    precision = issue.get("image_precision", "region" if boxes else "page")
    if precision not in ("line", "region", "page") or (boxes and precision == "page"):
        diagnostics.append("invalid_image_precision")
        precision = "region"
    if not boxes:
        diagnostics.append("image_unlocated")
        precision = "page"
    return boxes, precision, diagnostics


def locate_issues(text, issues, *, page, image_sha256):
    text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    located = []
    for index, raw in enumerate(issues):
        issue = raw if isinstance(raw, dict) else {"explanation": str(raw)}
        occurrences = issue.get("occurrences", issue.get("locations"))
        diagnostics = []
        spans = []
        if isinstance(occurrences, list):
            for occurrence in occurrences:
                if isinstance(occurrence, dict):
                    spans.append(_text_location(text, {**issue, **occurrence}))
                else:
                    diagnostics.append("invalid_occurrences")
        if not spans:
            spans = [_text_location(text, issue)]
            if occurrences is not None:
                diagnostics.append("invalid_occurrences")
        boxes, precision, image_diagnostics = _image_location(issue)
        if not boxes and isinstance(occurrences, list):
            for occurrence in occurrences:
                if isinstance(occurrence, dict):
                    child_boxes, _, child_diagnostics = _image_location(occurrence)
                    boxes.extend(child_boxes)
                    image_diagnostics.extend(d for d in child_diagnostics if d != "image_unlocated")
            if boxes:
                precision = "region"
                image_diagnostics = [d for d in image_diagnostics if d != "image_unlocated"]
        if len(boxes) > len(spans):
            diagnostics.append("additional_text_locations_unspecified")
        message = _field(issue, "explanation", "issue", "description", "reason", "message")
        if not message.strip():
            diagnostics.append("missing_explanation")
        diagnostics.extend(image_diagnostics)
        diagnostics.extend(d for span in spans for d in span["location_diagnostics"])
        # Preserve this exact legacy calculation: human resolutions are keyed by it.
        identity = hashlib.sha256(
            json.dumps([page, index, raw], ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()[:20]
        located.append(
            {
                "id": identity,
                "page": page,
                "text_sha256": text_hash,
                "image_sha256": image_sha256,
                **spans[0],
                "text_spans": spans,
                "location_diagnostics": list(dict.fromkeys(diagnostics)),
                "boxes": boxes,
                "image_precision": precision,
                "location": _field(issue, "location"),
                "message": message,
                "source_reading": _field(
                    issue, "source_quote", "source_text", "source_reading", "original", "source"
                ),
                "candidate": spans[0]["anchor"]["exact"],
                "suggestion": _field(issue, "suggestion"),
            }
        )
    return located
