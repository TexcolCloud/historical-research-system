from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, HTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from hrs.api.deps import EngineDep, SettingsDep
from hrs.api.schemas import RuntimeStatus
from hrs.search.service import configured_broker_health
from hrs.storage.objects import ObjectStorageError
from hrs.storage.service import objects_for

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
def ready(engine: EngineDep, settings: SettingsDep):
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1 FROM alembic_version"))
        objects_for(settings).check()
    except (SQLAlchemyError, BotoCoreError, ClientError, ObjectStorageError):
        raise HTTPException(503, "Required storage services are unavailable.") from None
    if settings.retrieval_device == "cuda":
        try:
            if configured_broker_health(settings).get("gpu_available") is not True:
                raise RuntimeError("GPU unavailable")
        except (OSError, ValueError, RuntimeError):
            raise HTTPException(503, "Required retrieval service is unavailable.") from None
    return {"status": "ready"}
