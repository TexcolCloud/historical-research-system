"""Research routes are separate from retired legacy card-generation interfaces."""

from uuid import UUID

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response

from hrs.api.deps import ResearchDep
from hrs.research.api import (
    ResearchBookResponse,
    ResearchCardPage,
    ResearchCardResponse,
    ResearchDecision,
    ResearchEntityPage,
    ResearchEntityResponse,
    ResearchRunResponse,
    ResearchSearchResponse,
    StartResearch,
)

router = APIRouter(prefix="/api/v2/research", tags=["research"])


@router.get("/books/{book_id}", response_model=ResearchBookResponse)
def overview(
    service: ResearchDep,
    book_id: UUID,
    include_cards: bool = True,
    include_entities: bool = True,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
):
    return service.overview(
        book_id,
        include_cards=include_cards,
        include_entities=include_entities,
        offset=offset,
        limit=limit,
    )


@router.post("/books/{book_id}/runs", response_model=ResearchRunResponse)
def start(service: ResearchDep, book_id: UUID, body: StartResearch | None = None):
    return service.start(book_id, body or StartResearch())


@router.get("/books/{book_id}/cards", response_model=ResearchCardPage)
def cards(
    service: ResearchDep,
    book_id: UUID,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
):
    from hrs.storage.models import research_cards

    return service.records(research_cards, book_id, offset=offset, limit=limit)


@router.get("/books/{book_id}/entities", response_model=ResearchEntityPage)
def entities(
    service: ResearchDep,
    book_id: UUID,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
):
    from hrs.storage.models import research_entities

    return service.records(research_entities, book_id, offset=offset, limit=limit)


@router.get("/runs/{run_id}", response_model=ResearchRunResponse)
def run(service: ResearchDep, run_id: str):
    return service.invoke(service.workflow.status, run_id)


@router.post("/runs/{run_id}/pause", response_model=ResearchRunResponse)
def pause(service: ResearchDep, run_id: str):
    return service.control(run_id, "pause")


@router.post("/runs/{run_id}/resume", response_model=ResearchRunResponse)
def resume(service: ResearchDep, run_id: str):
    return service.control(run_id, "resume")


@router.get("/cards/{card_id}", response_model=ResearchCardResponse)
def card(service: ResearchDep, card_id: str):
    return service.card(card_id)


@router.get("/entities/{entity_id}", response_model=ResearchEntityResponse)
def entity(service: ResearchDep, entity_id: str):
    return service.entity(entity_id)


@router.post("/cards/{card_id}/decision", response_model=ResearchCardResponse)
def decision(service: ResearchDep, card_id: str, body: ResearchDecision):
    return service.decide(card_id, body)


@router.get("/cards/{card_id}/export")
def export(service: ResearchDep, card_id: str, request: Request):
    return Response(
        service.export(card_id, base_url=str(request.base_url).rstrip("/")),
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="research-{card_id}.md"'},
    )


@router.get("/search", response_model=ResearchSearchResponse)
def search(
    service: ResearchDep,
    q: str = Query(min_length=1, max_length=1000),
    book_id: UUID | None = None,
    semantic: bool = True,
    limit: int = Query(8, ge=1, le=50),
):
    return service.search(q, book_id, semantic=semantic, limit=limit)
