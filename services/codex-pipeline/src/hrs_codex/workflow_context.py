"""Resources shared by explicit workflow commands, with one snapshot transaction."""

from hrs_codex.checkpoints import Checkpoints
from hrs_codex.evidence import Evidence
from hrs_codex.hashing import digest
from hrs_codex.task_inputs import task_inputs


class WorkflowContext:
    def __init__(self, root):
        self.repository = Checkpoints(root)
        self.evidence = Evidence(self.repository)

    def verified(self, document_id, validate=None):
        return self.repository.validated(document_id, validate or self.evidence.verify)

    def inputs(self, document, task):
        return task_inputs(self.repository, document, task)

    def verified_page(self, document_id, page):
        return self._scoped(document_id, lambda doc: page)

    def verified_task(self, document_id, task_id=None, stage=None):
        def page_for(doc):
            import time

            from hrs_codex.scheduling import task_priority

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

    def _scoped(self, document_id, page_for):
        def select(doc):
            page = page_for(doc)
            if page is None:
                return digest(doc)
            return digest(
                {
                    "page": page,
                    "fields": {
                        key: doc.get(key)
                        for key in ("source", "processing_policy", "state", "run_request")
                    },
                    "pages": [row for row in doc["pages"] if row["page"] == page],
                    "tasks": [task for task in doc["tasks"] if task["page"] == page],
                }
            )

        def validate(doc):
            page = page_for(doc)
            self.evidence.verify(doc, None if page is None else [page])

        return self.repository.validated(document_id, validate, select)
