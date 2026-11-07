"""Rank literal, source-bound directory targets without inventing approval evidence."""

import math
import re
import unicodedata
from difflib import SequenceMatcher

from hrs.processing.hashing import digest
from hrs.processing.navigation_spans import (
    source_spans,
    source_text,
    spans_current,
    spans_sha256,
    target_spans,
    validated_spans,
)

DATE = re.compile(
    r"([0-9〇零一二三四五六七八九]{4})年\s*([0-9〇零一二三四五六七八九十]{1,3})月\s*([0-9〇零一二三四五六七八九十]{1,3})日"
)
DIGITS = dict(zip("〇零一二三四五六七八九", "00123456789", strict=True))


def verification_key(entry, candidates):
    """Freeze source and candidate identities, independently of mutable ranking scores."""
    identity = [
        entry["id"],
        entry["source_sha256"],
        entry.get("source_image_sha256", ""),
        sorted(
            (c["physical_page"], c["anchor"], c["markdown_sha256"], c["image_sha256"])
            for c in candidates[:3]
        ),
    ]
    # Retained single-page receipts keep their original identity and remain readable.
    if len(source_spans(entry)) > 1 or any(len(target_spans(c)) > 1 for c in candidates[:3]):
        identity.append(
            [
                source_spans(entry),
                sorted(
                    (c["physical_page"], c["anchor"], spans_sha256(target_spans(c)))
                    for c in candidates[:3]
                ),
            ]
        )
    return digest(identity)


def comparison_text(value):
    """Search-only normalization; stored titles and target anchors remain literal."""
    return "".join(
        c
        for c in unicodedata.normalize("NFKC", value).casefold()
        if not c.isspace() and not unicodedata.category(c).startswith("P")
    )


def page_number(value):
    value = unicodedata.normalize("NFKC", value or "").strip()
    value = re.sub(r"^第|[页頁]$", "", value)
    if value.isdecimal():
        return int(value)
    if value and all(c in DIGITS for c in value):
        return int("".join(DIGITS[c] for c in value))
    units = {"十": 10, "百": 100, "千": 1000}
    if value and all(c in DIGITS or c in units for c in value):
        total, current = 0, 0
        for char in value:
            if char in units:
                total += (current or 1) * units[char]
                current = 0
            else:
                current = int(DIGITS[char])
        return total + current
    return None


def dates(value):
    return {
        tuple(page_number(part) for part in match)
        for match in DATE.findall(unicodedata.normalize("NFKC", value))
    }


def same_label(left, right):
    if not left or not right:
        return False
    a, b = page_number(left), page_number(right)
    return (
        a == b
        if a is not None and b is not None
        else comparison_text(left) == comparison_text(right)
    )


def target_evidence(page, anchor):
    text = page["approved"]["markdown"]
    start = text.index(anchor)
    line_start = text.rfind("\n", 0, start) + 1
    ownership_start = (
        line_start if re.fullmatch(r" {0,3}#{1,6}\s+| {0,3}\*\*", text[line_start:start]) else start
    )
    obs = page.get("observation", {})
    return {
        "physical_page": page["page"],
        "printed_label": obs.get("printed_label"),
        "sequence": obs.get("sequence", "body"),
        "anchor": anchor,
        "start": start,
        "ownership_start": ownership_start,
        "markdown_sha256": page["approved"]["sha256"],
        "image_sha256": page["image_sha256"],
    }


def retained_decision(doc, entry, decision):
    if not decision or decision.get("source_sha256") != entry["source_sha256"]:
        return False
    if not spans_current(doc, source_spans(entry)) or (
        decision.get("source_spans_sha256")
        and decision["source_spans_sha256"] != spans_sha256(source_spans(entry))
    ):
        return False
    target = decision.get("target")
    if target is None:
        return True
    number = target.get("physical_page")
    if type(number) is not int or not 1 <= number <= len(doc["pages"]):
        return False
    return spans_current(doc, target_spans(target))


def candidate_evidence(doc, target):
    """Retain a cross-page logical title while rechecking every literal fragment."""
    number, anchor = target.get("physical_page"), target.get("anchor")
    if type(number) is not int or not 1 <= number <= len(doc["pages"]):
        raise ValueError("Navigation target physical page is invalid")
    if not spans_current(doc, target_spans(target)):
        raise ValueError("Navigation target source changed")
    result = target_evidence(doc["pages"][number - 1], anchor)
    if target.get("title_spans"):
        spans = validated_spans(doc, target["title_spans"], boundary=True)
        if spans[0]["page"] != number or spans[0]["anchor"] != anchor:
            raise ValueError("Navigation target must start at its first title fragment")
        title = target.get("title", "")
        if not title or comparison_text(title) != comparison_text(
            "".join(s["anchor"] for s in spans)
        ):
            raise ValueError("Navigation logical title must match its literal fragments")
        result.update(title=title, title_spans=spans)
    return result


