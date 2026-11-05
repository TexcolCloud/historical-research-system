import asyncio
import json

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from hrs_platform.api.deps import (
    EngineDep,
)
from hrs_platform.schemas import WorkspaceSnapshot
from hrs_platform.services.events import read_events, workspace_snapshot

router = APIRouter(tags=["events"])


@router.get("/api/v2/events/snapshot", response_model=WorkspaceSnapshot)
def snapshot(engine: EngineDep):
    return workspace_snapshot(engine)


@router.get("/api/v2/events")
async def events(
    engine: EngineDep,
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
