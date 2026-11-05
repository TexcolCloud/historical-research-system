"""Names for persisted workflow values; serialization remains plain versioned JSON."""

from typing import Literal, TypedDict

MachineStage = Literal["ocr", "review", "verify", "navigation", "organization"]
TaskStage = MachineStage | Literal["extraction", "human_review", "machine_handoff"]
TaskState = Literal["ready", "leased", "completed", "pending", "superseded"]
PageState = Literal[
    "awaiting_ocr", "awaiting_review", "awaiting_verification", "approved", "pending"
]
DocumentState = Literal["processing", "pending", "ready", "published", "paused", "cancelled"]
RunRequestState = Literal[
    "pending", "running", "paused", "succeeded", "waiting_human", "retryable_error", "cancelled"
]


class TaskRecord(TypedDict):
    task_id: str
    stage: TaskStage
    page: int | None
    state: TaskState
    created_at: float


class TaskReceipt(TypedDict):
    task_id: str
    worker_id: str
    input_sha256: str
    approval_type: str
    human_approval: bool
    created_at: float