def verified_target(doc, entry, verification, candidates):
    """Retain independent original evidence when candidate ranking changes around it."""
    if (
        not verification
        or verification.get("source_sha256") != entry["source_sha256"]
        or verification.get("receipt", {}).get("approval_type") != "machine"
        or verification.get("receipt", {}).get("human_approval") is not False
        or not _verified_source_current(doc, entry, verification, candidates)
    ):
        return None
    target = verification.get("target")
    if not target:
        return None
    number = target.get("physical_page")
    if type(number) is not int or not 1 <= number <= len(doc["pages"]):
        return None
    anchor = target.get("anchor")
    if (
        not anchor
        or not spans_current(doc, source_spans(entry))
        or not spans_current(doc, target_spans(target))
    ):
        return None
    try:
        return candidate_evidence(doc, target)
    except ValueError:
        return None


def _verified_source_current(doc, entry, verification, candidates):
    """New receipts keep literal source scope; old receipts recover it from their frozen task."""
    current = source_spans(entry)
    frozen = verification.get("source_spans")
    if frozen is None:
        for task in doc.get("tasks", []):
            inputs = task.get("inputs") or task.get("verification_inputs", {})
            if task.get("state") != "completed" or inputs.get("navigation_mode") != "verify_links":
                continue
            row = next(
                (
                    e
                    for e in inputs.get("entries", [])
                    if e["id"] == entry["id"]
                    and e.get("verification_key") == verification.get("verification_key")
                ),
                None,
            )
            if row is not None:
                frozen = source_spans(row)
                break
    if frozen is None:
        # Read compatibility for older minimal records without a retained task scope.
        return verification.get("verification_key") == verification_key(entry, candidates[:3])
    return (
        spans_current(doc, frozen)
        and spans_sha256(frozen) == spans_sha256(current)
        and (
            not verification.get("source_spans_sha256")
            or verification["source_spans_sha256"] == spans_sha256(frozen)
        )
    )


def page_mappings(doc):
    """Bound arithmetic to observed equal-offset runs; never extrapolate over a break."""
    prior = doc.get("navigation") or {}
    manual = {
        decision["target"]["physical_page"]
        for entry in prior.get("source_entries", [])
        if (decision := prior.get("decisions", {}).get(entry["id"]))
        and decision.get("target")
        and retained_decision(doc, entry, decision)
    }
    mappings, previous = [], None
    for page in doc["pages"]:
        obs = page.get("observation", {})
        if not page.get("approved") or obs.get("kind") == "toc":
            continue
        label = page_number(obs.get("printed_label"))
        if label is None:
            continue
        sequence = obs.get("sequence", "body")
        offset = page["page"] - label
        if (
            previous
            and previous["sequence"] == sequence
            and previous["offset"] == offset
            and label > previous["printed_end"]
        ):
            previous.update(physical_end=page["page"], printed_end=label)
            previous["anchor_count"] += 1
            previous["manual_anchor_count"] += int(page["page"] in manual)
        else:
            previous = {
                "id": digest([sequence, page["page"], label])[:16],
                "sequence": sequence,
                "physical_start": page["page"],
                "physical_end": page["page"],
                "printed_start": label,
                "printed_end": label,
                "offset": offset,
                "anchor_count": 1,
                "manual_anchor_count": int(page["page"] in manual),
            }
            mappings.append(previous)
    return mappings


def headings(page):
    text = page["approved"]["markdown"]
    observed = page.get("observation", {}).get("headings", [])
    explicit = re.findall(r"(?m)^ {0,3}#{1,6}\s+(.+?)\s*#*\s*$", text)
    # A missing structure observation must not make already approved titles unsearchable.
    opening = next((line.strip() for line in text.splitlines() if line.strip()), "")
    values = [*observed, *explicit]
    for value in observed:
        if text.count(value) == 1:
            continue
        expression = r"\s*".join(re.escape(c) for c in value if not c.isspace())
        matches = list(re.finditer(expression, text)) if expression else []
        if len(matches) == 1:
            values.append(matches[0].group())
    if not values and 1 < len(opening) <= 180 and not opening.startswith(("![", "[^", "|")):
        values.append(opening)
    return sorted(
        {h for h in values if h and text.count(h) == 1},
        key=lambda h: (text.index(h), len(h), h),
    )


