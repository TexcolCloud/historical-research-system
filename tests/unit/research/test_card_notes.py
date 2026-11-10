"""Personal notes have their own revisions and never change source-card approval."""

import json
from copy import deepcopy

import pytest

from hrs.processing.research_workflow import ResearchWorkflow
from hrs.processing.store import Store
from hrs.research.card_notes import CardNotes
from tests.unit.research.test_card_only_workflow import author, review


def test_reading_absent_notes_does_not_create_a_notes_table(tmp_path):
    store = Store(tmp_path / "native")
    notes = CardNotes(store)
    with notes.ledger.connect() as connection:
        before = connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    assert notes.read({"id": "card-one", "version": 3}) == {
        "text": "",
        "version": 0,
        "updated_at": None,
    }
    with notes.ledger.connect() as connection:
        after = connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    assert before == after


def test_note_cas_and_history_are_independent_of_native_card_state_and_version(tmp_path):
    store = Store(tmp_path / "native")
    workflow = ResearchWorkflow(store)
    run = author(workflow)
    final = review(workflow, run)
    original = workflow.card(final["card_ids"][0])
    retained = deepcopy(original)
    status = workflow.status(run["run_id"])
    notes = CardNotes(store)
    first = notes.save(original, 0, "个人判断，尚未找到复函。")
    assert first["version"] == 1 and first["origin"] == "personal_note"
    assert first["card_version"] == original["version"]
    assert notes.save(original, 1, first["text"]) == first
    with pytest.raises(ValueError, match="新版本"):
        notes.save(original, 0, "过期输入不能覆盖。")
    second = notes.save(original, 1, "已找到线索，仍须核对。")
    assert second["version"] == 2 and notes.read(original) == second
    with notes.ledger.connect() as connection:
        history = connection.execute(
            "SELECT version,payload FROM card_note_revisions WHERE lineage_id=? ORDER BY version",
            (original.get("lineage_id") or original["id"],),
        ).fetchall()
    assert [row[0] for row in history] == [1, 2]
    assert [json.loads(row[1])["text"] for row in history] == [first["text"], second["text"]]
    assert original == retained == workflow.card(original["id"])
    assert workflow.status(run["run_id"]) == status


def test_known_card_lineage_keeps_personal_notes_across_an_independently_verified_revision(
    tmp_path,
):
    store = Store(tmp_path / "native")
    workflow = ResearchWorkflow(store)
    run = author(workflow)
    complete = review(workflow, run)
    original = workflow.card(complete["card_ids"][0])
    notes = CardNotes(store)
    first = notes.save(original, 0, "这份函件需要结合回函阅读。")
    edited = workflow.card_draft(original["id"])
    edited["summary"] = "修订卡片概要，原文证据保持。"
    revision = workflow.card_revision(original["id"], original["version"], edited)
    final = review(workflow, revision, worker="revision-review")
    revised = workflow.card(final["card_ids"][0])
    assert revised["id"] != original["id"]
    assert revised["lineage_id"] == (original.get("lineage_id") or original["id"])
    assert notes.read(revised) == first
    retained = deepcopy(revised)
    second = notes.save(revised, first["version"], "新增个人备注，不改制卡核验结果。")
    assert second["card_id"] == revised["id"] and second["version"] == 2
    assert notes.read(original) == second
    assert workflow.card(revised["id"]) == retained
    assert workflow.card(original["id"]) == original
    assert notes.read({"id": "unrelated-card", "version": 1})["version"] == 0
