"""Per-directory-page coverage; page approval alone never proves a parsed directory."""

import json
import re
from copy import deepcopy

from hrs.processing.hashing import digest
from hrs.processing.navigation_spans import is_page_folio, source_spans, spans_current
from hrs.processing.page_observation import normalized

STATUSES = {"entries", "continuation", "empty", "not_directory", "unresolved"}
TARGET_LABEL = re.compile(
    r"(?:\.{2,}|[·…⋯]+)[^\n]*?[（(]?[ \t]*(?P<start>[0-9IVXLCDMivxlcdm一二三四五六七八九十百〇零]+)(?:[ \t]*[—–－\-~～〜至][ \t]*(?P<end>[0-9IVXLCDMivxlcdm一二三四五六七八九十百〇零]+))?[ \t]*[）)]?[ \t]*(?:[·•⋅∙・.\-—–]+)?(?=[ \t]*(?:\r?$|\n))"
)


class CoverageError(ValueError):
    """A rejected marker retains a precise literal source location for normal correction."""

    code = "navigation_coverage_incomplete"

    def __init__(self, message, page, match):
        text = page["approved"]["markdown"]
        start, end = match.start(), match.start() + len(match.group().rstrip())
        line_end = text.find("\n", end)
        self.details = {
            "page": page["page"],
            "start": start,
            "end": end,
            "label": match["start"] + ("—" + match["end"] if match["end"] else ""),
            "anchor": text[text.rfind("\n", 0, start) + 1 : line_end if line_end >= 0 else None],
        }
        super().__init__(message + ": " + json.dumps(self.details, ensure_ascii=False))


def visible_targets(page):
    """Find source markers while excluding only independently reviewed boundary folios."""
    text = page["approved"]["markdown"]
    matches = []
    for match in TARGET_LABEL.finditer(text):
        start = text.rfind("\n", 0, match.start()) + 1
        end = text.find("\n", match.start())
        if not is_page_folio(page, start, len(text) if end < 0 else end):
            matches.append(match)
    return matches


def marker_owners(doc, entries):
    """One logical source entry owns at most one visible marker, including its full range."""
    owners = {}
    for entry in entries:
        covered = []
        for span in source_spans(entry):
            page = doc["pages"][span["page"] - 1]
            for match in visible_targets(page):
                end = match.start() + len(match.group().rstrip())
                if span["start"] <= match.start() and span["end"] >= end:
                    covered.append((page, match))
        if len(covered) > 1:
            page, match = covered[1]
            raise CoverageError(
                "Directory source entry spans multiple visible target labels", page, match
            )
        for page, match in covered:
            if normalized(entry["target_label"]) != normalized(match["start"]) or normalized(
                entry.get("target_end_label") or ""
            ) != normalized(match["end"] or ""):
                raise CoverageError(
                    "Directory source entry does not own its visible target label", page, match
                )
            key = (page["page"], match.start())
            if key in owners:
                raise CoverageError(
                    "Directory entries repeat one visible target label", page, match
                )
            owners[key] = entry["id"]
    return owners


def page_kind(page):
    return page.get("observation", {}).get("kind", page.get("page_kind", "unknown"))


def toc_candidates(doc):
    """Unknown interior pages are inspection candidates, never inferred TOC approvals."""
    known = {p["page"] for p in doc["pages"] if page_kind(p) == "toc"}
    known.update(
        page["page"]
        for page in doc["pages"]
        if page.get("approved")
        and re.search(
            r"(?mi)^\s*(?:#{1,6}\s*)?(?:目[ \t　]*录|table of contents|contents)\s*$",
            page["approved"]["markdown"],
        )
    )
    for task in doc.get("tasks", []):
        value = (task.get("result") or {}).get("page_observation") or {}
        if isinstance(value, dict) and value.get("kind") == "toc" and task.get("page"):
            page = doc["pages"][task["page"] - 1]
            if task.get("inputs", {}).get("image_sha256") == page["image_sha256"]:
                known.add(page["page"])
    if known:
        first, last = min(known), max(known)
        known.update(
            page["page"]
            for page in doc["pages"]
            if first < page["page"] < last and page_kind(page) == "unknown"
        )
    return sorted(known)


