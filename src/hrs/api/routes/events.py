import asyncio
import json

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from hrs.api.deps import (
    EngineDep,
)
from hrs.api.schemas import WorkspaceSnapshot
from hrs.library.events import read_events, workspace_snapshot

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
        stopping = request.app.state.shutdown_requested
        while not stopping.is_set() and not await request.is_disconnected():
            rows = await asyncio.to_thread(read_events, engine, cursor)
            for row in rows:
                if stopping.is_set():
                    return
                cursor = row["sequence"]
                payload = json.dumps(dict(row), default=str, ensure_ascii=False)
                yield f"id: {cursor}\ndata: {payload}\n\n"
            if not rows:
                if stopping.is_set():
                    return
                yield ": keepalive\n\n"
                await asyncio.to_thread(stopping.wait, 1)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
