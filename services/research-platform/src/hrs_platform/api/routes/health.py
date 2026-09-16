from fastapi import APIRouter
from sqlalchemy import text

from hrs_platform.api.deps import EngineDep

router = APIRouter(tags=["health"])


@router.get("/health/live")
def live():
    return {"status": "ok"}


@router.get("/health/ready")
def ready(engine: EngineDep):
    with engine.connect() as connection:
        connection.execute(text("SELECT 1 FROM alembic_version"))
    return {"status": "ready"}
