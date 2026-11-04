"""Source-bound directory navigation; no model inference or source-text rewriting."""

import hashlib
import json
import re
import unicodedata

KINDS = {"toc", "text", "frontmatter", "table", "figure", "unknown"}


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def normalized(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text)).casefold()


def label_range(label):
    match = re.fullmatch(
        r"\s*([\w一二三四五六七八九十百千〇零]+)\s*[—–－\-~～〜至]\s*([\w一二三四五六七八九十百千〇零]+)\s*",
        label,
    )
    return (match[1], match[2]) if match else (label, None)


def observation(page, value):
    if not isinstance(value, dict) or value.get("kind", "unknown") not in KINDS:
        raise ValueError("Invalid source page observation")
    text = page["approved"]["markdown"]
    label = value.get("printed_label")
    if label is not None and (
        not isinstance(label, str)
        or not label.strip()
        or len(label) > 40
        or normalized(label) not in normalized(text)
    ):
        raise ValueError("Printed label must be retained in the approved source text")
    headings = value.get("headings", [])
    if not isinstance(headings, list) or any(
        not isinstance(h, str) or not h or normalized(h) not in normalized(text) for h in headings
    ):
        raise ValueError("Heading observations must occur in approved source text")
    return {
        "kind": value.get("kind", "unknown"),
        "printed_label": label,
        "headings": headings,
        "sequence": value.get("sequence", "body"),
        "image_sha256": page["image_sha256"],
        "markdown_sha256": page["approved"]["sha256"],
    }


def toc_evidence(doc):
    return [
        {"page": p["page"], "sha256": p["approved"]["sha256"]}
        for p in doc["pages"]
        if p.get("approved") and p.get("observation", {}).get("kind", p.get("page_kind")) == "toc"
    ]


def validate_proposal(doc, entries):
    if not isinstance(entries, list):
        raise ValueError("Navigation requires an entries list")
    output, seen = [], set()
    for row in entries:
        source_page = row.get("source_page")
        if type(source_page) is not int or not 1 <= source_page <= len(doc["pages"]):
            raise ValueError("Directory source physical page is invalid")
        p = doc["pages"][source_page - 1]
        if (
            not p.get("approved")
            or p.get("observation", {}).get("kind", p.get("page_kind")) != "toc"
        ):
            raise ValueError("Directory entries require an approved directory source")
        anchor, title, label = (
            row.get("source_anchor", ""),
            row.get("title", ""),
            row.get("target_label", ""),
        )
        text = p["approved"]["markdown"]
        if not all(isinstance(value, str) for value in (anchor, title, label)):
            raise ValueError("Directory title, label and source anchor must be text")
        if (
            not anchor
            or text.count(anchor) != 1
            or not title
            or not label
            or normalized(title) not in normalized(anchor)
            or normalized(label) not in normalized(anchor)
        ):
            raise ValueError("Directory title and printed label require one unique source anchor")
        level = row.get("level", 1)
        end_label = row.get("target_end_label")
        if end_label is not None and (
            not isinstance(end_label, str)
            or not end_label
            or normalized(end_label) not in normalized(anchor)
        ):
            raise ValueError("Directory end label must occur in its retained source anchor")
        source_label = label
        label, range_end = label_range(label)
        end_label = end_label or range_end
        if type(level) is not int or not 1 <= level <= 8:
            raise ValueError("Directory levels must be between 1 and 8")
        identity = fingerprint([doc["source"]["sha256"], source_page, anchor])[:24]
        if identity in seen:
            raise ValueError("Repeated directory entry source")
        seen.add(identity)
        output.append(
            {
                "id": identity,
                "source_page": source_page,
                "source_anchor": anchor,
                "source_start": text.index(anchor),
                "source_sha256": p["approved"]["sha256"],
                "source_image_sha256": p["image_sha256"],
                "title": title,
                "target_label": label,
                "source_label": source_label,
                "target_end_label": end_label,
                "target_title": row.get("target_title") or title,
                "level": level,
                "sequence": row.get("sequence"),
                "parent_id": row.get("parent_id"),
                "title_relation": row.get("title_relation", "same source title"),
            }
        )
    # Physical/source order, rather than printed page arithmetic, defines the directory tree.
    output.sort(key=lambda x: (x["source_page"], x["source_start"]))
    parents = []
    for row in output:
        while parents and parents[-1]["level"] >= row["level"]:
            parents.pop()
        row["parent_id"] = parents[-1]["id"] if parents else None
        row["path"] = [p["title"] for p in parents] + [row["title"]]
        parents.append(row)
    return output


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


def target_position(entry):
    target = entry["target"]
    return target["physical_page"], target.get("ownership_start", target["start"])


