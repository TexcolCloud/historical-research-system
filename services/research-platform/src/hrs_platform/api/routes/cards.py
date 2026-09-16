from uuid import UUID

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse

from hrs_platform.api.deps import CardsDep, ExportsDep, ReviewDep
from hrs_platform.api.files import download
from hrs_platform.schemas import CardDetail, CardSummary

router = APIRouter(tags=["cards"])


@router.get("/api/v2/cards", response_model=list[CardSummary])
def card_list(
    cards: CardsDep,
    book_id: UUID | None = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
):
    return cards.list(book_id, offset, limit)


@router.get("/api/v2/cards/{card_id}", response_model=CardDetail)
def card_detail(cards: CardsDep, card_id: UUID):
    return cards.get(card_id)


@router.get("/api/v2/cards/{card_id}/export", response_class=FileResponse)
def export_card(review: ReviewDep, exports: ExportsDep, card_id: UUID):
    return download(review.objects, exports.card(card_id))
