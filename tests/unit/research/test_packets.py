"""Retained task projections cannot mutate evidence or expose review candidates."""

from copy import deepcopy

import pytest

from hrs.research.models import submission_model
from hrs.research.packets import build_task_packet
from hrs.research.policy import POLICY


@pytest.mark.parametrize("version", range(1, 8))
@pytest.mark.parametrize("stage", ["author", "verify", "repair", "reverify"])
def test_task_packet_keeps_frozen_policy_sources_and_review_order(version, stage):
    policy = {
        **deepcopy(POLICY),
        "version": version,
        "card_only": version >= 6,
        "complete_research": version >= 5,
        "dossier_cards": version >= 7,
    }
    article = {"id": "article", "title": "Synthetic", "text": "😀Source", "parts": []}
    task = {
        "stage": stage,
        "article_id": "article",
        "write_contract": submission_model(policy, stage).__name__,
        "cards_ref": "withheld",
        "external_sources": ["withheld"],
    }
    cards = [{"id": "candidate", "summary": "Candidate withheld from reviewers"}]
    original = deepcopy((task, policy, article, cards))
    packet = build_task_packet(task, policy, article, [article], snapshot={}, cards=cards)
    assert (task, policy, article, cards) == original
    assert packet["policy"] == policy and packet["source_length"] == len(article["text"])
    assert packet["source_index"][0]["id"] == article["id"]
    assert "cards_ref" not in packet and "external_sources" not in packet
    assert "source_reading" in packet["instructions"]
    if stage in {"verify", "reverify"}:
        assert "cards" not in packet
    if stage == "repair":
        assert packet["cards"] == cards
