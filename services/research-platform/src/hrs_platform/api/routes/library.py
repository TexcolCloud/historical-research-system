from uuid import UUID

from fastapi import APIRouter, Query
from fastapi.responses import Response

from hrs_platform.api.deps import LibraryDep, SearchDep
from hrs_platform.schemas import Chapter, SearchHit

router = APIRouter(tags=["library"])


@router.get("/api/v2/chapters/{chapter_id}", response_model=Chapter)
def chapter(library: LibraryDep, chapter_id: UUID):
    return library.chapter(chapter_id)


@router.get("/api/v2/search", response_model=list[SearchHit])
def search(
    retrieval: SearchDep,
    response: Response,
    q: str = Query(min_length=1, max_length=1000),
    book_id: UUID | None = None,
    semantic: bool = True,
    limit: int = Query(default=8, ge=1, le=50),
    candidate_limit: int | None = Query(default=None, ge=1, le=100),
    rerank_limit: int | None = Query(default=None, ge=1, le=100),
    diverse: bool = False,
    context_chars: int = Query(default=6000, ge=1400, le=12000),
    total_chars: int = Query(default=24000, ge=1400, le=60000),
):
    metrics = {}
    result = retrieval.search(
        q,
        book_id,
        semantic,
        limit,
        candidate_limit,
        context_chars,
        total_chars,
        metrics=metrics,
        rerank_limit=rerank_limit,
        diverse=diverse,
    )
    response.headers["Server-Timing"] = ", ".join(
        f"{name.removesuffix('_ms')};dur={value:.3f}"
        for name, value in metrics.items()
        if name.endswith("_ms")
    )
    response.headers["X-Retrieval-Evidence"] = metrics.get("evidence_status", "unassessed")
    return result
