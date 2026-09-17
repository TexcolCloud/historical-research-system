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
        try:
            yield
        finally:
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
    return app
