from fastapi import APIRouter
from sqlalchemy import text

from hrs_platform.api.deps import EngineDep, SettingsDep
from hrs_platform.schemas import RuntimeStatus

router = APIRouter(tags=["health"])


@router.get("/api/v2/runtime")
def runtime(settings: SettingsDep) -> RuntimeStatus:
    from hrs_codex.store import Store
    from hrs_codex.workspace import identity, resolve_workspace

    workspace = identity(Store(resolve_workspace(settings.codex_root)))
    return {
        "backend": settings.processing_backend,
        "workspace_id": workspace["workspace_id"],
        "root": workspace["root"],
        "ocr_model": "gpt-6.1-terra",
        "review_model": "gpt-6.1-sol",
        "review_failure": "bounded",
        "validation_scope": "workflow_only"
        if (resolve_workspace(settings.codex_root) / ".workflow-test-authorization.json").exists()
        else "content",
    }


@router.get("/health/live")
def live():
    return {"status": "ok"}


@router.get("/health/ready")
def ready(engine: EngineDep):
    with engine.connect() as connection:
        connection.execute(text("SELECT 1 FROM alembic_version"))
    return {"status": "ready"}
