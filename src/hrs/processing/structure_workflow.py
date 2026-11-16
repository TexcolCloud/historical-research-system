"""Explicit, idempotent source-structure backfill in the existing task ledger."""

import time

from hrs.processing.source_structure import SCHEMA_VERSION, structure_snapshot
from hrs.processing.workflow_rules import new_task, require_active


class StructureWorkflow:
    def __init__(self, context):
        self.context = context

    def snapshot(self, document_id):
        with self.context.verified(document_id) as doc:
            return structure_snapshot(doc)

    def pages(self, document_id, *, offset=0, limit=4):
        """Bound inspection output while validating one source and the requested page evidence."""
        if type(offset) is not int or offset < 0:
            raise ValueError("Structure offset must be an integer at least 0")
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError("Structure limit must be an integer between 1 and 20")

        def validate(doc):
            if offset >= len(doc["pages"]):
                raise ValueError("Structure offset is outside the document page sequence")
            selected = [page["page"] for page in doc["pages"][offset : offset + limit]]
            self.context.evidence.verify(doc, pages=selected)

        # Full document revision CAS keeps the global snapshot identity consistent;
        # evidence file I/O runs before the short repository lock is acquired.
        with self.context.verified(document_id, validate=validate) as doc:
            snapshot = structure_snapshot(doc)
            count = len(snapshot["pages"])
            selected = snapshot["pages"][offset : offset + limit]
            end = offset + len(selected)
            return {
                **snapshot,
                "pages": selected,
                "page_count": count,
                "offset": offset,
                "limit": limit,
                "next_offset": end if end < count else None,
                "truncated": len(selected) < count,
            }

    def backfill(self, document_id, *, pages=None, force=False, expected_snapshot_sha256=None):
        """Queue only missing/stale facts; directed recheck requires explicit force/pages."""
        if type(force) is not bool or (force and not pages):
            raise ValueError("Directed structure recheck requires explicit physical pages")
        with self.context.verified(document_id) as doc:
            require_active(doc)
            if pages is not None and (
                not isinstance(pages, list)
                or not pages
                or any(type(n) is not int or not 1 <= n <= len(doc["pages"]) for n in pages)
            ):
                raise ValueError("Structure backfill requires known physical pages")
            selected = set(pages or [p["page"] for p in doc["pages"]])
            snapshot = structure_snapshot(doc)
            if (
                expected_snapshot_sha256 is not None
                and snapshot["snapshot_sha256"] != expected_snapshot_sha256
            ):
                raise ValueError(
                    "Structure snapshot changed; read the current snapshot before requesting a recheck"
                )
            queued, skipped = [], []
            for row in snapshot["pages"]:
                number = row["page"]
                if number not in selected:
                    continue
                page = doc["pages"][number - 1]
                active = [
                    task
                    for task in doc["tasks"]
                    if task["stage"] == "structure"
                    and task["page"] == number
                    and task["state"] in {"ready", "leased"}
                ]
                if (
                    not page.get("approved")
                    or active
                    or (row["status"] == "reviewed" and not force)
                ):
                    skipped.append(
                        {
                            "page": number,
                            "reason": "already_queued"
                            if active
                            else "already_reviewed"
                            if page.get("approved")
                            else "content_not_approved",
                        }
                    )
                    continue
                task = new_task("structure", number)
                task["structure_policy"] = {
                    "schema_version": SCHEMA_VERSION,
                    "original_first": True,
                    "content_approval": False,
                }
                # Bind each explicit request to the exact current source; retries keep it frozen.
                task["structure_source"] = {
                    "image_sha256": page["image_sha256"],
                    "markdown_sha256": page["approved"]["sha256"],
                }
                doc["tasks"].append(task)
                queued.append(number)
            if queued:
                doc.setdefault("structure_backfills", []).append(
                    {
                        "at": time.time(),
                        "pages": queued,
                        "directed_recheck": force,
                        "schema_version": SCHEMA_VERSION,
                    }
                )
                self.context.repository.write(doc)
            return {
                "document_id": document_id,
                "queued_pages": queued,
                "skipped": skipped,
                "content_approval": False,
                "human_approval": False,
                "schema_version": SCHEMA_VERSION,
            }
