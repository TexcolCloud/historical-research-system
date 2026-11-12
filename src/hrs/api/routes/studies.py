"""Personal study HTTP adapter; all writes share the native study transaction."""

from typing import Literal

from fastapi import APIRouter, Query
from fastapi.responses import Response

from hrs.api.deps import ResearchDep
from hrs.studies.schemas import (
    CanvasContextResponse,
    CanvasCreate,
    CanvasImport,
    CanvasPage,
    CanvasResponse,
    CanvasRevision,
    CanvasUpdate,
    MaterialCreate,
    RecordCreate,
    RecordKind,
    RecordPage,
    RecordResponse,
    RecordUpdate,
    SourceReference,
    StudyTimelineResponse,
    TopicCreate,
    TopicPage,
    TopicResponse,
    TopicUpdate,
)
from hrs.studies.service import StudyService

router = APIRouter(prefix="/api/v2/studies", tags=["studies"])


@router.get("/topics", response_model=TopicPage)
def topics(
    research: ResearchDep,
    archived: bool = False,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    return StudyService(research).topics(archived=archived, offset=offset, limit=limit)


@router.post("/topics", response_model=TopicResponse)
def create_topic(research: ResearchDep, body: TopicCreate):
    return StudyService(research).create_topic(body)


@router.get("/topics/{topic_id}", response_model=TopicResponse)
def topic(research: ResearchDep, topic_id: str):
    return StudyService(research).topic(topic_id)


@router.patch("/topics/{topic_id}", response_model=TopicResponse)
def update_topic(research: ResearchDep, topic_id: str, body: TopicUpdate):
    return StudyService(research).update_topic(topic_id, body)


@router.post("/topics/{topic_id}/materials", response_model=TopicResponse)
def add_material(research: ResearchDep, topic_id: str, body: MaterialCreate):
    return StudyService(research).add_material(topic_id, body)


@router.get("/topics/{topic_id}/materials/{material_id}", response_model=SourceReference)
def material(research: ResearchDep, topic_id: str, material_id: str):
    return StudyService(research).material(topic_id, material_id)


@router.delete("/topics/{topic_id}/materials/{material_id}", response_model=TopicResponse)
def remove_material(
    research: ResearchDep, topic_id: str, material_id: str, expected_version: int = Query(ge=1)
):
    return StudyService(research).remove_material(
        topic_id, material_id, expected_version=expected_version
    )


@router.get("/topics/{topic_id}/records", response_model=RecordPage)
def records(
    research: ResearchDep,
    topic_id: str,
    kind: RecordKind | None = None,
    archived: bool = False,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    return StudyService(research).records(
        topic_id, kind=kind, archived=archived, offset=offset, limit=limit
    )


@router.post("/topics/{topic_id}/records", response_model=RecordResponse)
def create_record(research: ResearchDep, topic_id: str, body: RecordCreate):
    return StudyService(research).create_record(topic_id, body)


@router.get("/records/{record_id}", response_model=RecordResponse)
def record(research: ResearchDep, record_id: str):
    return StudyService(research).record(record_id)


@router.patch("/records/{record_id}", response_model=RecordResponse)
def update_record(research: ResearchDep, record_id: str, body: RecordUpdate):
    return StudyService(research).update_record(record_id, body)


@router.get("/topics/{topic_id}/canvases", response_model=CanvasPage)
def canvases(
    research: ResearchDep,
    topic_id: str,
    archived: bool = False,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    return StudyService(research).canvases(topic_id, archived=archived, offset=offset, limit=limit)


@router.post("/topics/{topic_id}/canvases", response_model=CanvasResponse)
def create_canvas(research: ResearchDep, topic_id: str, body: CanvasCreate):
    return StudyService(research).create_canvas(topic_id, body)


@router.post("/topics/{topic_id}/canvases/import", response_model=CanvasResponse)
def import_canvas(research: ResearchDep, topic_id: str, body: CanvasImport):
    return StudyService(research).import_canvas(topic_id, body)


@router.get("/canvases/{canvas_id}", response_model=CanvasResponse)
def canvas(research: ResearchDep, canvas_id: str):
    return StudyService(research).canvas(canvas_id)


@router.get("/canvases/{canvas_id}/context", response_model=CanvasContextResponse)
def canvas_context(
    research: ResearchDep, canvas_id: str, expected_version: int | None = Query(default=None, ge=1)
):
    return StudyService(research).canvas_context(canvas_id, expected_version=expected_version)


@router.patch("/canvases/{canvas_id}", response_model=CanvasResponse)
def update_canvas(research: ResearchDep, canvas_id: str, body: CanvasUpdate):
    return StudyService(research).update_canvas(canvas_id, body)


@router.get("/canvases/{canvas_id}/revisions", response_model=list[CanvasRevision])
def canvas_revisions(
    research: ResearchDep,
    canvas_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    return StudyService(research).canvas_revisions(canvas_id, offset=offset, limit=limit)


@router.get("/canvases/{canvas_id}/export")
def export_canvas(
    research: ResearchDep, canvas_id: str, format: Literal["json", "markdown"] = "json"
):
    content = StudyService(research).export_canvas(canvas_id, format=format)
    suffix = "json" if format == "json" else "md"
    return Response(
        content,
        media_type="application/json" if format == "json" else "text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="study-canvas-{canvas_id}.{suffix}"'
        },
    )


@router.get("/topics/{topic_id}/timeline", response_model=StudyTimelineResponse)
def timeline(
    research: ResearchDep,
    topic_id: str,
    date_basis: Literal["event", "formation"] = "event",
    material_id: str | None = Query(default=None, max_length=200),
    date_from: str | None = None,
    date_to: str | None = None,
    include_undated: bool = True,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=200),
):
    return StudyService(research).timeline(
        topic_id,
        date_basis=date_basis,
        material_id=material_id,
        date_from=date_from,
        date_to=date_to,
        include_undated=include_undated,
        offset=offset,
        limit=limit,
    )
