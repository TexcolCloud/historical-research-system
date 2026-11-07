"""One bounded, original-first native Sol pass over ambiguous navigation links."""

import time
from copy import deepcopy

from hrs.processing.hashing import digest
from hrs.processing.navigation_matching import target_evidence
from hrs.processing.navigation_matching import verification_key as verification_key
from hrs.processing.workflow_rules import new_task

UNRESOLVED = {"conflict", "not_found", "unresolved"}


def reconcile_link_tasks(doc):
    """Discard obsolete unstarted work; preserve active leases and real retry budgets."""
    entries = {entry["id"]: entry for entry in (doc.get("navigation") or {}).get("entries", [])}
    for task in doc["tasks"]:
        if task["state"] != "ready" or _inputs(task).get("navigation_mode") != "verify_links":
            continue
        frozen = _inputs(task)["entries"]
        needed = [
            entry
            for row in frozen
            if (entry := entries.get(row["id"]))
            and entry["status"] in UNRESOLVED
            and entry.get("candidates")
        ]
        changed = any(
            verification_key(entry, entry["candidates"][:3])
            != next(row["verification_key"] for row in frozen if row["id"] == entry["id"])
            for entry in needed
        )
        if not needed or changed or (len(needed) != len(frozen) and not task.get("claim")):
            task.update(
                state="superseded",
                superseded_at=time.time(),
                superseded_reason="navigation_decision_or_evidence_changed",
            )


def prepare_link_verification(doc, *, requested=False):
    reconcile_link_tasks(doc)
    nav = doc.get("navigation") or {}
    if not requested and not nav.get(
        "coverage_complete", all(p.get("approved") for p in doc["pages"])
    ):
        return False
    if any(t["stage"] == "navigation" and t["state"] in {"ready", "leased"} for t in doc["tasks"]):
        return False
    if any(doc.get("run_request", {}).get(key) for key in ("pause_requested", "cancel_requested")):
        return False
    attempted = {
        row["verification_key"]
        for task in doc["tasks"]
        if _inputs(task).get("navigation_mode") == "verify_links"
        and (task["state"] != "superseded" or task.get("claim") or task.get("result") is not None)
        for row in _inputs(task)["entries"]
    }
    entries = []
    for entry in nav.get("entries", []):
        if entry["status"] not in UNRESOLVED:
            continue
        candidates = entry.get("candidates", [])[:3]
        if not candidates:
            continue
        key = verification_key(entry, candidates)
        if key not in attempted:
            entries.append({**deepcopy(entry), "candidates": candidates, "verification_key": key})
        if len(entries) == 3:
            break
    if not entries:
        return False
    numbers = sorted(
        {e["source_page"] for e in entries}
        | {c["physical_page"] for e in entries for c in e["candidates"]}
    )
    task = new_task("navigation")
    task["verification_inputs"] = {
        "document_id": doc["id"],
        "source_sha256": doc["source"]["sha256"],
        "stage": "navigation",
        "navigation_mode": "verify_links",
        "entries": entries,
        "pages": [
            {
                "page": number,
                "image_sha256": doc["pages"][number - 1]["image_sha256"],
                "markdown_sha256": doc["pages"][number - 1]["approved"]["sha256"],
            }
            for number in numbers
        ],
    }
    if prior := nav.get("receipt", {}).get("worker_id"):
        task["verification_inputs"]["prior_worker_id"] = prior
    doc["tasks"].append(task)
    return True


def _inputs(task):
    return task.get("inputs") or task.get("verification_inputs", {})


def _validate_pages(doc, task):
    for reference in _inputs(task)["pages"]:
        page = doc["pages"][reference["page"] - 1]
        if (
            page.get("approved", {}).get("sha256") != reference["markdown_sha256"]
            or page["image_sha256"] != reference["image_sha256"]
        ):
            raise ValueError("Navigation verification source changed")


