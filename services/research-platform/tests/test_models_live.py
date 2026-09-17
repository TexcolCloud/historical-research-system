"""Opt-in production text protocol check using synthetic text, no historical approval."""

import asyncio
import os
from uuid import uuid4

import pytest
from pydantic import BaseModel
from sqlalchemy import delete, insert

from hrs_platform import models as db
from hrs_platform.services.models.text import Models


class Receipt(BaseModel):
    acknowledgement: str


@pytest.mark.skipif(
    os.environ.get("PLATFORM_TEST_MODELS") != "1", reason="Explicit paid text-model integration check"
)
def test_real_text_response_is_preserved_and_reused(platform):
    settings, engine = platform
    book, run = str(uuid4()), str(uuid4())
    with engine.begin() as connection:
        connection.execute(insert(db.books).values(id=book, title="技术测试：模型回执", state="processing"))
        connection.execute(
            insert(db.runs).values(id=run, book_id=book, kind="cards", state="processing", stage="test")
        )
    models = Models(settings, engine)

    async def exercise():
        result = await models.run(
            run,
            "协议验收",
            "这是技术连通性检查，不是史料内容审核。将 acknowledgement 设为 ready。",
            {"test": "protocol"},
            Receipt,
        )
        assert result.acknowledgement == "ready"
        # Simulate a crash after the provider response is durable but before the
        # validated stage is committed. Only this isolated technical row is removed.
        with engine.begin() as connection:
            connection.execute(
                delete(db.stage_outputs).where(
                    db.stage_outputs.c.run_id == run, db.stage_outputs.c.step == "协议验收"
                )
            )
        again = await models.run(
            run,
            "协议验收",
            "这是技术连通性检查，不是史料内容审核。将 acknowledgement 设为 ready。",
            {"test": "protocol"},
            Receipt,
        )
        assert again == result
        assert models.outputs.get(run, "协议验收:request:2") is None

    asyncio.run(exercise())
