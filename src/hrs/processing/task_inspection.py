"""TaskInspection: retained originals and source-bound inspection."""

from __future__ import annotations

import time
from uuid import uuid4

from hrs.processing.hashing import digest, file_digest
from hrs.processing.workflow_rules import (
    active_review,
)


def validate_original_reading(context, doc, task, access, reading):
    """The same source/observation gate applies before and after OCR is available."""
    if (
        not access
        or not isinstance(reading, dict)
        or reading.get("image_receipt") != access["receipt"]
        or reading.get("image_sha256") != access["image_sha256"]
    ):
        raise ValueError(
            "Open original_image with this task and lease before recording observations"
        )
    observations, unresolved = reading.get("observations"), reading.get("unresolved", [])
    if (
        not isinstance(observations, list)
        or not observations
        or any(not isinstance(s, str) or not s.strip() for s in observations)
    ):
        raise ValueError("Original reading requires nonempty source observations")
    if not isinstance(unresolved, list) or any(
        not isinstance(s, str) or not s.strip() for s in unresolved
    ):
        raise ValueError("Unresolved original observations must be a list of issues")
    context.evidence.verify_details(doc, task, reading)


class TaskInspection:
    def __init__(self, context):
        self.context = context

    def approved_pages_text(self, document_id, task_id, lease_token, *, after_page=0, limit=8):
        """Return whole frozen pages in a bounded batch; never truncate apparatus."""
        if (
            type(after_page) is not int
            or after_page < 0
            or type(limit) is not int
            or not 1 <= limit <= 8
        ):
            raise ValueError("Approved-page batch needs after_page >= 0 and limit between 1 and 8")
        task = self.get_task(document_id, task_id)
        if (
            task["stage"] not in {"organization", "navigation"}
            or task["state"] != "leased"
            or task["claim"]["lease_token"] != lease_token
            or task["claim"]["expires_at"] <= time.time()
        ):
            raise ValueError("Approved-page batch requires an active organization/navigation lease")
        frozen = [p for p in task["inputs"]["pages"] if p["page"] > after_page]
        pages, size = [], 0
        for reference in frozen[:limit]:
            page = self.approved_page_text(document_id, reference["page"])
            if any(
                page[field] != reference[field] for field in ("image_sha256", "markdown_sha256")
            ):
                raise ValueError("Approved page differs from the frozen organization snapshot")
            if pages and size + len(page["markdown"]) > 16000:
                break
            pages.append(page)
            size += len(page["markdown"])
        return {
            "pages": pages,
            "total_pages": len(task["inputs"]["pages"]),
            "next_after_page": pages[-1]["page"] if len(pages) < len(frozen) else None,
        }

    def approved_page_text(self, document_id, page):
        with self.context.verified_page(document_id, page) as doc:
            if type(page) is not int or not 1 <= page <= len(doc["pages"]):
                raise ValueError("Unknown physical page")
            row = doc["pages"][page - 1]
            if row["state"] != "approved":
                raise ValueError("Page has unresolved content")
            return {
                "document_id": document_id,
                "page": page,
                "image_sha256": row["image_sha256"],
                "markdown": row["approved"]["markdown"],
                "markdown_sha256": row["approved"]["sha256"],
            }

    def original_page(
        self,
        document_id: str,
        page: int,
        *,
        task_id: str | None = None,
        lease_token: str | None = None,
    ) -> tuple[dict, bytes]:
        """Read original pixels for pending, approved or published content alike."""
        pixels = {}

        def validate(doc):
            if type(page) is not int or not 1 <= page <= len(doc["pages"]):
                raise ValueError("Unknown physical page")
            if (
                file_digest(self.context.repository.evidence_path(doc, doc["source"]["path"]))
                != doc["source"]["sha256"]
            ):
                raise ValueError("Retained original source hash mismatch")
            row = doc["pages"][page - 1]
            data = self.context.repository.evidence_path(doc, row["image"]).read_bytes()
            if digest(data) != row["image_sha256"]:
                raise ValueError("Original page image hash mismatch")
            pixels["data"] = data

        with self.context.verified_page(document_id, page, validate) as doc:
            row, data = doc["pages"][page - 1], pixels["data"]
            metadata = {
                "document_id": document_id,
                "page": page,
                "image": row["image"],
                "image_sha256": row["image_sha256"],
                "source_sha256": doc["source"]["sha256"],
            }
            if task_id is not None or lease_token is not None:
                source_task = next(
                    (
                        t
                        for t in doc["tasks"]
                        if t["task_id"] == task_id
                        and t["stage"] in {"navigation", "organization", "ocr"}
                    ),
                    None,
                )
                if source_task:
                    if (
                        source_task["state"] != "leased"
                        or source_task["claim"]["lease_token"] != lease_token
                        or source_task["claim"]["expires_at"] <= time.time()
                    ):
                        raise ValueError("Source image requires an active lease")
                    allowed = (
                        [source_task["page"]]
                        if source_task["stage"] == "ocr"
                        else [p["page"] for p in source_task["inputs"]["pages"]]
                    )
                    if page not in allowed:
                        raise ValueError("Source image outside its claimed snapshot")
                    access = source_task.setdefault("image_accesses", {}).setdefault(
                        str(page),
                        {
                            **metadata,
                            "receipt": uuid4().hex,
                            "opened_at": time.time(),
                            "worker_id": source_task["claim"]["worker_id"],
                        },
                    )
                    self.context.repository.write(doc)
                    return {**metadata, "image_receipt": access["receipt"]}, data
                task = active_review(doc, task_id, lease_token)
                if task["page"] != page:
                    raise ValueError("Original image does not belong to the leased page")
                access = task.get("image_access")
                if not access:
                    access = {
                        **metadata,
                        "task_id": task_id,
                        "worker_id": task["claim"]["worker_id"],
                        "receipt": uuid4().hex,
                        "opened_at": time.time(),
                    }
                    task["image_access"] = access
                    self.context.repository.write(doc)
                metadata["image_receipt"] = access["receipt"]
            return metadata, data

    def review_candidate(
        self, document_id: str, task_id: str, lease_token: str, original_reading: dict
    ) -> dict:
        """Freeze source observations before exposing the candidate to a reviewer."""
        with self.context.verified_task(document_id, task_id=task_id) as doc:
            task = active_review(doc, task_id, lease_token)
            access = task.get("image_access")
            validate_original_reading(self.context, doc, task, access, original_reading)
            if task.get("source_reading"):
                if task["source_reading"]["reading"] != original_reading:
                    raise ValueError(
                        "Original reading cannot change after candidate exposure; retain uncertainty and retry independently"
                    )
            else:
                reading = {
                    "reading": original_reading,
                    "image_access": access,
                    "worker_id": task["claim"]["worker_id"],
                    "input_sha256": task["claim"]["input_sha256"],
                    "approval_type": "source_observation",
                    "human_approval": False,
                    "created_at": time.time(),
                }
                reading["sha256"] = digest(reading)
                task["source_reading"] = reading
                self.context.repository.write(doc)
            return {**task, **task["claim"]}

    def candidate_text(
        self, document_id: str, task_id: str, lease_token: str, *, start: int = 0, limit: int = 2000
    ) -> dict:
        with self.context.verified_task(document_id, task_id=task_id) as doc:
            task = active_review(doc, task_id, lease_token)
            if not task.get("source_reading"):
                raise ValueError("Freeze original-image observations before reading candidate text")
            text = task["inputs"]["markdown"]
            if (
                type(start) is not int
                or type(limit) is not int
                or not 0 <= start <= len(text)
                or not 1 <= limit <= 4000
            ):
                raise ValueError(
                    "Candidate range needs start within text and limit between 1 and 4000"
                )
            end = min(start + limit, len(text))
            task.setdefault("candidate_accesses", []).append(
                {
                    "start": start,
                    "end": end,
                    "lease_token": lease_token,
                    "markdown_sha256": digest(text),
                }
            )
            self.context.repository.write(doc)
            return {
                "task_id": task_id,
                "markdown_sha256": digest(text),
                "start": start,
                "end": end,
                "total_codepoints": len(text),
                "markdown": text[start:end],
            }

    def get_task(self, document_id: str, task_id: str) -> dict:
        with self.context.repository.lock:
            doc = self.context.repository.read(document_id)
            task = next(t for t in doc["tasks"] if t["task_id"] == task_id)
            inputs = task["inputs"] if "inputs" in task else self.context.inputs(doc, task)
            return {**task, "inputs": inputs}
