"""Application composition and resource lifetime; routes live under api/."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from hrs_platform.api.main import api_router
from hrs_platform.core.config import Settings
from hrs_platform.core.db import engine_for
from hrs_platform.domain.errors import Problem, ServiceError
from hrs_platform.services.cards import Cards
from hrs_platform.services.documents.library import Library
from hrs_platform.services.documents.review import Review
from hrs_platform.services.exports import Exports
from hrs_platform.services.runs.outputs import Outputs


def create_app(settings=None, engine=None):
    settings = settings or Settings.load()
    owned_engine = engine is None
    engine = engine if engine is not None else engine_for(settings)

    @asynccontextmanager
    async def lifespan(app):
        bridge = None
        if owned_engine and settings.processing_backend == "codex":
            from hrs_platform.services.codex_bridge import CodexBridge

            bridge = CodexBridge(settings, engine)
            app.state.codex_bridge = bridge
            bridge.start()
        try:
            yield
        finally:
            if bridge:
                bridge.stop()
            if owned_engine:
                engine.dispose()

    app = FastAPI(title="Historical Research Platform", version="2.0.0", lifespan=lifespan)
    app.state.settings, app.state.engine = settings, engine
    app.state.review = Review(settings, engine)
    app.state.library = Library(settings, engine)
    app.state.outputs = Outputs(settings, engine)
    app.state.cards = Cards(settings, engine)
    app.state.exports = Exports(settings, engine)

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
    from pathlib import Path

    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    distribution = Path(settings.project_root) / "services/review-workbench/dist"
    if distribution.is_dir():
        app.mount("/assets", StaticFiles(directory=distribution / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def workbench(path: str):
            if path.startswith(("api/", "internal/", "health/", "uploads/")):
                from fastapi import HTTPException

                raise HTTPException(404)
            return FileResponse(distribution / "index.html", headers={"Cache-Control": "no-cache"})

    return app
