"""Application composition and resource lifetime; routes live under api/."""

from contextlib import asynccontextmanager
from threading import Event, Lock

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from hrs.api.router import api_router
from hrs.config import Settings
from hrs.library.cards import Cards
from hrs.library.catalog import Library
from hrs.library.errors import Problem, ServiceError
from hrs.library.exports import Exports
from hrs.library.review import Review
from hrs.library.runs.outputs import Outputs
from hrs.storage.db import engine_for


def create_app(settings=None, engine=None):
    settings = settings or Settings.load()
    owned_engine = engine is None
    engine = engine if engine is not None else engine_for(settings)

    @asynccontextmanager
    async def lifespan(app):
        bridge = None
        try:
            if owned_engine and settings.processing_backend == "codex":
                from hrs.library.processing_bridge import CodexBridge

                bridge = CodexBridge(settings, engine)
                app.state.codex_bridge = bridge
                bridge.start()
            yield
        finally:
            try:
                if bridge:
                    bridge.stop()
            finally:
                if owned_engine:
                    engine.dispose()

    from hrs import __version__

    app = FastAPI(title="Historical Research Platform", version=__version__, lifespan=lifespan)
    app.state.shutdown_requested = Event()
    app.state.settings, app.state.engine = settings, engine
    app.state.review = Review(settings, engine)
    app.state.library = Library(settings, engine)
    app.state.outputs = Outputs(settings, engine)
    app.state.cards = Cards(settings, engine)
    app.state.exports = Exports(settings, engine)
    app.state.research = None
    app.state.research_lock = Lock()

    @app.exception_handler(ServiceError)
    async def rejected_operation(request: Request, error: ServiceError):
        return JSONResponse({"detail": error.detail}, status_code=error.status_code)

    @app.exception_handler(Problem)
    async def recoverable_problem(request: Request, error: Problem):
        return JSONResponse(
            {"detail": error.detail, "code": error.code, "retryable": error.retryable},
            status_code=error.status,
        )

    app.include_router(api_router)
    # Existing React workbench, API and uploads can share one local origin.
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    distribution = settings.web_dist
    if distribution.is_dir():
        app.mount("/assets", StaticFiles(directory=distribution / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def workbench(path: str):
            if path.startswith(("api/", "internal/", "health/", "uploads/")):
                from fastapi import HTTPException

                raise HTTPException(404)
            return FileResponse(distribution / "index.html", headers={"Cache-Control": "no-cache"})

    return app
