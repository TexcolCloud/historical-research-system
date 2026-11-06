"""Synthetic, read-only card detail API checks; no generation or model calls."""

from unittest.mock import MagicMock
from uuid import uuid4

from hrs.config import Settings
from hrs.library.cards import Cards


def test_detail_keeps_older_candidate_without_item_ids_readable(monkeypatch):
    monkeypatch.setattr("hrs.library.review.Review.read_json", lambda self, reference: reference)
    engine = MagicMock()
    candidate = {"items": [{"selections": [{"unit_id": "u", "quote": "原文", "occurrence": 0}]}]}
    content = {"candidate": candidate, "units": [{"unit_id": "u", "text": "原文"}]}
    engine.connect.return_value.__enter__.return_value.execute.return_value.mappings.return_value.one_or_none.return_value = {
        "content": content,
        "checks": {},
    }
    detail = Cards(Settings.load(), engine).get(str(uuid4()))
    assert detail["candidate"] == candidate
    assert detail["units"] == content["units"]
    assert detail["quote_locations"] == []
