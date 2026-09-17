"""Local Qwen visual gate over an original-first reviewed, explicitly fictional image.

The text-pass receipt is a fixture to isolate vision; this is not an end-to-end
text-generation or historical acceptance test. All records use the test schema.
"""

import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import insert, select

from hrs_platform import models as db
from hrs_platform.services.storage import objects_for
from hrs_platform.services.cards import Cards
from hrs_platform.services.models.vision import result_key


@pytest.mark.skipif(os.environ.get("PLATFORM_TEST_VISION") != "1", reason="Opt-in real local Qwen GPU review")
def test_local_visual_mismatch_prevents_adoption_and_durable_verdicts_are_reused(platform):
    settings, engine = platform
    objects = objects_for(settings)
    original = Path(__file__).parent / "fixtures/visual-quantities.png"
    image = objects.put_file(original, "image/png")
    bundle = objects.put_bytes(
        json.dumps(
            {"manifest": {"pages": [{"page": 1, "image": "page.png"}]}, "files": {"page.png": image}}
        ).encode()
    )
    book, run = str(uuid4()), str(uuid4())
    with engine.begin() as connection:
        connection.execute(
            insert(db.books).values(id=book, title="Local vision technical gate", state="ready")
        )
        connection.execute(
            insert(db.runs).values(
                id=run,
                book_id=book,
                kind="cards",
                state="processing",
                stage="vision",
                source={"sha256": hashlib.sha256(original.read_bytes()).hexdigest()},
                conversion=bundle,
            )
        )
    cards = Cards(settings, engine)
    parent = cards.outputs.node(
        run,
        "fixture-parent",
        kind="component",
        label="Technical visual test",
        objective="Verify visual gating only",
    )
    generated = []
    for amount in (120, 130):
        quote = f"Road transport: {amount} tons."
        candidate = {
            "title": f"Technical quote {amount}",
            "items": [
                {
                    "item_id": f"quantity-{amount}",
                    "kind": "evidence",
                    "text": quote,
                    "supplied_quote": quote,
                    "selections": [{"unit_id": "u", "quote": quote, "occurrence": 0}],
                }
            ],
        }
        source_step = f"fixture-source-{amount}"
        cards.outputs.put(
            run,
            source_step,
            {
                "units": [
                    {
                        "unit_id": "u",
                        "text": quote,
                        "sources": [{"unit_start": 0, "unit_end": len(quote), "pages": [1]}],
                    }
                ]
            },
            {},
        )
        generated.append(
            {
                "id": str(uuid4()),
                "candidate": candidate,
                "source_step": source_step,
                "parent_node": parent,
                "text_check": {
                    "conclusion": "pass",
                    "unverified_object_ids": [],
                    "findings": [],
                    "technical_fixture": True,
                },
            }
        )
    cards.outputs.put(run, "generated-cards", {"cards": generated}, {})
    # Text checks remain explicit fixtures in this vision-only test.
    cards.outputs.put(run, "finalized-cards", {"cards": generated}, {"technical_fixture": True})
    try:
        cards.check_images(run)
        for card in generated:
            check = cards.outputs.get(run, result_key(card["id"], "pages-1"))
            assert all(not observation["printed_page"] for observation in check["page_observations"])
        result = cards.adopt(run)
        assert result["adopted"] == 1 and result["state"] == "needs_revision"
        rows = {row["title"]: row["state"] for row in cards.list(book)}
        assert rows == {"Technical quote 120": "adopted", "Technical quote 130": "needs_revision"}
        with engine.connect() as connection:
            before = connection.execute(select(db.stage_outputs.c.step)).scalars().all()
        cards.check_images(run)
        with engine.connect() as connection:
            assert connection.execute(select(db.stage_outputs.c.step)).scalars().all() == before
    finally:
        with engine.connect() as connection:
            report = {
                "classification": "synthetic visual gate; text pass is a fixture; not historical approval",
                "source": image,
                "stages": [dict(row) for row in connection.execute(select(db.stage_outputs)).mappings()],
                "cards": [dict(row) for row in connection.execute(select(db.cards)).mappings()],
            }
        (settings.project_root / "output/refactor-v2/local-visual-gate-evidence.json").write_text(
            json.dumps(report, ensure_ascii=False, default=str, indent=2), encoding="utf-8"
        )
