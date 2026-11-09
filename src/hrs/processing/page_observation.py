"""Source-bound page structure, distinct from an original-reading receipt."""

import re
import time
import unicodedata
from copy import deepcopy

from hrs.processing.hashing import digest

KINDS = {"toc", "text", "frontmatter", "table", "figure", "unknown"}
FIELDS = ("kind", "printed_label", "headings", "sequence", "reason")
RUNNING_FIELDS = ("running_headers", "running_footers")
FIELDS += RUNNING_FIELDS


def normalized(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text)).casefold()


def running_line(text, value, field):
    """Running furniture must be a unique whole first/last nonempty source line."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return (
        bool(lines)
        and text.count(value) == 1
        and value.strip() == lines[0 if field == "running_headers" else -1]
    )


def structure(value):
    """Validate new writes without interpreting missing semantic fields as unknown."""
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("kind"), str)
        or value["kind"] not in KINDS
    ):
        raise ValueError("Page observation requires an explicit valid kind")
    if any(key in value for key in ("image_receipt", "observations", "source_reading_sha256")):
        raise ValueError("Page structure must not contain an original-reading receipt")
    label, headings = value.get("printed_label"), value.get("headings", [])
    if label is not None and (not isinstance(label, str) or not label.strip() or len(label) > 40):
        raise ValueError("Invalid printed page label")
    if not isinstance(headings, list) or any(
        not isinstance(heading, str) or not heading.strip() for heading in headings
    ):
        raise ValueError("Page structure headings must be nonempty text")
    sequence = value.get("sequence", "body")
    if not isinstance(sequence, str) or not sequence.strip() or len(sequence) > 100:
        raise ValueError("Page structure sequence must be source-supported text")
    reason = value.get("reason")
    if reason is not None and (not isinstance(reason, str) or not reason.strip()):
        raise ValueError("Page structure reason must be nonempty text")
    furniture = {}
    for field in RUNNING_FIELDS:
        if field not in value:
            continue
        items = value[field]
        if (
            not isinstance(items, list)
            or any(not isinstance(item, str) or not item.strip() for item in items)
            or len(items) != len(set(items))
        ):
            raise ValueError(f"{field} must contain distinct nonempty literal lines")
        furniture[field] = list(items)
    return {
        "kind": value["kind"],
        "printed_label": label,
        "headings": list(headings),
        "sequence": sequence,
        **({"reason": reason} if reason else {}),
        **furniture,
    }


def observation(page, value):
    value = structure(value)
    text = page["approved"]["markdown"]
    label = value["printed_label"]
    if label is not None and normalized(label) not in normalized(text):
        raise ValueError("Printed label must be retained in the approved source text")
    if any(normalized(heading) not in normalized(text) for heading in value["headings"]):
        raise ValueError("Heading observations must occur in approved source text")
    for field in RUNNING_FIELDS:
        if any(not running_line(text, line, field) for line in value.get(field, [])):
            raise ValueError(f"{field} requires a unique complete outer source line")
    return {
        **value,
        "image_sha256": page["image_sha256"],
        "markdown_sha256": page["approved"]["sha256"],
    }


def retain_proposal(page, task, result, receipt):
    """Keep a reviewed classification through corrections; it grants no text approval."""
    if "page_observation" not in result:
        return
    raw = result["page_observation"]
    previous = page.get("structure_proposal")
    value = structure(raw)
    if previous and previous["image_sha256"] == page["image_sha256"]:
        if (
            value["kind"] == "unknown"
            and previous["value"]["kind"] != "unknown"
            and not value.get("reason")
        ):
            raise ValueError("Replacing known page structure with unknown requires source evidence")
        value = structure({**previous["value"], **{key: raw[key] for key in FIELDS if key in raw}})
    proposal = {
        "value": value,
        "image_sha256": page["image_sha256"],
        "source_task_id": task["task_id"],
        "source_result_sha256": digest(result),
        "source_reading_sha256": receipt.get("source_reading_sha256"),
        "created_at": receipt["created_at"],
    }
    if previous:
        page.setdefault("structure_proposal_history", []).append(previous)
    page["structure_proposal"] = proposal


def bind_proposal(page):
    proposal = page.get("structure_proposal")
    if not proposal or proposal["image_sha256"] != page["image_sha256"]:
        return
    bound = observation(page, proposal["value"])
    page["observation"] = bound
    page["page_kind"] = bound["kind"]


def _rebind(page, value):
    """Recover only text fields still uniquely locatable in the approved version."""
    value = structure(value)
    text = page["approved"]["markdown"]
    dropped = []
    if value["printed_label"] and normalized(value["printed_label"]) not in normalized(text):
        dropped.append("printed_label")
        value["printed_label"] = None
    retained = []
    for heading in value["headings"]:
        if text.count(heading) == 1:
            retained.append(heading)
        else:
            dropped.append("heading:" + heading)
    value["headings"] = retained
    for field in RUNNING_FIELDS:
        if field in value:
            retained = []
            for line in value[field]:
                if running_line(text, line, field):
                    retained.append(line)
                else:
                    dropped.append(field + ":" + line)
            value[field] = retained
    return observation(page, value), dropped


def restore_observations(doc, *, apply=False):
    """Explicit metadata repair from retained native receipts; never amend those receipts."""
    changes, skipped = [], []
    for page in doc["pages"]:
        if not page.get("approved"):
            skipped.append({"page": page["page"], "reason": "page_not_approved"})
            continue
        if page.get("observation", {}).get("kind") in KINDS - {"unknown"}:
            continue
        candidates = [
            task
            for task in reversed(doc["tasks"])
            if task.get("page") == page["page"]
            and task["stage"] in {"review", "verify"}
            and task.get("inputs", {}).get("image_sha256") == page["image_sha256"]
            and isinstance((task.get("result") or {}).get("page_observation"), dict)
            and task["result"]["page_observation"].get("kind") in KINDS - {"unknown"}
            and task.get("source_reading", {}).get("sha256")
            and task.get("receipt", {}).get("source_reading_sha256")
            == task["source_reading"]["sha256"]
            and task["receipt"].get("result_sha256") == digest(task["result"])
        ]
        if not candidates:
            skipped.append({"page": page["page"], "reason": "no_valid_original_review_structure"})
            continue
        task = candidates[0]
        try:
            bound, dropped = _rebind(page, task["result"]["page_observation"])
        except ValueError as error:
            skipped.append({"page": page["page"], "reason": str(error)})
            continue
        change = {
            "page": page["page"],
            "before": deepcopy(page.get("observation")),
            "after": bound,
            "source_task_id": task["task_id"],
            "source_result_sha256": task["receipt"]["result_sha256"],
            "source_reading_sha256": task["source_reading"]["sha256"],
            "dropped_fields": dropped,
        }
        changes.append(change)
        if apply:
            page["observation"], page["page_kind"] = bound, bound["kind"]
    report = {
        "restored_pages": [change["page"] for change in changes],
        "changes": changes,
        "skipped": skipped,
        "content_approval": False,
        "human_approval": False,
    }
    if apply and changes:
        doc.setdefault("structure_repairs", []).append(
            {
                **deepcopy(report),
                "at": time.time(),
                "origin": "retained_original_review_metadata",
            }
        )
    return report