def freeze_link_observations(doc, task, readings):
    """Freeze source pixels before releasing titles, candidate anchors or page text."""
    _validate_pages(doc, task)
    if not isinstance(readings, list):
        raise ValueError("Navigation original readings must cover every frozen page")
    expected = {p["page"] for p in _inputs(task)["pages"]}
    if len(readings) != len(expected) or {r.get("page") for r in readings} != expected:
        raise ValueError("Navigation original readings must cover every frozen page")
    for reading in readings:
        access = task.get("image_accesses", {}).get(str(reading["page"]))
        if (
            not access
            or reading.get("image_receipt") != access["receipt"]
            or reading.get("image_sha256") != access["image_sha256"]
        ):
            raise ValueError("Open every navigation original before freezing observations")
        observations, unresolved = reading.get("observations"), reading.get("unresolved", [])
        if (
            not isinstance(observations, list)
            or not observations
            or any(not isinstance(value, str) or not value.strip() for value in observations)
        ):
            raise ValueError("Navigation original observations must be nonempty text")
        if not isinstance(unresolved, list) or any(
            not isinstance(value, str) or not value.strip() for value in unresolved
        ):
            raise ValueError("Navigation unresolved observations must be text")
    if frozen := task.get("navigation_original_reading"):
        if frozen["readings"] != readings:
            raise ValueError("Navigation original observations are already frozen")
    else:
        task["navigation_original_reading"] = {
            "readings": deepcopy(readings),
            "sha256": digest(readings),
            "created_at": time.time(),
        }
    return {"entries": deepcopy(_inputs(task)["entries"])}


def apply_link_verifications(doc, task, result, receipt):
    _validate_pages(doc, task)
    reading = task.get("navigation_original_reading")
    if not reading or digest(reading["readings"]) != reading["sha256"]:
        raise ValueError("Navigation verification requires frozen original observations")
    entries = {e["id"]: e for e in _inputs(task)["entries"]}
    links = result.get("links")
    if (
        not isinstance(links, list)
        or len(links) != len(entries)
        or {row.get("entry_id") for row in links} != entries.keys()
    ):
        raise ValueError("Navigation results must cover every claimed entry exactly once")
    unresolved = {r["page"] for r in reading["readings"] if r.get("unresolved")}
    decisions = {}
    for row in links:
        entry = entries[row["entry_id"]]
        target = None
        if row.get("target_page") is not None:
            candidate = next(
                (
                    c
                    for c in entry["candidates"]
                    if c["physical_page"] == row["target_page"]
                    and c["anchor"] == row.get("target_anchor")
                ),
                None,
            )
            if not candidate:
                raise ValueError("Navigation target must be one frozen candidate")
            if entry["source_page"] in unresolved or row["target_page"] in unresolved:
                raise ValueError("Navigation unresolved original observations block a link")
            page = doc["pages"][row["target_page"] - 1]
            if page["approved"]["markdown"].count(row["target_anchor"]) != 1:
                raise ValueError("Navigation candidate must retain one unique source anchor")
            target = target_evidence(page, row["target_anchor"])
        decisions[entry["id"]] = {
            "source_sha256": entry["source_sha256"],
            "verification_key": entry["verification_key"],
            "target": target,
            "receipt": {
                **receipt,
                "approval_type": "machine",
                "human_approval": False,
                "original_reading_sha256": reading["sha256"],
            },
        }
    doc["navigation"].setdefault("link_verifications", {}).update(decisions)


def verify_link_readings(task):
    """Retired leases retain verifiable observations without granting current access."""
    records = task.get("navigation_reading_history", []) + [
        {
            "reading": task.get("navigation_original_reading"),
            "image_accesses": task.get("image_accesses", {}),
        }
    ]
    for record in records:
        reading = record.get("reading")
        if not reading:
            continue
        if digest(reading["readings"]) != reading["sha256"]:
            raise ValueError("Navigation original observation history hash mismatch")
        for row in reading["readings"]:
            access = record["image_accesses"].get(str(row["page"]), {})
            if row["image_sha256"] != access.get("image_sha256") or row[
                "image_receipt"
            ] != access.get("receipt"):
                raise ValueError("Navigation original observation history receipt mismatch")
