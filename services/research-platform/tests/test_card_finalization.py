import asyncio
from copy import deepcopy
from uuid import uuid4

import pytest
from sqlalchemy import insert

from hrs_platform import models as db
from hrs_platform.services.cards import Cards
from hrs_platform.services.cards import validate_final_candidate
from hrs_platform.domain.generation_contracts import CardDraft
from hrs_platform.services.visual_review import result_key


def draft():
    return CardDraft(
        title="虚构技术材料",
        document_type="测试",
        source_layer="测试原文",
        formation_date={},
        event_date={},
        tags=[],
        entities=[],
        evidence_relations=[],
        no_argument_reason="仅测试原图观察回流",
        items=[
            {
                "item_id": "e",
                "kind": "evidence",
                "title": "原文",
                "text": "运送120吨。",
                "source_unit_ids": ["u"],
                "selections": [{"unit_id": "u", "quote": "运送120吨。"}],
            },
            {
                "item_id": "s",
                "kind": "section",
                "title": "版面",
                "text": "尚待原页核验。",
                "evidence_refs": ["e"],
            },
        ],
    )


def test_final_narrative_cannot_change_visually_checked_evidence():
    original = draft()
    revised = original.model_copy(deep=True)
    revised.items[0].context = "同页第一章，作为运输口径的证据。"
    validate_final_candidate(revised, original, [{"unit_id": "u", "text": "运送120吨。"}])
    revised.items[0].text = "运送130吨。"
    with pytest.raises(ValueError, match="preserve every visually checked"):
        validate_final_candidate(revised, original, [{"unit_id": "u", "text": "运送120吨。"}])


@pytest.mark.parametrize("final_pass,source_pass", [(True, True), (False, True), (True, False)])
def test_actual_observations_reach_final_review_and_only_finalized_cards_can_be_adopted(
    platform, final_pass, source_pass
):
    settings, engine = platform
    book, run, identity = [str(uuid4()) for _ in range(3)]
    cards = Cards(settings, engine)
    with engine.begin() as connection:
        connection.execute(insert(db.books).values(id=book, title="技术定稿测试", state="ready"))
        connection.execute(
            insert(db.runs).values(id=run, book_id=book, kind="cards", state="processing", stage="vision")
        )
    candidate = draft().model_dump(mode="json")
    check = {
        "checked_object_ids": ["e", "s"],
        "findings": [],
        "unverified_object_ids": [],
        "conclusion": "pass",
        "reasoning_summary": "测试回执",
    }
    generated = {
        "cards": [
            {
                "id": identity,
                "candidate": candidate,
                "source_step": "source",
                "parent_node": None,
                "text_check": check,
            }
        ]
    }
    cards.outputs.put(run, "generated-cards", generated, {})
    cards.outputs.put(
        run,
        "source",
        {
            "assigned_unit_ids": ["u"],
            "units": [
                {
                    "unit_id": "u",
                    "text": "运送120吨。",
                    "sources": [{"unit_start": 0, "unit_end": 7, "pages": [1]}],
                }
            ],
        },
        {},
    )
    cards.outputs.put(
        run,
        result_key(identity, "pages-1"),
        {
            "items": [{"item_id": "e", "result": "verified"}],
            "page_observations": [{"printed_page": "", "layout_and_reading_order": ["标题后接一段正文"]}],
        },
        {},
    )
    with pytest.raises(ValueError, match="before adoption"):
        cards.adopt(run)
    calls = []

    class Models:
        async def run(self, run_id, key, prompt, payload, output_type, **kwargs):
            calls.append(key)
            assert payload["research_state"]["available_units_cover_whole_book"]
            if ":source-coverage:" in key:
                assert payload["source_units"][0]["unit_id"] == "u"
                value = {
                    **check,
                    "checked_object_ids": ["u"],
                    "conclusion": "pass" if source_pass else "needs_revision",
                }
            elif key.endswith(":修订"):
                assert payload["original_checks"][0]["page_observations"][0]["printed_page"] == ""
                value = deepcopy(payload["candidate"])
                value["items"][1]["text"] = "原图为标题后接一段正文，未见印刷页码。"
            else:
                value = {
                    **check,
                    "conclusion": "needs_revision" if len(calls) == 1 or not final_pass else "pass",
                }
            result = output_type.model_validate(value)
            kwargs["validate"](result)
            return result

    cards.models = Models()
    asyncio.run(cards.finalize(run))
    count = (4 if source_pass else 7) if final_pass else 5
    assert len(calls) == count
    asyncio.run(cards.finalize(run))
    assert len(calls) == count
    assert cards.adopt(run)["adopted"] == int(final_pass and source_pass)
    adopted = cards.get(identity)
    assert adopted["candidate"]["items"][0] == candidate["items"][0]
    assert "未见印刷页码" in adopted["candidate"]["items"][1]["text"]
    assert adopted["verdict"]["human_approval"] is False
    assert adopted["verdict"]["original_candidate"] == candidate
