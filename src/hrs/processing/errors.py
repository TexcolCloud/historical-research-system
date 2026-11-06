"""Stable error categories; existing callers may continue catching ValueError."""


class EvidenceMismatch(ValueError):
    code = "evidence_mismatch"


class LeaseConflict(ValueError):
    code = "lease_conflict"


class WorkflowStopped(ValueError):
    code = "workflow_stopped"
