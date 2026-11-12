"""Personal analysis and scoped discovery; source approvals remain elsewhere."""

from typing import Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field

from hrs.api.deps import ResearchDep
from hrs.library.errors import ServiceError
from hrs.research.schemas import ResearchSourceHit
from hrs.studies.analysis import AnalysisService
from hrs.studies.analysis_schemas import (
    AnalysisAdoption,
    AnalysisDisposition,
    AnalysisPage,
    AnalysisRequest,
    AnalysisResponse,
    AnalysisVersion,
)
from hrs.studies.discovery import search_materials
from hrs.studies.schemas import RecordResponse
from hrs.studies.service import StudyService

router = APIRouter(prefix="/api/v2/studies", tags=["studies"])


class StudySearchResponse(BaseModel):
    query: str
    backend: Literal["hybrid", "lexical"]
    available: bool
    hits: list[ResearchSourceHit]
    evidence_status: str
    metrics: dict


@router.get("/search", response_model=StudySearchResponse)
def search(
    service: ResearchDep,
    q: str = Query(min_length=1, max_length=1000),
    topic_id: str | None = None,
    book_ids: list[str] | None = Query(default=None, max_length=100),
    semantic: bool = True,
    limit: int = Query(12, ge=1, le=50),
):
    topic = StudyService(service).topic(topic_id) if topic_id else None
    return search_materials(
        service, q, topic=topic, book_ids=book_ids, semantic=semantic, limit=limit
    )


@router.get("/topics/{topic_id}/analyses", response_model=AnalysisPage)
def analyses(
    service: ResearchDep,
    topic_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=200),
):
    return AnalysisService(StudyService(service)).list(topic_id, offset=offset, limit=limit)


@router.post("/topics/{topic_id}/analyses", response_model=AnalysisResponse)
def create(service: ResearchDep, topic_id: str, body: AnalysisRequest):
    return AnalysisService(StudyService(service)).create(topic_id, body)


@router.get("/analyses/{analysis_id}", response_model=AnalysisResponse)
def analysis(service: ResearchDep, analysis_id: str):
    return AnalysisService(StudyService(service)).read(analysis_id)


@router.post("/analyses/{analysis_id}/cancel", response_model=AnalysisResponse)
def cancel(service: ResearchDep, analysis_id: str, body: AnalysisVersion):
    return AnalysisService(StudyService(service)).cancel(analysis_id, body.expected_version)


@router.post("/analyses/{analysis_id}/adopt", response_model=RecordResponse)
def adopt(service: ResearchDep, analysis_id: str, body: AnalysisAdoption):
    return AnalysisService(StudyService(service)).adopt(analysis_id, body)


@router.post(
    "/analyses/{analysis_id}/suggestions/{suggestion_id}/disposition",
    response_model=AnalysisResponse,
)
def disposition(
    service: ResearchDep, analysis_id: str, suggestion_id: str, body: AnalysisDisposition
):
    return AnalysisService(StudyService(service)).disposition(
        analysis_id, suggestion_id, body.expected_version, ignored=body.ignored
    )


class Preferences(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=0)
    compact: bool = False
    restore_last_canvas: bool = True


class PreferencesResponse(BaseModel):
    version: int
    compact: bool
    restore_last_canvas: bool


@router.get("/preferences", response_model=PreferencesResponse)
def preferences(service: ResearchDep):
    studies = StudyService(service)
    try:
        return studies.store.read("preferences", "workspace-preferences")
    except ServiceError as error:
        if error.status_code != 404:
            raise
        return {"version": 0, "compact": False, "restore_last_canvas": True}


@router.patch("/preferences", response_model=PreferencesResponse)
def save_preferences(service: ResearchDep, body: Preferences):
    studies = StudyService(service)
    values = body.model_dump(exclude={"expected_version"})
    if body.expected_version == 0:
        return studies.store.create("preferences", values, item_id="workspace-preferences")
    return studies.store.save(
        "preferences", "workspace-preferences", values, expected_version=body.expected_version
    )