def coverage_report(doc):
    nav = doc.get("navigation") or {}
    retained = nav.get("coverage_pages", {})
    candidates = toc_candidates(doc)
    rows, missing, unresolved = [], [], []
    for number in candidates:
        page = doc["pages"][number - 1]
        row = retained.get(str(number))
        current = bool(
            row
            and page.get("approved")
            and row.get("markdown_sha256") == page["approved"]["sha256"]
            and row.get("image_sha256") == page["image_sha256"]
        )
        if not current:
            missing.append(number)
            row = {
                "page": number,
                "status": "missing",
                "reason": "目录原页尚无当前版本的逐页解析覆盖记录",
            }
        elif row["status"] == "unresolved":
            if row.get("context_sha256") != context_sha256(doc, number):
                missing.append(number)
                row = {
                    "page": number,
                    "status": "missing",
                    "reason": "相邻原页的核验证据已更新，需要补充目录解析",
                }
            else:
                unresolved.append(number)
        rows.append({key: row[key] for key in ("page", "status", "reason") if key in row})
    complete = all(page.get("approved") for page in doc["pages"])
    return {
        "pages_complete": complete,
        "coverage_complete": complete and not missing and not unresolved,
        "toc_pages": [p["page"] for p in doc["pages"] if page_kind(p) == "toc"],
        "missing_pages": missing,
        "unresolved_pages": unresolved,
        "page_coverage": rows,
    }


def context_sha256(doc, number, frozen_pages=None):
    """A newly approved adjacent page permits one fresh pass, not an endless retry."""
    snapshot = {page["page"]: page for page in frozen_pages} if frozen_pages is not None else None
    rows = []
    for other in range(max(1, number - 1), min(len(doc["pages"]), number + 1) + 1):
        page = doc["pages"][other - 1]
        frozen = snapshot.get(other, {}) if snapshot is not None else None
        rows.append(
            {
                "page": other,
                "image_sha256": page["image_sha256"],
                "markdown_sha256": frozen.get("markdown_sha256")
                if frozen is not None
                else page.get("approved", {}).get("sha256"),
            }
        )
    return digest(rows)


def parse_plan(doc, *, retry_unresolved=False):
    report = coverage_report(doc)
    waiting = set(report["missing_pages"])
    if retry_unresolved:
        waiting.update(report["unresolved_pages"])
    waiting = sorted(number for number in waiting if doc["pages"][number - 1].get("approved"))
    core = [number for number in waiting if number <= waiting[0] + 2] if waiting else []
    scope = sorted(
        {
            number
            for page in core
            for number in range(max(1, page - 1), min(len(doc["pages"]), page + 1) + 1)
            if doc["pages"][number - 1].get("approved")
        }
    )
    return {
        "core": core,
        "context": [number for number in scope if number not in core],
        "pages": scope,
    }


