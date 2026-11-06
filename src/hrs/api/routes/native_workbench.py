from fastapi import APIRouter
from pydantic import BaseModel, Field

from hrs.api.deps import EngineDep, SettingsDep
from hrs.library.native_workbench import attach_document

router = APIRouter(tags=["workbench"])


class NativeWorkbenchRequest(BaseModel):
    document_id: str = Field(pattern=r"^[0-9a-f]{24}$")
    workspace_id: str = Field(pattern=r"^[0-9a-f]{32}$")


@router.post("/api/v2/native-workbench", status_code=201)
def native_workbench(settings: SettingsDep, engine: EngineDep, request: NativeWorkbenchRequest):
    return attach_document(settings, engine, request.document_id, request.workspace_id)
