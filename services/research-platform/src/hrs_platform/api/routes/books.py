from uuid import UUID

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse

from hrs_platform.api.deps import CardsDep, EngineDep, ExportsDep, LibraryDep, ReviewDep
from hrs_platform.api.files import download
from hrs_platform.schemas import BookSummary, ChapterSummary, DeletionReceipt, RecoveryRequest, RunSummary
from hrs_platform.services import books
from hrs_platform.services.deletion import request_deletion

router = APIRouter(tags=["books"])


@router.get("/api/v2/books", response_model=list[BookSummary])
def list_books(
    engine: EngineDep, offset: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=500)
):
    return books.list_books(engine, offset, limit)


@router.get("/api/v2/books/{book_id}", response_model=BookSummary)
def get_book(engine: EngineDep, book_id: UUID):
    return books.get_book(engine, book_id)


@router.delete("/api/v2/books/{book_id}", response_model=DeletionReceipt, status_code=202)
def delete_book(engine: EngineDep, book_id: UUID):
    return request_deletion(engine, book_id)


@router.get("/api/v2/books/{book_id}/runs", response_model=list[RunSummary])
def book_runs(engine: EngineDep, book_id: UUID):
    return books.list_runs(engine, book_id)


@router.post("/api/v2/books/{book_id}/card-runs", response_model=RunSummary, status_code=201)
def start_cards(cards: CardsDep, book_id: UUID, request: RecoveryRequest):
    return cards.start(book_id, request.request_id)


@router.get("/api/v2/books/{book_id}/chapters", response_model=list[ChapterSummary])
def chapters(library: LibraryDep, book_id: UUID):
    return library.chapters(book_id)


@router.get("/api/v2/books/{book_id}/export", response_class=FileResponse)
def export_book(review: ReviewDep, exports: ExportsDep, book_id: UUID):
    return download(review.objects, exports.book(book_id))
