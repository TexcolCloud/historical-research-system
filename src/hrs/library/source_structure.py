"""Workspace-scoped inspection and explicit rechecks of native structure evidence."""

from hrs.library.deletion import require_run_active
from hrs.library.errors import ServiceError
from hrs.library.navigation import Navigation
from hrs.processing.errors import EvidenceMismatch, EvidenceValidationConflict
from hrs.processing.source_structure import structure_snapshot


class SourceStructure:
    def __init__(self, settings, engine):
        self.engine = engine
        self.navigation = Navigation(settings, engine)

    def page(self, run_id, page):
        store, document_id = self.navigation.native(run_id)
        try:
            return self._page(store, document_id, run_id, page)
        except (EvidenceMismatch, EvidenceValidationConflict, FileNotFoundError) as error:
            raise ServiceError(
                409, "本页来源校验未通过或结构已有更新，请重新读取并检查原件是否完整。"
            ) from error

    @staticmethod
    def _page(store, document_id, run_id, page):
        with store.context.verified_page(document_id, page) as document:
            snapshot = structure_snapshot(document)
            if not 1 <= page <= len(snapshot["pages"]):
                raise ServiceError(404, "原件页码不存在。")
            source = document["pages"][page - 1]
            row = snapshot["pages"][page - 1]
            text = source.get("approved", {}).get("markdown", "")
            tasks = [task for task in document["tasks"] if task["stage"] == "structure"]
            page_tasks = [task for task in tasks if task["page"] == page]
            return {
                "document_id": document_id,
                "snapshot_sha256": snapshot["snapshot_sha256"],
                "structure_version": snapshot["structure_version"],
                "page_count": len(snapshot["pages"]),
                "reviewed_pages": sum(p["status"] == "reviewed" for p in snapshot["pages"]),
                "pending_structure_pages": len(
                    {task["page"] for task in tasks if task["state"] in {"ready", "leased"}}
                ),
                "structure_task_state": page_tasks[-1]["state"] if page_tasks else None,
                "processing_paused": bool(
                    document.get("run_request", {}).get("pause_requested")
                    or document.get("run_request", {}).get("state") == "paused"
                ),
                "page": page,
                "status": row["status"],
                "image": f"/api/v2/runs/{run_id}/artifacts/{source['image']}",
                "image_sha256": row["image_sha256"],
                "markdown_sha256": row["markdown_sha256"],
                "coverage": row["coverage"],
                "fragments": [
                    {
                        **fragment,
                        "text": text[fragment["start"] : fragment["end"]][:400],
                        "text_truncated": fragment["end"] - fragment["start"] > 400,
                    }
                    for fragment in row["fragments"]
                ],
                "relations": row["relations"],
            }

    def recheck(self, run_id, request):
        store, document_id = self.navigation.native(run_id)
        with self.engine.begin() as connection:
            require_run_active(connection, str(run_id))
            try:
                receipt = store.backfill_structure(
                    document_id,
                    pages=request.pages,
                    force=request.force,
                    expected_snapshot_sha256=request.expected_snapshot_sha256,
                )
                if receipt["queued_pages"]:
                    receipt["run_request"] = store.request_run(document_id)
            except ValueError as error:
                raise ServiceError(409, str(error)) from error
            return receipt
