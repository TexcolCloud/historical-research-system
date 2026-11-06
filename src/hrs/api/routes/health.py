from fastapi import APIRouter, HTTPException
from sqlalchemy import text

from hrs.api.deps import EngineDep, SettingsDep
from hrs.api.schemas import RuntimeStatus

router = APIRouter(tags=["health"])


@router.get("/api/v2/runtime")
def runtime(settings: SettingsDep) -> RuntimeStatus:
    from hrs.processing.acceptance import workspace_scope
    from hrs.processing.store import Store
    from hrs.processing.workspace import identity, resolve_workspace

    workspace = identity(Store(resolve_workspace(settings.codex_root)))
    try:
        scope = workspace_scope(resolve_workspace(settings.codex_root))
    except ValueError as error:
        raise HTTPException(503, str(error)) from error
    return {
        "backend": settings.processing_backend,
        "workspace_id": workspace["workspace_id"],
        "root": workspace["root"],
        "ocr_model": "gpt-6.1-terra",
        "review_model": "gpt-6.1-sol",
        "review_failure": "bounded",
        "validation_scope": scope,
    }


@router.get("/health/live")
def live():
    return {"status": "ok"}


@router.get("/health/ready")
def ready(engine: EngineDep):
    with engine.connect() as connection:
        connection.execute(text("SELECT 1 FROM alembic_version"))
    return {"status": "ready"}
