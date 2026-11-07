"""Source-bound directory navigation; no model inference or source-text rewriting."""

import json
import re
import unicodedata

from hrs.processing.hashing import digest
from hrs.processing.navigation_coverage import coverage_report
from hrs.processing.navigation_matching import (
    candidates_for,
    page_mappings,
    retained_decision,
    target_catalog,
    verified_target,
)
from hrs.processing.navigation_matching import (
    target_evidence as target_evidence,
)
from hrs.processing.navigation_spans import (
    source_spans,
    spans_current,
    spans_sha256,
    validated_spans,
)
from hrs.processing.page_observation import observation as observation

NAVIGATION_REVIEW_POLICY = "navigation-confirmation-v1"


def navigation_requires_confirmation(entry):
    """Unresolved links need a decision; automatic evidence collection does not."""
    return entry.get("status") in {"conflict", "not_found", "unresolved"}


def navigation_review_summary(navigation):
    """Derive queue metadata without changing the source-bound navigation hash."""
    entries = [
        {**entry, "needs_confirmation": navigation_requires_confirmation(entry)}
        for entry in navigation.get("entries", [])
    ]
    return {
        **navigation,
        "entries": entries,
        "pending_count": sum(entry["needs_confirmation"] for entry in entries),
    }


def fingerprint(value):
    return digest(json.dumps(value, ensure_ascii=False, sort_keys=True))


def normalized(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text)).casefold()


def label_range(label):
    match = re.fullmatch(
        r"\s*([\w一二三四五六七八九十百千〇零]+)\s*[—–－\-~～〜至]\s*([\w一二三四五六七八九十百千〇零]+)\s*",
        label,
    )
    return (match[1], match[2]) if match else (label, None)


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
        supplied = row.get("source_spans")
        spans = validated_spans(
            doc,
            supplied
            if supplied is not None
            else [{"page": row.get("source_page"), "anchor": row.get("source_anchor", "")}],
            toc=True,
            boundary=True,
        )
        first = spans[0]
        source_page, anchor = first["page"], first["anchor"]
        p = doc["pages"][source_page - 1]
        title, label = (
            row.get("title", ""),
            row.get("target_label", ""),
        )
        text = p["approved"]["markdown"]
        combined = "\n".join(span["anchor"] for span in spans)
        if not all(isinstance(value, str) for value in (anchor, title, label)):
            raise ValueError("Directory title, label and source anchor must be text")
        if (
            not anchor
            or text.count(anchor) != 1
            or not title
            or not label
            or normalized(title) not in normalized(combined)
            or normalized(label) not in normalized(combined)
        ):
            raise ValueError("Directory title and printed label require one unique source anchor")
        level = row.get("level", 1)
        end_label = row.get("target_end_label")
        if end_label is not None and (
            not isinstance(end_label, str)
            or not end_label
            or normalized(end_label) not in normalized(combined)
        ):
            raise ValueError("Directory end label must occur in its retained source anchor")
        source_label = label
        label, range_end = label_range(label)
        end_label = end_label or range_end
        if type(level) is not int or not 1 <= level <= 8:
            raise ValueError("Directory levels must be between 1 and 8")
        identity = fingerprint([doc["source"]["sha256"], source_page, anchor])[:24]
        previous = _retained_source_identity(doc, spans, title, label)
        if previous:
            identity = previous["id"]
            # Keep the exact old anchor used by a retained human receipt when it remains current.
            anchor = previous["source_anchor"]
            retained = source_spans(previous)
            retained_text = "\n".join(span["anchor"] for span in retained)
            if (
                spans_current(doc, retained)
                and normalized(title) in normalized(retained_text)
                and normalized(source_label) in normalized(retained_text)
            ):
                spans = validated_spans(doc, retained, toc=True, boundary=True)
        elif len(spans) > 1 and identity in (doc.get("navigation") or {}).get("decisions", {}):
            # A fuller logical source must not silently extend an earlier human approval scope.
            identity = fingerprint(
                [
                    doc["source"]["sha256"],
                    "source-spans-v1",
                    [(span["page"], span["anchor"]) for span in spans],
                ]
            )[:24]
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
                "source_spans": spans,
                "source_spans_sha256": spans_sha256(spans),
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


