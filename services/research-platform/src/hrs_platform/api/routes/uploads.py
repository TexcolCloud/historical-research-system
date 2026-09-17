from uuid import UUID

from fastapi import APIRouter, Header

from hrs_platform.api.deps import EngineDep, SettingsDep
from hrs_platform.domain.errors import ServiceError
from hrs_platform.schemas import TusHook, UploadRequest, UploadSession
from hrs_platform.services import books

router = APIRouter(tags=["uploads"])


@router.post("/api/v2/uploads", response_model=UploadSession, status_code=201)
def create_upload(
    engine: EngineDep,
    settings: SettingsDep,
    request: UploadRequest,
    idempotency_key: UUID | None = Header(default=None),
    x_upload_token: str | None = Header(default=None),
):
    return books.create_upload(engine, settings, request, idempotency_key, x_upload_token)


@router.post("/internal/tus", include_in_schema=False)
def tus_hook(
    engine: EngineDep, settings: SettingsDep, hook: TusHook, authorization: str | None = Header(default=None)
):
    try:
        return books.handle_tus(engine, settings, hook, authorization)
    except ServiceError as error:
        if hook.Type != "pre-create":
            raise
        return {
            "RejectUpload": True,
            "HTTPResponse": {"StatusCode": error.status_code, "Body": error.detail},
        }
