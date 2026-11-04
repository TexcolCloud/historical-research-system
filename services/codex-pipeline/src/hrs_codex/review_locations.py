"""Locate fallible review observations without changing source Markdown."""

import hashlib
import json
import math
from difflib import SequenceMatcher


def locate_issues(text, issues, *, page, image_sha256):
    text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    located = []
    for index, raw in enumerate(issues):
        issue = raw if isinstance(raw, dict) else {"explanation": str(raw)}

        def field(*keys, values=issue):
            return next((values[k] for k in keys if isinstance(values.get(k), str)), "")

        anchor = issue.get("anchor") or {}
        if not isinstance(anchor, dict):
            anchor = {}
        exact = anchor.get("exact") or field("candidate_quote", "candidate_text", "candidate")
        prefix, suffix = anchor.get("prefix", ""), anchor.get("suffix", "")
        prefix = prefix if isinstance(prefix, str) else ""
        suffix = suffix if isinstance(suffix, str) else ""
        exact = exact if isinstance(exact, str) else ""
        matches, cursor = [], 0
        if isinstance(exact, str) and exact:
            while (at := text.find(exact, cursor)) >= 0:
                if (not prefix or text[:at].endswith(prefix)) and (
                    not suffix or text[at + len(exact) :].startswith(suffix)
                ):
                    matches.append(at)
                cursor = at + 1
        start = end = None
        status = "missing" if not matches else "ambiguous"
        focus = field("focus") or exact
        insertion = issue.get("insertion_offset")
        if type(insertion) is not int or not 0 <= insertion <= len(exact):
            insertion = None
        reference = field("source_quote", "source_text", "source_reading", "original", "source")
        if not field("focus") and reference and exact and len(exact) <= 500:
            comparison = SequenceMatcher(None, exact, reference, autojunk=False)
            differences = [op for op in comparison.get_opcodes() if op[0] != "equal"]
            if len(differences) == 1 and comparison.ratio() >= 0.4:
                _, left, right, _, _ = differences[0]
                focus = exact[left:right]
                if left == right:
                    insertion = left
        if len(matches) == 1:
            relative = exact.find(focus)
            if focus and relative >= 0 and exact.find(focus, relative + 1) < 0:
                start = matches[0] + relative
                end = start + len(focus)
                status = "located"
            if insertion is not None:
                start = end = matches[0] + insertion
                status = "located"
                focus = ""
        boxes = issue.get("boxes") or ([issue["box"]] if issue.get("box") else [])
        boxes = (
            [
                b
                for b in boxes
                if isinstance(b, list)
                and len(b) == 4
                and all(type(v) in (int, float) and math.isfinite(v) and 0 <= v <= 1 for v in b)
                and b[0] < b[2]
                and b[1] < b[3]
            ]
            if isinstance(boxes, list)
            else []
        )
        precision = issue.get("image_precision", "region")
        # Model-supplied word boxes are proposals, never certified glyph geometry.
        if precision not in ("line", "region"):
            precision = "region"
        identity = hashlib.sha256(
            json.dumps([page, index, raw], ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()[:20]
        located.append(
            {
                "id": identity,
                "page": page,
                "text_sha256": text_hash,
                "image_sha256": image_sha256,
                "anchor": {"exact": exact or "", "prefix": prefix, "suffix": suffix},
                "focus": focus or "",
                "start": start,
                "end": end,
                "text_status": status,
                "boxes": boxes,
                "image_precision": precision if boxes else "page",
                "location": field("location"),
                "message": field("explanation", "issue", "description", "reason", "message"),
                "source_reading": field(
                    "source_quote", "source_text", "source_reading", "original", "source"
                ),
                "candidate": exact or "",
                "suggestion": field("suggestion"),
                **({"insertion_offset": insertion} if insertion is not None else {}),
            }
        )
    return located
