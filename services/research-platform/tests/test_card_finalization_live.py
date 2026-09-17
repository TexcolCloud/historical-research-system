"""Resume actual model evidence from a labelled synthetic run; no new OCR or human approval."""

import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import insert, select

from hrs_platform import models as db
from hrs_platform.services.cards import Cards


@pytest.mark.skipif(
    not os.environ.get("PLATFORM_FINALIZATION_REPLAY"),
    reason="Opt-in durable actual-model finalization replay",
)
def test_original_visual_observations_are_incorporated_by_actual_text_model(platform):
    settings, engine = platform
    original = json.loads(Path(os.environ["PLATFORM_FINALIZATION_REPLAY"]).read_text("utf-8"))
    generated = next(stage for stage in original["stages"] if stage["step"] == "generated-cards")
    run, book = generated["run_id"], str(uuid4())
    with engine.begin() as connection:
        connection.execute(insert(db.books).values(id=book, title="技术联测回执定稿复验", state="ready"))
        connection.execute(
            insert(db.runs).values(id=run, book_id=book, kind="cards", state="processing", stage="vision")
        )
        # Immutable source/model objects are reused from S3, not replaced by pass fixtures.
        for stage in original["stages"]:
            if stage["run_id"] == run:
                connection.execute(
                    insert(db.stage_outputs).values(
                        **{key: stage[key] for key in ("run_id", "step", "input_sha256", "reference")}
                    )
                )
    cards = Cards(settings, engine)
    try:
        asyncio.run(cards.finalize(run))
        result = cards.adopt(run)
        assert result["adopted"] == result["cards"] > 0
        before = cards.outputs.get(run, "generated-cards")
        after = cards.outputs.get(run, "finalized-cards")
        for old, new in zip(before["cards"], after["cards"], strict=True):
            from hrs_platform.domain.card_rules import validate_final_candidate
            from hrs_platform.domain.generation_contracts import CardDraft

            source = cards.outputs.get(run, old["source_step"])
            validate_final_candidate(
                CardDraft.model_validate(new["candidate"]),
                CardDraft.model_validate(old["candidate"]),
                source["units"],
            )
            prose = json.dumps(
                [item for item in new["candidate"]["items"] if item["kind"] != "evidence"], ensure_ascii=False
            )
            assert "尚待原页" not in prose and "original_start" not in prose and "source_record" not in prose
    finally:
        with engine.connect() as connection:
            report = {
                "test": "actual text finalization of durable synthetic text and Qwen evidence; not historical approval",
                "replayed_evidence": os.environ["PLATFORM_FINALIZATION_REPLAY"],
                "stages": [dict(row) for row in connection.execute(select(db.stage_outputs)).mappings()],
                "nodes": [dict(row) for row in connection.execute(select(db.execution_nodes)).mappings()],
                "cards": [dict(row) for row in connection.execute(select(db.cards)).mappings()],
            }
        (settings.project_root / "output/refactor-v2/finalization-live-evidence.json").write_text(
            json.dumps(report, ensure_ascii=False, default=str, indent=2), "utf-8"
        )
