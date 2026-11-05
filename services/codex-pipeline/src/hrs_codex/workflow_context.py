"""Resources shared by explicit workflow commands, with one snapshot transaction."""

from hrs_codex.checkpoints import Checkpoints
from hrs_codex.evidence import Evidence
from hrs_codex.task_inputs import task_inputs


class WorkflowContext:
    def __init__(self, root):
        self.repository = Checkpoints(root)
        self.evidence = Evidence(self.repository)

    def verified(self, document_id, validate=None):
        return self.repository.validated(document_id, validate or self.evidence.verify)

    def inputs(self, document, task):
        return task_inputs(self.repository, document, task)
