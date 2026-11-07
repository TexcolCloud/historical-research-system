"""Rank literal, source-bound directory targets without inventing approval evidence."""

import math
import re
import unicodedata
from difflib import SequenceMatcher

from hrs.processing.hashing import digest

DATE = re.compile(
    r"([0-9〇零一二三四五六七八九]{4})年\s*([0-9〇零一二三四五六七八九十]{1,3})月\s*([0-9〇零一二三四五六七八九十]{1,3})日"
)
DIGITS = dict(zip("〇零一二三四五六七八九", "00123456789", strict=True))


def verification_key(entry, candidates):
    """Freeze source and candidate identities, independently of mutable ranking scores."""
    return digest(
        [
            entry["id"],
            entry["source_sha256"],
            entry.get("source_image_sha256", ""),
            sorted(
                (c["physical_page"], c["anchor"], c["markdown_sha256"], c["image_sha256"])
                for c in candidates[:3]
            ),
        ]
    )


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
    source = doc["pages"][entry["source_page"] - 1].get("approved", {})
    if source.get("sha256") != entry["source_sha256"]:
        return False
    target = decision.get("target")
    if target is None:
        return True
    number = target.get("physical_page")
    if type(number) is not int or not 1 <= number <= len(doc["pages"]):
        return False
    page = doc["pages"][number - 1]
    return page.get("approved", {}).get("sha256") == target.get("markdown_sha256") and (
        not target.get("image_sha256") or page["image_sha256"] == target["image_sha256"]
    )


def verified_target(doc, entry, verification, candidates):
    """A machine receipt is valid only for the currently frozen candidate evidence."""
    if (
        not verification
        or verification.get("source_sha256") != entry["source_sha256"]
        or verification.get("verification_key") != verification_key(entry, candidates[:3])
        or verification.get("receipt", {}).get("approval_type") != "machine"
        or verification.get("receipt", {}).get("human_approval") is not False
    ):
        return None
    target = verification.get("target")
    if not target:
        return None
    number = target.get("physical_page")
    if type(number) is not int or not 1 <= number <= len(doc["pages"]):
        return None
    page = doc["pages"][number - 1]
    anchor = target.get("anchor")
    if (
        not anchor
        or page.get("approved", {}).get("sha256") != target.get("markdown_sha256")
        or page["image_sha256"] != target.get("image_sha256")
        or page["approved"]["markdown"].count(anchor) != 1
        or not any(c["physical_page"] == number and c["anchor"] == anchor for c in candidates[:3])
    ):
        return None
    return target_evidence(page, anchor)


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
    return catalog


def _context_terms(entry):
    value = entry["source_anchor"].replace(entry["title"], "", 1)
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
    source_dates = dates(entry["source_anchor"])
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
        candidates.append(
            {
                **target_evidence(page, anchor),
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
        and not date_conflict
        and (label or (not target.get("printed_label") and date_match)),
    )
    return target
