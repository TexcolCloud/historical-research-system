"""Stable error categories; existing callers may continue catching ValueError."""


class EvidenceMismatch(ValueError):
    code = "evidence_mismatch"


class LeaseConflict(ValueError):
    code = "lease_conflict"


class EvidenceValidationConflict(LeaseConflict):
    """One evidence scope changed repeatedly while its immutable snapshot was checked."""


class WorkflowStopped(ValueError):
    code = "workflow_stopped"
