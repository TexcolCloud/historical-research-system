"""Research routes are separate from retired legacy card-generation interfaces."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response

from hrs.api.deps import ResearchDep
from hrs.research.schemas import (
    CardNotesResponse,
    CardNotesUpdate,
    EditableCardResponse,
    ResearchBookResponse,
    ResearchCardPage,
    ResearchCardResponse,
    ResearchDecision,
    ResearchEntityPage,
    ResearchEntityResponse,
    ResearchNetworkEntityPage,
    ResearchNetworkResponse,
    ResearchRelationshipResponse,
    ResearchRunResponse,
    ResearchSearchResponse,
    ResearchTimelineResponse,
    StartResearch,
)
from hrs.research.workspace import (
    CardRevision,
    IdentityDecision,
    QuestionCreate,
    QuestionEvidence,
    QuestionUpdate,
    SelectionUpdate,
    Version,
)

router = APIRouter(prefix="/api/v2/research", tags=["research"])


def _retired_research_write():
    from hrs.library.errors import ServiceError

    raise ServiceError(410, "研究问题工作台已停用；历史记录仍可查看，史料卡会自动制作。")


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
    return service.list_cards(book_id, offset=offset, limit=limit)


@router.get("/books/{book_id}/entities", response_model=ResearchEntityPage)
def entities(
    service: ResearchDep,
    book_id: UUID,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
):
    return service.list_entities(book_id, offset=offset, limit=limit)


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


@router.get("/entities", response_model=ResearchEntityPage)
def catalogue(
    service: ResearchDep,
    book_id: UUID | None = None,
    book_ids: list[UUID] | None = Query(default=None),
    q: str | None = Query(default=None, max_length=1000),
    kind: str | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
):
    return service.workspace.catalogue(
        book_id=book_id, book_ids=book_ids, query=q, kind=kind, offset=offset, limit=limit
    )


@router.post("/entities/{entity_id}/decisions", response_model=ResearchEntityResponse)
def identity_decision(service: ResearchDep, entity_id: str, body: IdentityDecision):
    return service.workspace.identity_decision(entity_id, body)


@router.get("/cards/{card_id}/draft", response_model=EditableCardResponse)
def card_draft(service: ResearchDep, card_id: str):
    card = service.card(card_id)
    return {
        "card": service.invoke(service.workflow.card_draft, card_id),
        "version": card["version"],
        "source_current": card["source_current"],
        "superseded": card["superseded"],
        "card_only": card["card_only"],
        "processing_mode": card["processing_mode"],
    }


@router.get("/cards/{card_id}/notes", response_model=CardNotesResponse)
def card_notes(service: ResearchDep, card_id: str):
    return service.card_notes(card_id)


@router.patch("/cards/{card_id}/notes", response_model=CardNotesResponse)
def update_card_notes(service: ResearchDep, card_id: str, body: CardNotesUpdate):
    return service.update_card_notes(card_id, body)


@router.post("/cards/{card_id}/revisions", response_model=ResearchRunResponse)
def revise_card(service: ResearchDep, card_id: str, body: CardRevision):
    return service.workspace.card_revision(card_id, body)


@router.get("/pending")
def pending(
    service: ResearchDep,
    book_id: UUID | None = None,
    kind: str | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
):
    return service.workspace.pending(book_id=book_id, kind=kind, offset=offset, limit=limit)


@router.get("/reading/{chapter_id}")
def reading(service: ResearchDep, chapter_id: UUID, text_sha256: str | None = None):
    return service.workspace.reading(str(chapter_id), expected_sha256=text_sha256)


@router.get("/graph")
def graph(
    service: ResearchDep,
    entity_id: str,
    hops: int = Query(1, ge=1, le=2),
    limit: int = Query(50, ge=1, le=100),
):
    return service.workspace.graph(entity_id, hops=hops, limit=limit)


@router.get("/network", response_model=ResearchNetworkResponse)
def network(
    service: ResearchDep,
    entity_id: str,
    hops: int = Query(1, ge=1, le=2),
    limit: int = Query(100, ge=1, le=200),
    book_ids: list[UUID] | None = Query(default=None, max_length=100),
    node_kinds: list[Literal["person", "organization", "place", "document", "event", "card"]]
    | None = Query(default=None),
    predicate: str | None = Query(default=None, max_length=200),
    date_from: str | None = None,
    date_to: str | None = None,
    include_undated: bool = True,
    include_source_cards: bool = False,
):
    return service.workspace.network(
        entity_id,
        hops=hops,
        limit=limit,
        book_ids=list(map(str, book_ids)) if book_ids is not None else None,
        node_kinds=node_kinds,
        predicate=predicate,
        date_from=date_from,
        date_to=date_to,
        include_undated=include_undated,
        include_source_cards=include_source_cards,
    )


@router.get("/network/entities", response_model=ResearchNetworkEntityPage)
def network_entities(
    service: ResearchDep,
    q: str | None = Query(default=None, max_length=1000),
    kind: Literal["person", "organization", "place", "event", "document"] | None = None,
    book_ids: list[UUID] | None = Query(default=None, max_length=100),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
):
    return service.workspace.network_entities(
        q=q,
        kind=kind,
        book_ids=list(map(str, book_ids)) if book_ids is not None else None,
        offset=offset,
        limit=limit,
    )


@router.get("/network/relations/{relation_id}", response_model=ResearchRelationshipResponse)
def network_relation(service: ResearchDep, relation_id: str):
    return service.workspace.network_relation(relation_id)


@router.get("/timeline", responses={200: {"model": ResearchTimelineResponse}})
def timeline(
    service: ResearchDep,
    entity_id: str | None = None,
    book_id: UUID | None = None,
    book_ids: list[UUID] | None = Query(default=None, max_length=100),
    date_basis: Literal["event", "formation"] = "event",
    date_from: str | None = None,
    date_to: str | None = None,
    include_undated: bool = True,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    summary: bool = False,
):
    """Use summary=true for bounded navigation metadata; the default retains legacy evidence detail."""
    return service.workspace.timeline(
        entity_id=entity_id,
        book_id=book_id,
        book_ids=list(map(str, book_ids)) if book_ids is not None else None,
        date_basis=date_basis,
        date_from=date_from,
        date_to=date_to,
        include_undated=include_undated,
        offset=offset,
        limit=limit,
        summary=summary,
    )


@router.get("/questions")
def questions(
    service: ResearchDep,
    book_ids: list[UUID] | None = Query(default=None),
    status: str | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
):
    return service.workspace.questions(
        book_ids=list(map(str, book_ids)) if book_ids is not None else None,
        status=[status] if status else None,
        offset=offset,
        limit=limit,
    )


@router.post("/questions")
def create_question(service: ResearchDep, body: QuestionCreate):
    _retired_research_write()


@router.get("/questions/{question_id}")
def question(service: ResearchDep, question_id: str):
    return service.workspace.question(question_id)


@router.patch("/questions/{question_id}")
def update_question(service: ResearchDep, question_id: str, body: QuestionUpdate):
    _retired_research_write()


@router.post("/questions/{question_id}/evidence")
def select_evidence(service: ResearchDep, question_id: str, body: QuestionEvidence):
    _retired_research_write()


@router.patch("/questions/{question_id}/evidence/{selection_id}")
def update_selection(
    service: ResearchDep, question_id: str, selection_id: str, body: SelectionUpdate
):
    _retired_research_write()


@router.post("/questions/{question_id}/research")
def research_question(service: ResearchDep, question_id: str, body: Version):
    _retired_research_write()


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
    book_ids: list[UUID] | None = Query(default=None),
    semantic: bool = True,
    limit: int = Query(8, ge=1, le=50),
):
    return service.search(
        q,
        book_id,
        book_ids=list(map(str, book_ids)) if book_ids is not None else None,
        semantic=semantic,
        limit=limit,
    )
