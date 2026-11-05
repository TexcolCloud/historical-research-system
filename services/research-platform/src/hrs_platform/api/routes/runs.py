from uuid import UUID

from fastapi import APIRouter, Header, Query
from fastapi.responses import Response

from hrs_platform.api.deps import EngineDep, LibraryDep, OutputsDep, ReviewDep, SettingsDep
from hrs_platform.domain.errors import ServiceError
from hrs_platform.schemas import (
    ExecutionNode,
    ExecutionWindow,
    NavigationDecision,
    NavigationReport,
    RecoveryReport,
    RecoveryRequest,
    RunControlReceipt,
    RunSummary,
    StructureReport,
)
from hrs_platform.services import books
from hrs_platform.services.runs.recovery import retry_run

router = APIRouter(tags=["runs"])


@router.get("/api/v2/runs/{run_id}/navigation", response_model=NavigationReport)
def navigation(settings: SettingsDep, engine: EngineDep, run_id: UUID) -> dict:
    from hrs_platform.services.documents.navigation import Navigation

    return Navigation(settings, engine).status(run_id)


@router.post("/api/v2/runs/{run_id}/navigation/{entry_id}/decision", response_model=NavigationReport)
def navigation_decision(
    settings: SettingsDep, engine: EngineDep, run_id: UUID, entry_id: str, request: NavigationDecision
) -> dict:
    from hrs_platform.services.documents.navigation import Navigation

    return Navigation(settings, engine).decide(run_id, entry_id, request)


@router.post("/api/v2/runs/{run_id}/pause", response_model=RunControlReceipt)
def pause(settings: SettingsDep, engine: EngineDep, run_id: UUID):
    from hrs_platform.services.documents.navigation import Navigation

    return Navigation(settings, engine).control(run_id, "pause")


@router.post("/api/v2/runs/{run_id}/resume", response_model=RunControlReceipt)
def resume(settings: SettingsDep, engine: EngineDep, run_id: UUID):
    from hrs_platform.services.documents.navigation import Navigation

    return Navigation(settings, engine).control(run_id, "resume")


@router.get("/api/v2/runs/{run_id}", response_model=RunSummary)
def get_run(engine: EngineDep, run_id: UUID):
    return books.get_run(engine, run_id)


@router.get("/api/v2/runs/{run_id}/recovery", response_model=RecoveryReport)
def recovery(settings: SettingsDep, engine: EngineDep, run_id: UUID):
    from hrs_platform.services.runs.recovery import native_report

    return native_report(settings, engine, str(run_id))


@router.post("/api/v2/runs/{run_id}/retry", response_model=RunSummary)
def retry(engine: EngineDep, run_id: UUID, request: RecoveryRequest):
    if books.get_run(engine, run_id)["kind"] == "cards":
        raise ServiceError(410, "史料卡制作已停用，历史结果与证据仍保留。")
    return retry_run(engine, run_id, request.request_id)


@router.get("/api/v2/runs/{run_id}/executions", response_model=list[ExecutionNode])
def executions(engine: EngineDep, outputs: OutputsDep, run_id: UUID):
    books.get_run(engine, run_id)
    return outputs.list_nodes(run_id)


@router.get("/api/v2/runs/{run_id}/execution-window", response_model=ExecutionWindow)
def execution_window(
    engine: EngineDep,
    outputs: OutputsDep,
    run_id: UUID,
    history: bool = False,
    label: str | None = Query(default=None, max_length=255),
    offset: int = Query(default=0, ge=0),
):
    books.get_run(engine, run_id)
    return outputs.window(run_id, history=history, label=label, offset=offset)


@router.get("/api/v2/executions/{execution_id}/details")
def execution_details(outputs: OutputsDep, execution_id: UUID):
    return outputs.details(execution_id)


@router.get("/api/v2/runs/{run_id}/structure", response_model=StructureReport)
def reading_structure(library: LibraryDep, run_id: UUID):
    return library.structure(str(run_id))


@router.get("/api/v2/runs/{run_id}/artifacts/{name:path}")
def artifact(
    review: ReviewDep, run_id: UUID, name: str, range_header: str | None = Header(default=None, alias="Range")
):
    reference = review.artifact(run_id, name)
    if reference["media_type"] == "application/pdf":
        from hrs_platform.api.files import object_response

        return object_response(review.objects, reference, range_header)
    return Response(
        review.objects.read_bytes(reference),
        media_type=reference["media_type"],
        headers={
            "ETag": '"' + reference["sha256"] + '"',
            "Cache-Control": "private, max-age=3600",
            "X-Content-Type-Options": "nosniff",
        },
    )