def _retained_source_identity(doc, spans, title, label):
    first = spans[0]
    text = doc["pages"][first["page"] - 1]["approved"]["markdown"]
    matches = []
    for previous in (doc.get("navigation") or {}).get("source_entries", []):
        anchor = previous.get("source_anchor", "")
        old_title = normalized(previous.get("title", ""))
        if (
            previous.get("source_page") != first["page"]
            or previous.get("source_sha256") != first["markdown_sha256"]
            or not anchor
            or text.count(anchor) != 1
            or previous.get("target_label") != label
            or not old_title
            or not (
                normalized(title).startswith(old_title) or old_title.startswith(normalized(title))
            )
        ):
            continue
        if previous["id"] in (doc.get("navigation") or {}).get("decisions", {}):
            retained_text = "\n".join(span["anchor"] for span in source_spans(previous))
            if normalized(title) not in normalized(retained_text) or normalized(
                label
            ) not in normalized(retained_text):
                continue
        start = text.index(anchor)
        if max(start, first["start"]) < min(start + len(anchor), first["end"]):
            matches.append(previous)
    return matches[0] if len(matches) == 1 else None


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
    mappings = page_mappings(doc)
    catalog = target_catalog(doc)
    for source in prior.get("source_entries", []):
        row = dict(source)
        start_label, end_label = label_range(row["target_label"])
        if end_label:
            row.update(
                source_label=row["target_label"],
                target_label=start_label,
                target_end_label=end_label,
            )
        candidates, target = candidates_for(
            doc,
            row,
            mappings,
            prior.get("supplemental_candidates", {}).get(row["id"], []),
            catalog=catalog,
        )
        row.update(
            candidates=candidates,
            target=target,
            status="matched" if target else "conflict" if candidates else "waiting_evidence",
            match_method=(target or (candidates[0] if candidates else {})).get("match_method"),
            group_id=(target or (candidates[0] if candidates else {})).get("group_id"),
        )
        row["reason"] = (
            "正文标题与页码或独立日期证据一致"
            if row["status"] == "matched"
            else "已按标题、日期、分段页码与校准证据推荐候选，仍需确认"
            if row["status"] == "conflict"
            else "目标页面尚未形成完整核验证据"
        )
        if not spans_current(doc, source_spans(row)):
            row.update(
                status="waiting_evidence", target=None, reason="目录源版本已变化，等待重新关联"
            )
            entries.append(row)
            continue
        verification = prior.get("link_verifications", {}).get(row["id"])
        if target := verified_target(doc, row, verification, candidates):
            row.update(
                status="matched_verified",
                target=target,
                match_method="original_review",
                verification=verification,
                reason="独立原图核验已确认目录与正文的对应位置",
            )
        decision = prior.get("decisions", {}).get(row["id"])
        if retained_decision(doc, row, decision):
            row.update(
                status="matched_manual"
                if decision.get("target")
                else "unlinked_manual"
                if decision.get("final")
                else "unresolved",
                target=decision.get("target"),
                decision=decision,
                reason="用户确认的导航位置，不代表新增正文审批"
                if decision.get("target")
                else "用户确认不建立关联，原目录保留"
                if decision.get("final")
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
        "page_mappings": mappings,
        "evidence_sha256": fingerprint(evidence),
        "state": "available"
        if entries
        else "waiting_evidence"
        if toc_evidence(doc)
        else "not_detected",
        **coverage_report(doc),
        "page_kinds": {
            str(p["page"]): p.get("observation", {}).get("kind", p.get("page_kind", "unknown"))
            for p in doc["pages"]
            if p.get("approved")
        },
        "policy": "source-bound-navigation-v3-evidence-ranked",
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