def apply_coverage(doc, task, result, entries, receipt):
    """Validate the claimed coverage before merging any directory parsing result."""
    allowed = {p["page"] for p in task["inputs"]["pages"]}
    required = set(task["inputs"].get("toc_candidate_pages", []))
    bounded = task["inputs"].get("coverage_contract_version", 1) >= 2
    if not bounded:
        required.update(
            p["page"] for p in doc["pages"] if p["page"] in allowed and page_kind(p) == "toc"
        )
    spans = [span for entry in entries for span in source_spans(entry)]
    owners = marker_owners(doc, entries)
    if bounded:
        if not required or len(required) > 3 or not required <= allowed:
            raise ValueError("Directory parsing requires one bounded core page batch")
        if any(
            not any(span["page"] in required for span in source_spans(entry)) for entry in entries
        ):
            raise ValueError("Directory proposal does not touch the claimed core pages")
    else:
        required.update(span["page"] for span in spans)
    explicit = result.get("page_coverage", [])
    if not isinstance(explicit, list):
        raise ValueError("Directory page coverage must be a list")
    rows = {}
    for row in explicit:
        if not isinstance(row, dict) or type(row.get("page")) is not int:
            raise ValueError("Directory page coverage requires physical page numbers")
        if (
            row["page"] not in (required if bounded else allowed)
            or row["page"] in rows
            or row.get("status") not in STATUSES
        ):
            raise ValueError("Invalid or repeated directory page coverage")
        if row.get("status") != "entries" and (
            not isinstance(row.get("reason"), str) or not row["reason"].strip()
        ):
            raise ValueError("Directory page coverage disposition requires source evidence")
        rows[row["page"]] = dict(row)
        required.add(row["page"])
    for number in sorted(required):
        page = doc["pages"][number - 1]
        page_spans = [span for span in spans if span["page"] == number]
        row = rows.get(number) or ({"page": number, "status": "entries"} if page_spans else None)
        if row is None:
            raise ValueError(f"Directory page coverage missing for physical page {number}")
        if str(number) not in task.get("image_accesses", {}):
            raise ValueError("Directory page coverage requires original-image access")
        status = row["status"]
        kind = page_kind(page)
        if status == "not_directory" and kind in {"toc", "unknown"}:
            raise ValueError(
                "Non-directory coverage needs an explicit original-page classification"
            )
        if status in {"entries", "continuation", "empty"} and kind != "toc":
            raise ValueError("Directory coverage requires a verified directory page classification")
        if status in {"entries", "continuation"} and not page_spans:
            raise ValueError("Directory page coverage has no retained entry fragments")
        if status == "continuation" and not any(
            len(source_spans(entry)) > 1 and any(s["page"] == number for s in source_spans(entry))
            for entry in entries
        ):
            raise ValueError("Directory continuation coverage needs cross-page source fragments")
        labels = visible_targets(page)
        if status == "empty" and (labels or page_spans):
            raise ValueError("Directory page coverage cannot mark visible entries empty")
        if status in {"entries", "continuation"}:
            for match in labels:
                if (number, match.start()) not in owners:
                    raise CoverageError(
                        "Directory page coverage omits a visible target label", page, match
                    )
        rows[number] = {
            **row,
            "markdown_sha256": page["approved"]["sha256"],
            "image_sha256": page["image_sha256"],
            "task_id": task["task_id"],
            "receipt_sha256": digest(receipt),
            "context_sha256": task["inputs"].get("coverage_contexts", {}).get(str(number))
            or context_sha256(doc, number, task["inputs"]["pages"]),
        }
    nav = doc.get("navigation") or {}
    doc["navigation"] = nav
    coverage = nav.setdefault("coverage_pages", {})
    for number, row in rows.items():
        if previous := coverage.get(str(number)):
            if previous != row:
                nav.setdefault("coverage_history", []).append(deepcopy(previous))
        coverage[str(number)] = row
    nav["coverage_contract_version"] = max(
        nav.get("coverage_contract_version", 1), task["inputs"].get("coverage_contract_version", 1)
    )
    # Patch by source identity. A later partial parse never erases a prior human binding.
    merged = {entry["id"]: entry for entry in nav.get("source_entries", [])}
    for old in list(merged.values()):
        if old["id"] in nav.get("decisions", {}) or not spans_current(doc, source_spans(old)):
            continue
        aliases = [
            entry
            for entry in entries
            if entry["id"] != old["id"] and _completed_fragment_alias(old, entry)
        ]
        if len(aliases) == 1:
            replacement = aliases[0]
            nav.setdefault("source_entry_history", []).append(
                {
                    "entry": deepcopy(old),
                    "alias_of": replacement["id"],
                    "reason": "cross_page_title_completed",
                    "task_id": task["task_id"],
                }
            )
            nav.setdefault("source_entry_aliases", {})[old["id"]] = replacement["id"]
            merged.pop(old["id"])
    merged.update({entry["id"]: entry for entry in entries})
    ordered = sorted(
        merged.values(), key=lambda entry: (entry["source_page"], entry["source_start"])
    )
    parents = []
    for entry in ordered:
        while parents and parents[-1]["level"] >= entry["level"]:
            parents.pop()
        entry["parent_id"] = parents[-1]["id"] if parents else None
        entry["path"] = [parent["title"] for parent in parents] + [entry["title"]]
        parents.append(entry)
    nav["source_entries"] = ordered


def _completed_fragment_alias(old, new):
    if normalized(old["target_label"]) != normalized(new["target_label"]):
        return False
    previous, title = normalized(old["title"]), normalized(new["title"])
    if (
        not previous
        or len(previous) >= len(title)
        or not (title.startswith(previous) or title.endswith(previous))
    ):
        return False
    return any(
        left["page"] == right["page"]
        and left.get("start", -1) < right.get("end", -1)
        and right.get("start", -1) < left.get("end", -1)
        for left in source_spans(old)
        for right in source_spans(new)
    )
