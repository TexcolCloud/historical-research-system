from uuid import UUID

from fastapi import APIRouter, Header, Request
from fastapi.responses import Response

from hrs.api.deps import EngineDep, SettingsDep
from hrs.api.schemas import TusHook, UploadRequest, UploadSession
from hrs.library import books
from hrs.library.errors import ServiceError

router = APIRouter(tags=["uploads"])


@router.api_route(
    "/uploads/{upload_path:path}",
    methods=["POST", "PATCH", "HEAD", "DELETE", "OPTIONS"],
    include_in_schema=False,
)
async def local_tus_proxy(upload_path: str, request: Request, settings: SettingsDep):
    """Keep resumable browser uploads same-origin when the host API serves React."""
    import httpx

    headers = {
        k: v
        for k, v in request.headers.items()
        if k.lower() not in {"host", "connection", "content-length"}
    }
    async with httpx.AsyncClient(timeout=300) as client:
        response = await client.request(
            request.method,
            settings.tus_control_url.rstrip("/") + "/" + upload_path,
            headers=headers,
            content=request.stream(),
        )
    returned = {
        k: v
        for k, v in response.headers.items()
        if k.lower()
        not in {"connection", "transfer-encoding", "content-encoding", "content-length"}
    }
    if "location" in returned:
        from urllib.parse import urlparse

        returned["location"] = urlparse(returned["location"]).path
    return Response(response.content, status_code=response.status_code, headers=returned)


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
    engine: EngineDep,
    settings: SettingsDep,
    hook: TusHook,
    authorization: str | None = Header(default=None),
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