def target_catalog(doc):
    """Read each approved title/opening once per document refresh, shared by all entries."""
    catalog = []
    for page in doc["pages"]:
        if not page.get("approved") or page.get("observation", {}).get("kind") == "toc":
            continue
        text = page["approved"]["markdown"]
        anchors = headings(page)
        starts = sorted({text.index(anchor) for anchor in anchors})
        for anchor in anchors:
            start = text.index(anchor)
            end = next((position for position in starts if position > start), len(text))
            opening = text[start : min(start + 500, end)]
            catalog.append(
                {
                    "target": target_evidence(page, anchor),
                    "title": comparison_text(anchor),
                    "dates": dates(opening),
                    "opening": comparison_text(opening),
                }
            )
    catalog.extend(_cross_page_titles(doc, catalog))
    return catalog


def _logical_titles(doc):
    """Only retained TOC/organization titles may guide a physical-page continuation."""
    values = {}
    for entry in (doc.get("navigation") or {}).get("source_entries", []):
        title = entry.get("target_title") or entry.get("title", "")
        if 4 <= len(comparison_text(title)) <= 240 and spans_current(doc, source_spans(entry)):
            values.setdefault(comparison_text(title), title)
    organization = doc.get("organization") or {}
    for row in organization.get("articles", []) + organization.get("reading_structure", {}).get(
        "titles", []
    ):
        title = row.get("title", "")
        if 4 <= len(comparison_text(title)) <= 240:
            values.setdefault(comparison_text(title), title)
    return values


def _cross_page_titles(doc, catalog):
    """Exact known titles may span page boundaries; suggestions still need native original review."""
    titles = _logical_titles(doc)
    if not titles:
        return []
    records = [*catalog, *_edge_title_records(doc, titles)]
    by_page = {}
    for record in records:
        by_page.setdefault(record["target"]["physical_page"], []).append(record)
    found = {}
    for record in records:
        first = record["target"]
        prefix = comparison_text(first["anchor"])
        possible = [
            (normal, title)
            for normal, title in titles.items()
            if normal.startswith(prefix) and normal != prefix
        ]
        for normal, title in possible:
            parts = [{"page": first["physical_page"], "anchor": first["anchor"]}]
            combined = prefix
            for number in range(
                first["physical_page"] + 1, min(first["physical_page"] + 4, len(doc["pages"]) + 1)
            ):
                choices = []
                for following in by_page.get(number, []):
                    partial = comparison_text(following["target"]["anchor"])
                    if partial and normal.startswith(combined + partial):
                        proposed = [
                            *parts,
                            {"page": number, "anchor": following["target"]["anchor"]},
                        ]
                        try:
                            spans = validated_spans(doc, proposed, boundary=True)
                        except ValueError:
                            continue
                        choices.append((partial, proposed, spans))
                if not choices:
                    break
                # Heading markup or a wrapped heading may expose several aliases at one opening.
                # Prefer its longest exact literal prefix, never a later paragraph.
                partial, parts, spans = max(choices, key=lambda c: (len(c[0]), -c[2][-1]["start"]))
                combined += partial
                if combined == normal:
                    target = {**first, "title": title, "title_spans": spans}
                    opening = "\n".join(
                        doc["pages"][p["page"] - 1]["approved"]["markdown"][p["start"] :]
                        for p in spans
                    )
                    found[(first["physical_page"], first["start"], normal)] = {
                        "target": target,
                        "title": normal,
                        "dates": dates(opening[:800]),
                        "opening": comparison_text(opening[:800]),
                    }
                    break
    return list(found.values())


