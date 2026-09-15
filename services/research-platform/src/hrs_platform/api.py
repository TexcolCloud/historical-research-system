import asyncio
import json
from contextlib import asynccontextmanager
from urllib.parse import quote
from uuid import UUID

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from sqlalchemy import func, select, text, update

from . import books, schema
from .cards import Cards
from .contracts import (
    BookSummary,
    CardDetail,
    CardSummary,
    Chapter,
    ChapterSummary,
    ConversionPage,
    DeletionReceipt,
    DraftReceipt,
    ExecutionNode,
    RecoveryRequest,
    ReviewDecision,
    ReviewDraft,
    ReviewIssue,
    ReviewIssueSummary,
    ReviewReceipt,
    RunSummary,
    SearchHit,
    StructureReport,
    TusHook,
    UploadRequest,
    UploadSession,
)
from .database import engine_for
from .deletion import request_deletion
from .domain.errors import Problem
from .exports import Exports
from .library import Library
from .outputs import Outputs
from .recovery import retry_run
from .review import Review
from .search import Search
from .settings import Settings


def read_events(engine, position):
    """Assign delivery order only to committed events, serialized across readers."""
    with engine.begin() as connection:
        connection.execute(text("SELECT pg_advisory_xact_lock(1388427267)"))
        pending = connection.scalars(
            select(schema.events.c.sequence)
            .where(schema.events.c.delivery_sequence.is_(None))
            .order_by(schema.events.c.sequence)
            .limit(500)
        ).all()
        for identity in pending:
            connection.execute(
                update(schema.events)
                .where(schema.events.c.sequence == identity)
                .values(delivery_sequence=func.nextval("event_delivery_sequence"))
            )
        rows = (
            connection.execute(
                select(schema.events)
                .where(schema.events.c.delivery_sequence > position)
                .order_by(schema.events.c.delivery_sequence)
                .limit(100)
            )
            .mappings()
            .all()
        )
        return [{**row, "sequence": row["delivery_sequence"]} for row in rows]


