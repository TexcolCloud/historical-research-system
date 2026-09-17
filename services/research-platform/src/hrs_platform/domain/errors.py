"""Stable recoverable errors, without upstream credentials or exception dumps."""

from typing import Any


class ServiceError(Exception):
    """A rejected business operation; the HTTP adapter preserves its public status."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(f"{status_code}: {detail}")
        self.status_code, self.detail = status_code, detail


class TaskError(Exception):
    """Durable failure information, independent of the workflow engine."""

    def __init__(self, message: str, *details, type=None, non_retryable=False):
        super().__init__(message)
        self.message, self.details = message, details
        self.type, self.non_retryable = type, non_retryable


class Problem(Exception):
    def __init__(
        self,
        code: str,
        detail: str,
        *,
        status: int = 400,
        retryable: bool = False,
        next_actions: list[dict[str, Any]] | None = None,
        errors: list[dict[str, Any]] | None = None,
    ):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status = status
        self.retryable = retryable
        self.next_actions = next_actions or []
        self.errors = errors or []

    def body(self, request_id: str, instance: str = "") -> dict:
        return {
            "type": f"urn:document-retrieval:problem:{self.code}",
            "title": self.code.replace("_", " "),
            "status": self.status,
            "detail": self.detail,
            "instance": instance,
            "code": self.code,
            "request_id": request_id,
            "retryable": self.retryable,
            "errors": self.errors,
            "next_actions": self.next_actions,
        }