def _edge_title_records(doc, titles):
    """Recover plain title fragments only where a retained full title matches a page edge."""
    records = []
    for page in doc["pages"]:
        if not page.get("approved") or page.get("observation", {}).get("kind") == "toc":
            continue
        text = page["approved"]["markdown"]
        lines = [
            match
            for match in re.finditer(r"(?m)^.*\S.*$", text)
            if not re.fullmatch(r"\s*\d{1,5}\s*", match.group())
        ]
        for edge in (lines[:4], lines[-4:]):
            for left in range(len(edge)):
                for right in range(left + 1, len(edge) + 1):
                    anchor = text[edge[left].start() : edge[right - 1].end()].strip()
                    anchor = re.sub(r"^ {0,3}#{1,6}[ \t]+", "", anchor)
                    if anchor.startswith(("**", "__")):
                        anchor = anchor[2:]
                    if anchor.endswith(("**", "__")):
                        anchor = anchor[:-2]
                    partial = comparison_text(anchor)
                    if not 1 <= len(partial) <= 240 or text.count(anchor) != 1:
                        continue
                    if any(
                        normal != partial
                        and (normal.startswith(partial) or normal.endswith(partial))
                        for normal in titles
                    ):
                        records.append({"target": target_evidence(page, anchor), "title": partial})
    return records


def _context_terms(entry):
    value = source_text(entry).replace(entry["title"], "", 1)
    value = DATE.sub("", value)
    if value.rstrip().endswith(entry["source_label"]):
        value = value.rstrip()[: -len(entry["source_label"])]
    return [
        term
        for part in re.split(r"[\s，,、；;：:（）()]+", value)
        if len(term := comparison_text(part)) >= 3 and not term.isdecimal()
    ]


def _ordered_neighbors(doc, entry):
    prior = doc.get("navigation") or {}
    rows = [
        row
        for row in prior.get("source_entries", [])
        if row.get("sequence") == entry.get("sequence")
    ]
    numbers = [page_number(row["target_label"]) for row in rows]
    if (
        len(rows) < 2
        or any(number is None for number in numbers)
        or any(left > right for left, right in zip(numbers, numbers[1:]))
    ):
        return []
    index = next(i for i, row in enumerate(rows) if row["id"] == entry["id"])
    return [
        (i < index, decision["target"])
        for i, row in enumerate(rows)
        if row["id"] != entry["id"]
        and (decision := prior.get("decisions", {}).get(row["id"]))
        and decision.get("target")
        and retained_decision(doc, row, decision)
    ]


def candidates_for(doc, entry, mappings, supplemental=(), *, catalog=None):
    """Return ranked literal targets and a conservative automatic target, if proven."""
    desired = comparison_text(entry["target_title"])
    source_dates = dates(source_text(entry))
    context = _context_terms(entry)
    neighbors = _ordered_neighbors(doc, entry)
    candidates = []
    records = [
        item
        for item in (target_catalog(doc) if catalog is None else catalog)
        if not entry.get("sequence") or item["target"]["sequence"] == entry["sequence"]
    ]
    for item in records:
        if item["title"] == desired:
            candidates.append(
                _rank_candidate(
                    entry,
                    item,
                    dict(item["target"]),
                    mappings,
                    neighbors,
                    context,
                    source_dates,
                    True,
                    1,
                )
            )
    # Exact, directly evidenced matches do not need a full fuzzy scan on every page commit.
    if not any(candidate["_automatic"] for candidate in candidates):
        for item in records:
            title = item["title"]
            if title == desired or min(len(desired), len(title)) < 4:
                continue
            similarity = SequenceMatcher(None, desired, title, autojunk=False).ratio()
            if similarity < 0.72:
                continue
            candidates.append(
                _rank_candidate(
                    entry,
                    item,
                    dict(item["target"]),
                    mappings,
                    neighbors,
                    context,
                    source_dates,
                    False,
                    similarity,
                )
            )
    for supplied in supplemental:
        number = supplied.get("physical_page")
        if type(number) is not int or not 1 <= number <= len(doc["pages"]):
            continue
        page = doc["pages"][number - 1]
        approved = page.get("approved", {})
        anchor = supplied.get("anchor")
        if (
            supplied.get("source_sha256") != entry["source_sha256"]
            or supplied.get("markdown_sha256") != approved.get("sha256")
            or supplied.get("image_sha256") != page["image_sha256"]
            or not anchor
            or approved.get("markdown", "").count(anchor) != 1
            or page.get("observation", {}).get("kind") == "toc"
            or (
                entry.get("sequence")
                and page.get("observation", {}).get("sequence", "body") != entry["sequence"]
            )
            or not spans_current(doc, target_spans(supplied))
        ):
            continue
        supplied_score = supplied.get("match_score", 0)
        supplied_score = (
            max(0, min(supplied_score, 1))
            if type(supplied_score) in {int, float} and math.isfinite(supplied_score)
            else 0
        )
        method = supplied.get("match_method", "suggested")
        signal = "语义检索推荐，需原图核对" if method == "semantic" else "PDF 书签推荐，需原图核对"
        existing = next(
            (c for c in candidates if c["physical_page"] == number and c["anchor"] == anchor), None
        )
        if existing:
            existing["match_score"] = round(
                min(1, existing["match_score"] + 0.04 * supplied_score), 4
            )
            existing["match_signals"].append(signal)
            continue
        try:
            evidence = candidate_evidence(doc, supplied)
        except ValueError:
            continue
        candidates.append(
            {
                **evidence,
                "match_score": round(0.3 + 0.2 * supplied_score, 4),
                "match_method": method,
                "match_signals": [signal],
                "_automatic": False,
            }
        )
    candidates = _merge_aliases(candidates)
    candidates.sort(key=lambda c: (-c["match_score"], c["physical_page"], c["start"]))
    automatic = [c for c in candidates if c.pop("_automatic")]
    target = automatic[0] if len(automatic) == 1 else None
    return candidates[:8], target


