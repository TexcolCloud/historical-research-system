"""Stable recoverable errors, without upstream credentials or exception dumps."""

from typing import Any


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
