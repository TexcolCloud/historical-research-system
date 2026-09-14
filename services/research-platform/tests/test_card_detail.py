"""Synthetic, read-only card detail API checks; no generation or model calls."""

import json
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert
from test_card_finalization import draft

from hrs_platform import schema as db
from hrs_platform.api import create_app
from hrs_platform.cards import Cards
from hrs_platform.settings import Settings


@pytest.mark.parametrize("problem", [None, "missing-source", "missing-pages", "unmatched"])
@pytest.mark.parametrize("storage", ["memory", "database"])
def test_detail_exposes_exact_unicode_occurrence_and_cross_page_quote_without_guessing(
    request, monkeypatch, problem, storage
):
    settings, engine = (
        request.getfixturevalue("platform") if storage == "database" else (Settings.load(), MagicMock())
    )
    if storage == "memory":
        monkeypatch.setattr("hrs_platform.review.Review.read_json", lambda self, reference: reference)
    book, run, identity, chapter = [str(uuid4()) for _ in range(4)]
    cards = Cards(settings, engine)
    candidate = draft().model_dump(mode="json")
    quote = candidate["items"][0]["selections"][0]["quote"]
    text = f"𠮷地：{quote}\n再次记载：{quote}后续正文。"
    start = text.rfind(quote)
    selection = candidate["items"][0]["selections"][0]
    selection["occurrence"] = 1
    unit = {
        "unit_id": "u",
        "chapter_id": chapter,
        "run_id": run,
        "title": "合成运输章",
        "text": text,
        "pages": [8, 9, 10],
        "start": 100,
        "end": 100 + len(text),
        "sources": [
            {"unit_start": 0, "unit_end": start, "pages": [8]},
            {"unit_start": start, "unit_end": start + 3, "pages": [9]},
            {"unit_start": start + 3, "unit_end": len(text), "pages": [10]},
        ],
    }
    if problem == "missing-pages":
        unit.pop("sources")
    if problem == "unmatched":
        selection["quote"] = "不存在的引文"
    content = {"candidate": candidate, "units": [] if problem == "missing-source" else [unit]}
    checks = {"human_approval": False}
    if storage == "database":
        content = cards.review.objects.put_bytes(json.dumps(content, ensure_ascii=False).encode())
        checks = cards.review.objects.put_bytes(json.dumps(checks).encode())
    else:
        engine.connect.return_value.__enter__.return_value.execute.return_value.mappings.return_value.one_or_none.return_value = {
            "id": identity,
            "book_id": book,
            "run_id": run,
            "title": "合成卡",
            "state": "adopted",
            "content": content,
            "checks": checks,
            "created_at": "2026-09-14T00:00:00Z",
        }
    with engine.begin() as connection:
        connection.execute(insert(db.books).values(id=book, title="合成测试", state="ready"))
        connection.execute(
            insert(db.runs).values(id=run, book_id=book, kind="cards", state="completed", stage="complete")
        )
        connection.execute(
            insert(db.cards).values(
                id=identity,
                book_id=book,
                run_id=run,
                title="合成卡",
                state="adopted",
                content=content,
                checks=checks,
            )
        )
    with TestClient(create_app(settings, engine)) as client:
        response = client.get(f"/api/v2/cards/{identity}")
    assert response.status_code == 200
    detail = response.json()
    assert detail["candidate"] == candidate
    assert detail["verdict"]["human_approval"] is False
    location = detail["quote_locations"][0]
    assert location["item_id"] == "e" and location["selection_index"] == 0 and location["unit_id"] == "u"
    if problem in {"missing-source", "unmatched"}:
        assert location["start"] is None and location["end"] is None
    else:
        assert text[location["start"] : location["end"]] == quote
        assert location["start"] == start
    assert location["pages"] == ([] if problem else [9, 10])
    assert bool(location["issue"]) == bool(problem)
