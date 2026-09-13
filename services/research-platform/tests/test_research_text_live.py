"""Actual text research; optional local-vision adoption over a labelled synthetic original, no OCR claim."""

import asyncio
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import insert, select

from hrs_platform import schema as db
from hrs_platform.activities import objects_for
from hrs_platform.cards import Cards
from hrs_platform.library import Library


@pytest.mark.skipif(os.environ.get("PLATFORM_TEST_MODELS") != "1", reason="Opt-in actual text-model workflow")
def test_organization_reading_and_card_protocols_use_complete_synthetic_source(platform):
    settings, engine = platform
    with_vision = os.environ.get("PLATFORM_TEST_FULL_MODELS") == "1"
    objects = objects_for(settings)
    book, run = str(uuid4()), str(uuid4())
    text = (
        "# 软件验收虚构材料\n以下两章为软件测试用的虚构材料，不是实际史实。\n\n"
        "# 第一章 运输记录\n1938年1月，甲县运输处记载当月运送粮食120吨。"
        "记录只涵盖县城至乙镇的公路运输，不包括水运。记录未说明损耗数量，因此不能把120吨视为实际入库量。\n\n"
        "# 第二章 仓储记录\n同月乙镇仓库登记入库粮食110吨，登记范围还包括其他路线的来货。"
        "两份记录统计范围不同，不能直接据此推断运输损耗为10吨。以上数字仅用于验证软件能否保留数量口径和否定条件。\n"
    )
    content = objects.put_bytes(text.encode("utf-8"), "text/markdown")
    boundary = objects.put_bytes(json.dumps({"pages": [{"page": 1, "start": 0, "end": len(text)}]}).encode())
    manifest = {"candidate_markdown": "candidate.md", "page_boundaries": "pages.json", "page_count": 1}
    files = {"candidate.md": content, "pages.json": boundary}
    if with_vision:
        image = Path(__file__).parent / "fixtures/synthetic-transport-original.png"
        files["original.png"] = objects.put_file(image, "image/png")
        manifest["pages"] = [{"page": 1, "image": "original.png"}]
    bundle = objects.put_bytes(json.dumps({"manifest": manifest, "files": files}).encode())
    with engine.begin() as connection:
        connection.execute(
            insert(db.books).values(
                id=book, title="技术测试：虚构的运输与仓储记录", state="ready_for_ingestion"
            )
        )
        connection.execute(
            insert(db.runs).values(
                id=run,
                book_id=book,
                state="ready_for_ingestion",
                stage="organization",
                source={"sha256": hashlib.sha256(text.encode()).hexdigest()},
                conversion=bundle,
                result={"review_initialized": True},
            )
        )
    library = Library(settings, engine)

    async def exercise():
        organization = await library.organize(run)
        assert "".join(part["text"] for group in organization["groups"] for part in group["parts"]) == text
        library.publish(run)
        cards = Cards(settings, engine)
        child = cards.create_run(run)["run_id"]
        result = await cards.generate(child)
        assert result["candidates"] > 0
        generated = cards.outputs.get(child, "generated-cards")
        assert all(card["candidate"]["items"] for card in generated["cards"])
        warehouse = [
            card
            for card in generated["cards"]
            if any(
                "仓库" in selection["quote"]
                for item in card["candidate"]["items"]
                for selection in item["selections"]
            )
        ]
        assert warehouse and all(card["candidate"]["event_date"]["start_year"] == 1938 for card in warehouse)
        # Candidate generation is not visual verification or adopted content.
        assert cards.list(book) == []
        if with_vision:
            for card in generated["cards"]:
                prose = json.dumps(card["candidate"], ensure_ascii=False)
                assert "未提供原页图" not in prose and "未提供全书" not in prose
            # Actual text-generation output enters the actual local vision gate.
            # No fixed text-pass receipt or historical human approval is supplied.
            cards.check_images(child)
            await cards.finalize(child)
            adoption = cards.adopt(child)
            assert adoption["adopted"] == result["candidates"]
            assert adoption["state"] == "completed"

    try:
        asyncio.run(exercise())
    finally:
        with engine.connect() as connection:
            report = {
                "test": "synthetic text + local vision + adoption; not historical approval"
                if with_vision
                else "labelled synthetic text; not historical or visual approval",
                "book_id": book,
                "run_id": run,
                "nodes": [dict(row) for row in connection.execute(select(db.execution_nodes)).mappings()],
                "stages": [dict(row) for row in connection.execute(select(db.stage_outputs)).mappings()],
                "cards": [dict(row) for row in connection.execute(select(db.cards)).mappings()],
            }
        path = (
            settings.project_root
            / "output/refactor-v2"
            / os.environ.get("PLATFORM_TEXT_EVIDENCE", "research-text-context-evidence.json")
        )
        path.write_text(json.dumps(report, ensure_ascii=False, default=str, indent=2), encoding="utf-8")