def create_app(settings=None, engine=None):
    settings = settings or Settings.load()
    owned_engine = engine is None
    engine = engine or engine_for(settings)

    @asynccontextmanager
    async def lifespan(app):
        yield
        if owned_engine:
            engine.dispose()

    app = FastAPI(title="Historical Research Platform", version="2.0.0", lifespan=lifespan)
    review = Review(settings, engine)
    library = Library(settings, engine)
    outputs = Outputs(settings, engine)
    cards = Cards(settings, engine)
    exports = Exports(settings, engine)

    @app.exception_handler(Problem)
    async def recoverable_problem(request: Request, error: Problem):
        from fastapi.responses import JSONResponse

        return JSONResponse(
            {"detail": error.detail, "code": error.code, "retryable": error.retryable},
            status_code=error.status,
        )

    def download(value):
        title, ref = value
        from .storage import object_response

        return object_response(
            review.objects,
            ref,
            headers={
                "Content-Disposition": "attachment; filename*=UTF-8''" + quote(title + ".md", safe=""),
            },
        )

    @app.get("/api/v2/books/{book_id}/export", response_class=FileResponse)
    def export_book(book_id: UUID):
        return download(exports.book(book_id))

    @app.get("/api/v2/cards/{card_id}/export", response_class=FileResponse)
    def export_card(card_id: UUID):
        return download(exports.card(card_id))

    @app.post("/api/v2/books/{book_id}/card-runs", response_model=RunSummary, status_code=201)
    def start_cards(book_id: UUID, request: RecoveryRequest):
        return cards.start(book_id, request.request_id)

    @app.post("/api/v2/runs/{run_id}/retry", response_model=RunSummary)
    def retry(run_id: UUID, request: RecoveryRequest):
        return retry_run(engine, run_id, request.request_id)

    @app.get("/api/v2/books/{book_id}/runs", response_model=list[RunSummary])
    def book_runs(book_id: UUID):
        with engine.connect() as connection:
            return list(
                connection.execute(
                    select(schema.runs)
                    .where(schema.runs.c.book_id == str(book_id))
                    .order_by(schema.runs.c.created_at.desc())
                ).mappings()
            )

    @app.get("/api/v2/cards", response_model=list[CardSummary])
    def card_list(
        book_id: UUID | None = None,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=500),
    ):
        return cards.list(book_id, offset, limit)

    @app.get("/api/v2/cards/{card_id}", response_model=CardDetail)
    def card_detail(card_id: UUID):
        return cards.get(card_id)

    @app.get("/api/v2/search", response_model=list[SearchHit])
    def search(
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
        result = Search(settings, engine).search(
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

    @app.get("/api/v2/books/{book_id}/chapters", response_model=list[ChapterSummary])
    def chapters(book_id: UUID):
        return library.chapters(book_id)

    @app.get("/api/v2/chapters/{chapter_id}", response_model=Chapter)
    def chapter(chapter_id: UUID):
        return library.chapter(chapter_id)

    @app.get("/api/v2/runs/{run_id}/executions", response_model=list[ExecutionNode])
    def executions(run_id: UUID):
        books.get_run(engine, run_id)
        return outputs.list_nodes(run_id)

    @app.get("/api/v2/executions/{execution_id}/details")
    def execution_details(execution_id: UUID):
        with engine.connect() as connection:
            row = (
                connection.execute(
                    select(schema.execution_nodes).where(schema.execution_nodes.c.id == str(execution_id))
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise HTTPException(404, "执行记录不存在。")
        return review.read_json(row["details"]) if row["details"] else {}

    @app.get("/health/live")
    def live():
        return {"status": "ok"}

    @app.get("/health/ready")
    def ready():
        with engine.connect() as connection:
            connection.execute(text("SELECT 1 FROM alembic_version"))
        return {"status": "ready"}

    @app.post("/api/v2/uploads", response_model=UploadSession, status_code=201)
    def create_upload(
        request: UploadRequest,
        idempotency_key: UUID | None = Header(default=None),
        x_upload_token: str | None = Header(default=None),
    ):
        return books.create_upload(engine, settings, request, idempotency_key, x_upload_token)

    @app.get("/api/v2/books", response_model=list[BookSummary])
    def list_books(offset: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=500)):
        return books.list_books(engine, offset, limit)

    @app.get("/api/v2/books/{book_id}", response_model=BookSummary)
    def get_book(book_id: UUID):
        return books.get_book(engine, book_id)

    @app.delete("/api/v2/books/{book_id}", response_model=DeletionReceipt, status_code=202)
    def delete_book(book_id: UUID):
        return request_deletion(engine, book_id)

    @app.get("/api/v2/runs/{run_id}", response_model=RunSummary)
    def get_run(run_id: UUID):
        return books.get_run(engine, run_id)

    @app.get("/api/v2/reviews", response_model=list[ReviewIssueSummary])
    def review_list(
        run_id: UUID | None = None,
        pending: bool = True,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=500),
    ):
        return review.list(run_id, pending, offset, limit)

    @app.get("/api/v2/reviews/{issue_id}", response_model=ReviewIssue)
    def review_issue(issue_id: UUID):
        return review.get(issue_id)

    @app.get("/api/v2/runs/{run_id}/review-pages/{page}", response_model=ConversionPage)
    def review_page(run_id: UUID, page: int):
        return review.page(run_id, page)

    @app.get("/api/v2/runs/{run_id}/structure", response_model=StructureReport)
    def reading_structure(run_id: UUID):
        return library.structure(str(run_id))

    @app.post("/api/v2/reviews/{issue_id}/decisions", response_model=ReviewReceipt)
    def review_decision(issue_id: UUID, request: ReviewDecision):
        return review.decide(issue_id, request)

    @app.put("/api/v2/reviews/{issue_id}/draft", response_model=DraftReceipt)
    def review_draft(issue_id: UUID, request: ReviewDraft):
        return review.save_draft(issue_id, request)

    @app.get("/api/v2/runs/{run_id}/artifacts/{name:path}")
    def artifact(run_id: UUID, name: str, range_header: str | None = Header(default=None, alias="Range")):
        run = books.get_run(engine, run_id)
        if name == "original.pdf":
            reference = run["source"] if run["source"].get("sha256") else None
        elif run["conversion"]:
            reference = review.read_json(run["conversion"])["files"].get(name)
        else:
            draft = review.ocr_draft(run)
            reference = draft[0].get(name) if draft else None
        if reference is None:
            raise HTTPException(404, "原件或附件尚未生成。")
        if reference["media_type"] == "application/pdf":
            from .storage import object_response

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

    @app.post("/internal/tus", include_in_schema=False)
    def tus_hook(hook: TusHook, authorization: str | None = Header(default=None)):
        try:
            return books.handle_tus(engine, settings, hook, authorization)
        except HTTPException as error:
            if hook.Type != "pre-create":
                raise
            return {
                "RejectUpload": True,
                "HTTPResponse": {"StatusCode": error.status_code, "Body": error.detail},
            }

    @app.get("/api/v2/events")
    async def events(
        request: Request,
        after: int = Query(default=0, ge=0),
        last_event_id: str | None = Header(default=None),
    ):
        try:
            cursor = max(after, int(last_event_id or 0))
        except ValueError as error:
            raise HTTPException(422, "事件游标无效。") from error

        async def stream():
            nonlocal cursor
            while not await request.is_disconnected():
                rows = await asyncio.to_thread(read_events, engine, cursor)
                for row in rows:
                    cursor = row["sequence"]
                    payload = json.dumps(dict(row), default=str, ensure_ascii=False)
                    yield f"id: {cursor}\ndata: {payload}\n\n"
                if not rows:
                    yield ": keepalive\n\n"
                    await asyncio.sleep(1)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app
