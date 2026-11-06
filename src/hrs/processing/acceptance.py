"""Workspace authorization and retained acceptance provenance have separate lifetimes."""

import json
from pathlib import Path


def workspace_scope(root: Path) -> str:
    """Read current permission; malformed permissions never imply content approval."""
    path = Path(root) / ".workflow-test-authorization.json"
    if not path.exists():
        return "content"
    try:
        value = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(
            "Workflow-test authorization is unreadable; restore a valid workspace authorization"
        ) from error
    if not isinstance(value, dict) or type(value.get("enabled")) is not bool:
        raise ValueError("Workflow-test authorization requires an explicit boolean enabled field")
    if value.get("scope", "workflow_only") != "workflow_only":
        raise ValueError("Workflow-test authorization must have workflow_only scope")
    return "workflow_only" if value["enabled"] else "content"


def decision_scope(root: Path, requested: str | None = None) -> str:
    """Choose a local decision's scope before writing its authoritative receipt."""
    current = "workflow_test" if workspace_scope(root) == "workflow_only" else "content"
    if requested is not None and requested not in {"content", "workflow_test"}:
        raise ValueError("Unknown decision scope")
    if requested is not None and requested != current:
        raise ValueError(
            "Decision scope differs from current workspace authorization; reload before deciding"
        )
    return current


def publication_acceptance(document: dict, *, include_active: bool = True) -> str:
    """Retained test provenance survives permission revocation, relocation and reprojection."""
    publications = [
        document.get("publication"),
        (document.get("active_publication") or {}).get("publication") if include_active else None,
    ]
    for publication in publications:
        if publication and (
            publication.get("approval_type") == "workflow_test"
            or publication.get("validation_scope") == "workflow_only"
            or publication.get("content_verified") is False
        ):
            return "workflow_test"
    if include_active and not document.get("publication") and publications[1]:
        # During a revision the library still serves the retained publication.
        # Draft decisions cannot relabel that earlier published version.
        return "machine_or_human"
    if any(
        (page.get("approved") or {}).get("approval_type") == "workflow_test"
        for page in document.get("pages", [])
    ):
        return "workflow_test"
    if any(
        receipt.get("approval_type") == "workflow_test"
        for receipt in document.get("navigation", {}).get("decisions", {}).values()
    ):
        return "workflow_test"
    return "machine_or_human"