def _merge_aliases(candidates):
    """One physical article start is one target, even with several literal title aliases."""
    groups = {}
    for candidate in candidates:
        key = candidate["physical_page"], candidate.get("ownership_start", candidate["start"])
        groups.setdefault(key, []).append(candidate)
    result = []
    for group in groups.values():
        winner = min(
            group,
            key=lambda c: (
                not c["_automatic"],
                c.get("match_method") != "literal",
                -c["match_score"],
                c["anchor"].count("\\") + c["anchor"].count("*"),
                -len(c["anchor"]),
                c["anchor"],
            ),
        )
        winner = {
            **winner,
            "_automatic": any(c["_automatic"] for c in group),
            "match_score": max(c["match_score"] for c in group),
            "match_signals": list(
                dict.fromkeys(signal for c in group for signal in c["match_signals"])
            ),
        }
        if len(group) > 1:
            winner["match_signals"].append("同一正文位置的标题别名已合并")
        result.append(winner)
    return result


def _rank_candidate(
    entry, item, target, mappings, neighbors, context, source_dates, exact, similarity
):
    anchor = target["anchor"]
    label = same_label(target.get("printed_label"), entry["target_label"])
    target_dates = item["dates"]
    date_match = bool(source_dates and len(source_dates) == 1 and source_dates == target_dates)
    date_conflict = bool(source_dates and target_dates and not source_dates & target_dates)
    signals = [
        "标题完全一致"
        if anchor == entry["target_title"]
        else "标题仅标点或空白不同"
        if exact
        else "标题近似，需核对"
    ]
    if len(target_spans(target)) > 1:
        signals.append("跨页标题片段，需原图核对")
    score = 0.5 if exact else 0.35 * similarity
    if label:
        score += 0.3
        signals.append("书内页码一致")
    elif target.get("printed_label"):
        score -= 0.1
        signals.append(f"书内页码不同（正文 {target['printed_label']}）")
    if date_match:
        score += 0.15
        signals.append("日期一致")
    elif date_conflict:
        score -= 0.35
        signals.append("日期不同，需核对")
    for term in context:
        if term in item["opening"]:
            score += 0.03
            signals.append(f"正文开头含目录附加信息「{term}」")
    number = page_number(entry["target_label"])
    mapping = next(
        (
            m
            for m in mappings
            if m["sequence"] == target["sequence"]
            and number is not None
            and m["printed_start"] <= number <= m["printed_end"]
            and m["physical_start"] <= target["physical_page"] <= m["physical_end"]
            and number + m["offset"] == target["physical_page"]
        ),
        None,
    )
    if mapping:
        score += 0.04
        signals.append("本段页码映射一致")
    position = (target["physical_page"], target["start"])
    if neighbors:
        ordered = all(
            (position >= (other["physical_page"], other["start"]))
            if earlier
            else (position <= (other["physical_page"], other["start"]))
            for earlier, other in neighbors
        )
        score += 0.05 if ordered else -0.2
        signals.append("人工校准后的目录顺序一致" if ordered else "与人工校准的目录顺序不同")
    target.update(
        match_score=round(max(0, min(score, 1)), 4),
        match_signals=signals,
        match_method="literal"
        if anchor == entry["target_title"]
        else "layout"
        if exact
        else "suggested",
        group_id=mapping["id"] if mapping else None,
        # Internal proof flags are removed before crossing the navigation interface.
        _automatic=exact
        and len(target_spans(target)) == 1
        and not date_conflict
        and (label or (not target.get("printed_label") and date_match)),
    )
    return target
