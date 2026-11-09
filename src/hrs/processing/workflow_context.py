"""Resources shared by explicit workflow commands, with one snapshot transaction."""

from hrs.processing.checkpoints import Checkpoints
from hrs.processing.evidence import Evidence
from hrs.processing.hashing import digest
from hrs.processing.task_inputs import task_inputs


class WorkflowContext:
    def __init__(self, root):
        self.repository = Checkpoints(root)
        self.evidence = Evidence(self.repository)

    def verified(self, document_id, validate=None):
        return self.repository.validated(document_id, validate or self.evidence.verify)

    def inputs(self, document, task):
        return task_inputs(self.repository, document, task)

    def verified_page(self, document_id, page, validate=None):
        return self._scoped(document_id, lambda doc: page, validate)

    def verified_task(self, document_id, task_id=None, stage=None, selector=None):
        def page_for(doc):
            if selector is not None:
                task = selector(doc)
                return task["page"] if task else 0
            import time

            from hrs.processing.scheduling import task_priority

            candidates = [
                task
                for task in doc["tasks"]
                if (
                    task["task_id"] == task_id
                    if task_id
                    else task["stage"] == stage
                    and task["state"] in {"ready", "leased"}
                    and task.get("retry_after", 0) <= time.time()
                    and (task["state"] != "leased" or task["claim"]["expires_at"] <= time.time())
                )
            ]
            selected = min(candidates, key=lambda task: task_priority(doc, task), default=None)
            return selected["page"] if selected else 0

        return self._scoped(document_id, page_for)

    def _scoped(self, document_id, page_for, validate=None):
        selected_page = None

        def select(doc):
            nonlocal selected_page
            page = page_for(doc)
            selected_page = page
            selected = {
                "page": page,
                "fields": {
                    key: doc.get(key)
                    for key in (
                        "source",
                        "processing_policy",
                        "state",
                        "revision",
                        "revision_history",
                    )
                },
                # Progress, scheduler metrics and lease renewals do not change evidence.
                "run_control": {
                    key: doc.get("run_request", {}).get(key)
                    for key in ("state", "token", "owner", "pause_requested", "cancel_requested")
                },
                "pages": [row for row in doc["pages"] if page is None or row["page"] == page],
                "tasks": [
                    {
                        **task,
                        "claim": {
                            key: value
                            for key, value in task["claim"].items()
                            if key != "expires_at"
                        },
                    }
                    if task.get("claim")
                    else task
                    for task in doc["tasks"]
                    if page is None or task["page"] == page
                ],
            }
            if page is None:
                selected["organization"] = doc.get("organization")
            return page, digest(selected)

        def validate_snapshot(doc):
            if validate is not None:
                return validate(doc)
            self.evidence.verify(doc, None if selected_page is None else [selected_page])

        return self.repository.validated(
            document_id, validate_snapshot, select, retry_scope=lambda selected: selected[0]
        )