def resolve_navigation(doc):
    prior = doc.get("navigation") or {}
    entries = []
    evidence = [
        {"page": p["page"], "sha256": p["approved"]["sha256"], "observation": p.get("observation")}
        for p in doc["pages"]
        if p.get("approved")
    ]
    labels = [
        {"physical_page": p["page"], **p["observation"]}
        for p in doc["pages"]
        if p.get("approved") and p.get("observation") and p["observation"].get("printed_label")
    ]
    for source in prior.get("source_entries", []):
        row = dict(source)
        start_label, end_label = label_range(row["target_label"])
        if end_label:
            row.update(
                source_label=row["target_label"],
                target_label=start_label,
                target_end_label=end_label,
            )
        candidates, alternatives = [], []
        for p in doc["pages"]:
            obs = p.get("observation", {})
            if not p.get("approved") or not obs.get("printed_label") or obs.get("kind") == "toc":
                continue
            if row.get("sequence") and obs.get("sequence") != row["sequence"]:
                continue
            text = p["approved"]["markdown"]
            anchors = [
                h
                for h in obs.get("headings", [])
                if normalized(h) == normalized(row["target_title"]) and text.count(h) == 1
            ]
            for anchor in anchors:
                target = target_evidence(p, anchor)
                (
                    candidates
                    if normalized(obs["printed_label"]) == normalized(row["target_label"])
                    else alternatives
                ).append(target)
        row.update(
            candidates=candidates or alternatives,
            target=None,
            status="matched"
            if len(candidates) == 1
            else "conflict"
            if len(candidates) > 1 or alternatives
            else "waiting_evidence",
        )
        if len(candidates) == 1:
            row["target"] = candidates[0]
        row["reason"] = (
            "页码与正文标题一致"
            if row["status"] == "matched"
            else "存在重复目标或目录页码与正文不一致"
            if row["status"] == "conflict"
            else "目标页面尚未形成完整核验证据"
        )
        current_source = doc["pages"][row["source_page"] - 1].get("approved", {})
        if current_source.get("sha256") != row["source_sha256"]:
            row.update(
                status="waiting_evidence", target=None, reason="目录源版本已变化，等待重新关联"
            )
            entries.append(row)
            continue
        decision = prior.get("decisions", {}).get(row["id"])
        if decision:
            p = (
                doc["pages"][decision["target"]["physical_page"] - 1]
                if decision.get("target")
                else None
            )
            if decision.get("source_sha256") == row["source_sha256"] and (
                p is None
                or p.get("approved", {}).get("sha256") == decision["target"]["markdown_sha256"]
            ):
                row.update(
                    status="matched_manual" if p else "unresolved",
                    target=decision.get("target"),
                    decision=decision,
                    reason="用户确认的导航位置，不代表新增正文审批"
                    if p
                    else "用户选择暂不关联，原目录保留",
                )
        if row["status"] == "waiting_evidence" and all(p.get("approved") for p in doc["pages"]):
            row["status"] = "not_found"
            row["reason"] = "本文件全部页面已核验，未找到页码与标题一致的目标"
        entries.append(row)
    result = {
        **prior,
        "entries": entries,
        "page_labels": labels,
        "evidence_sha256": fingerprint(evidence),
        "state": "available"
        if entries
        else "waiting_evidence"
        if toc_evidence(doc)
        else "not_detected",
        "coverage_complete": all(p.get("approved") for p in doc["pages"]),
        "page_kinds": {
            str(p["page"]): p.get("observation", {}).get("kind", p.get("page_kind", "unknown"))
            for p in doc["pages"]
            if p.get("approved")
        },
        "policy": "source-bound-navigation-v2-literal-ranges",
    }
    identity = fingerprint({k: v for k, v in result.items() if k not in {"version", "sha256"}})
    result["version"] = prior.get("version", 0) + (identity != prior.get("sha256"))
    result["sha256"] = identity
    return result


def fragment_navigation(navigation, pages, spans=None):
    """Metadata only: ownership never changes canonical text or character offsets."""
    items = [e for e in navigation.get("entries", []) if e.get("target")]
    applicable = []
    # Page-local character bounds disambiguate multiple headings on the same page.
    # Without bounds, retain only entries owning the whole requested page range.
    spans = spans or [(p, 0, float("inf")) for p in pages]
    for entry in items:
        index = navigation.get("entries", []).index(entry)
        next_boundary = next(
            (e for e in navigation.get("entries", [])[index + 1 :] if e["level"] <= entry["level"]),
            None,
        )
        if next_boundary and not next_boundary.get("target"):
            # An unresolved intervening chapter cannot be silently owned by its predecessor.
            continue
        start = target_position(entry)
        ends = [
            target_position(e)
            for e in items
            if e["level"] <= entry["level"] and target_position(e) > start
        ]
        end = min(ends) if ends else (float("inf"), 0)
        if spans and all(start <= (p, a) and (p, b) <= end for p, a, b in spans):
            applicable.append(entry)
    leaf = max(
        applicable,
        key=lambda e: (e["level"], e["target"]["physical_page"], e["target"]["start"]),
        default=None,
    )
    return {
        "toc_path": leaf["path"] if leaf else [],
        "toc_entry_id": leaf["id"] if leaf else None,
        "doc_kind": "navigation"
        if pages and all(navigation.get("page_kinds", {}).get(str(p)) == "toc" for p in pages)
        else "content",
        "navigation_version": navigation.get("version", 0),
        "printed_pages": [
            {
                "physical_page": p["physical_page"],
                "label": p["printed_label"],
                "sequence": p.get("sequence", "body"),
            }
            for p in navigation.get("page_labels", [])
            if p["physical_page"] in pages
        ],
    }
