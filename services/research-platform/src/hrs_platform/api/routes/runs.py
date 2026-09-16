from uuid import UUID

from fastapi import APIRouter, Header
from fastapi.responses import Response

from hrs_platform.api.deps import EngineDep, LibraryDep, OutputsDep, ReviewDep
from hrs_platform.schemas import ExecutionNode, RecoveryRequest, RunSummary, StructureReport
from hrs_platform.services import books
from hrs_platform.services.recovery import retry_run

router = APIRouter(tags=["runs"])


@router.get("/api/v2/runs/{run_id}", response_model=RunSummary)
def get_run(engine: EngineDep, run_id: UUID):
    return books.get_run(engine, run_id)


@router.post("/api/v2/runs/{run_id}/retry", response_model=RunSummary)
def retry(engine: EngineDep, run_id: UUID, request: RecoveryRequest):
    return retry_run(engine, run_id, request.request_id)


@router.get("/api/v2/runs/{run_id}/executions", response_model=list[ExecutionNode])
def executions(engine: EngineDep, outputs: OutputsDep, run_id: UUID):
    books.get_run(engine, run_id)
    return outputs.list_nodes(run_id)


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
        from hrs_platform.services.storage import object_response

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
