"""User research records retain definitions, selections and independent findings."""

import pytest

from hrs.processing.store import Store
from hrs.research.notebook import ResearchNotebook


def test_question_revisions_preserve_notes_and_reject_late_updates(tmp_path):
    notebook = ResearchNotebook(Store(tmp_path / "native"))
    question = notebook.create("拨款申请是否实际执行？", ["book-1"], notes="先查复函")
    updated = notebook.update(question["id"], question["version"], notes="查到申请，还须查拨付")
    assert updated["notes"] == "查到申请，还须查拨付"
    assert updated["definition_version"] == question["definition_version"]
    with pytest.raises(ValueError, match="changed"):
        notebook.update(question["id"], question["version"], notes="迟到草稿")
    assert notebook.question(question["id"])["notes"] == updated["notes"]
    assert notebook.history(question["id"])["total"] == 2


def retained_selection(count=1):
    return {
        "kind": "card",
        "target_id": "complete-card",
        "evidence": [
            {
                "id": f"e-{index}",
                "start": index,
                "end": index + 1,
                "quote": "字",
                "text_sha256": "a" * 64,
            }
            for index in range(count)
        ],
        "record_ref": {"kind": "card", "id": "complete-card", "version": 1},
        "version_refs": [{"card_id": "complete-card", "card_version": 1}],
    }


def test_complete_card_selection_keeps_all_trusted_evidence_and_record_versions(tmp_path):
    notebook = ResearchNotebook(Store(tmp_path / "native"))
    question = notebook.create("完整文章的依据是否保留", [])
    selection = retained_selection(133)
    result = notebook.select(question["id"], question["version"], selection, trusted_evidence=True)
    saved = result["selections"][0]
    assert saved["evidence"] == selection["evidence"]
    assert saved["record_ref"] == selection["record_ref"]
    assert saved["version_refs"] == selection["version_refs"]
    assert saved["decision"] == "saved"
    assert "approval_type" not in saved
    revised = retained_selection(133)
    revised["record_ref"]["version"] = 2
    revised["version_refs"][0]["card_version"] = 2
    result = notebook.select(result["id"], result["version"], revised, trusted_evidence=True)
    assert len(result["selections"]) == 2
    assert {item["record_ref"]["version"] for item in result["selections"]} == {1, 2}


def test_explicit_selection_matches_api_limit_and_keeps_snapshot_bound(tmp_path):
    notebook = ResearchNotebook(Store(tmp_path / "native"))
    question = notebook.create("检验显式选材上限", [])
    result = notebook.select(question["id"], question["version"], retained_selection(100))
    assert len(result["selections"][0]["evidence"]) == 100
    with pytest.raises(ValueError, match="100"):
        notebook.select(result["id"], result["version"], retained_selection(101))
    oversized = retained_selection()
    oversized["evidence"][0].update(quote="字" * 120001, end=120001)
    with pytest.raises(ValueError, match="snapshot"):
        notebook.select(result["id"], result["version"], oversized)
    assert len(notebook.question(question["id"])["selections"]) == 1


@pytest.mark.parametrize(
    "updates",
    [
        {"record_ref": {"kind": "card", "id": "other", "version": 1}},
        {"record_ref": {"kind": "card", "id": "complete-card", "version": True}},
        {"record_ref": {"kind": "card", "id": "complete-card", "sha256": "bad"}},
        {"version_refs": [{"card_id": "complete-card", "card_version": 0}]},
        {"record_ref": {"kind": "card", "id": "complete-card", "approval_type": "human"}},
    ],
)
def test_selection_record_binding_has_no_approval_or_unversioned_identity(tmp_path, updates):
    notebook = ResearchNotebook(Store(tmp_path / "native"))
    question = notebook.create("保留派生版本", [])
    with pytest.raises(ValueError):
        notebook.select(question["id"], question["version"], retained_selection() | updates)
    assert notebook.question(question["id"])["selections"] == []


def test_selected_evidence_retains_versions_and_conflicts_without_granting_approval(tmp_path):
    notebook = ResearchNotebook(Store(tmp_path / "native"))
    question = notebook.create("申请是否执行", ["book-1"])
    selected = notebook.select(
        question["id"],
        question["version"],
        {
            "kind": "source",
            "target_id": "chapter-1",
            "title": "拨款申请",
            "decision": "saved",
            "note": "只能证明申请",
            "evidence": [
                {
                    "book_id": "book-1",
                    "chapter_id": "chapter-1",
                    "start": 0,
                    "end": 4,
                    "quote": "申请拨款",
                    "text_sha256": "a" * 64,
                }
            ],
        },
    )
    selection = selected["selections"][0]
    adopted = notebook.set_selection(
        question["id"],
        selection["id"],
        selected["version"],
        decision="adopted",
        note="用于提出请求的论述",
    )
    assert adopted["selections"][0]["evidence"] == selection["evidence"]
    assert adopted["selections"][0]["decision"] == "adopted"
    assert adopted["status"] == "open"
    assert "approval_type" not in adopted["selections"][0]
    with pytest.raises(ValueError, match="changed"):
        notebook.set_selection(
            question["id"], selection["id"], selected["version"], decision="excluded"
        )


def test_only_verified_findings_are_retained_and_never_resolve_the_user_question(tmp_path):
    notebook = ResearchNotebook(Store(tmp_path / "native"))
    question = notebook.create("申请是否执行", ["book-1"])
    finding = {
        "id": "f1",
        "question_id": question["id"],
        "definition_version": 1,
        "text": "本件仅能证明申请",
        "status": "partial",
        "evidence_ids": ["e1"],
        "unresolved": ["尚缺拨付凭证"],
    }
    card = {
        "id": "card-1",
        "version": 1,
        "book_id": "book-1",
        "run_id": "run-1",
        "state": "pending",
        "question_findings": [finding],
        "evidence": [
            {
                "id": "e1",
                "article_id": "article-1",
                "start": 0,
                "end": 4,
                "quote": "申请拨款",
                "text_sha256": "a" * 64,
            }
        ],
    }
    notebook.sync_card(card)
    assert notebook.question(question["id"])["findings"] == []
    card.update(state="verified", approval_type="native", checks={"decision": "approve"})
    notebook.sync_card(card)
    notebook.sync_card(card)
    stored = notebook.question(question["id"])
    assert len(stored["findings"]) == 1
    assert stored["findings"][0]["evidence"][0]["quote"] == "申请拨款"
    assert stored["status"] == "open"
    renamed = notebook.update(question["id"], stored["version"], title="何时实际拨付")
    assert renamed["definition_version"] == 2
    assert renamed["findings"][0]["definition_current"] is False


def test_native_progress_finishes_as_ready_and_late_updates_preserve_human_resolution(tmp_path):
    notebook = ResearchNotebook(Store(tmp_path / "native"))
    question = notebook.create("申请是否执行", ["book-1"])
    started = notebook.attach_runs(question["id"], question["version"], ["run-1", "run-2"])
    assert started["status"] == "researching"
    notebook.sync_run({"run_id": "run-1", "state": "completed"})
    assert notebook.question(question["id"])["status"] == "researching"
    notebook.sync_run({"run_id": "run-2", "state": "pending"})
    ready = notebook.question(question["id"])
    assert ready["status"] == "ready"
    resolved = notebook.update(
        question["id"], ready["version"], status="resolved", notes="由用户完成判断"
    )
    notebook.sync_run({"run_id": "run-2", "state": "completed"})
    assert notebook.question(question["id"])["status"] == "resolved"
    assert notebook.question(question["id"])["notes"] == resolved["notes"]
    assert notebook.research_context(["book-2"])["questions"] == []


def test_count_only_progress_does_not_conflict_with_user_notes(tmp_path):
    notebook = ResearchNotebook(Store(tmp_path / "native"))
    question = notebook.create("何时拨付", ["book-1"])
    notebook.attach_runs(question["id"], question["version"], ["run-1"])
    notebook.sync_run({"run_id": "run-1", "state": "running", "version": 2})
    editing = notebook.question(question["id"])
    notebook.sync_run({"run_id": "run-1", "state": "running", "version": 3})
    saved = notebook.update(question["id"], editing["version"], notes="用户仍在编辑")
    assert saved["notes"] == "用户仍在编辑"


def test_reusing_an_existing_completed_request_does_not_claim_it_is_running(tmp_path):
    notebook = ResearchNotebook(Store(tmp_path / "native"))
    question = notebook.create("查证部署与执行", ["book-1"])
    notebook.attach_runs(question["id"], question["version"], ["run-1"])
    notebook.sync_run({"run_id": "run-1", "state": "completed", "version": 4})
    completed = notebook.question(question["id"])
    repeated = notebook.attach_runs(question["id"], completed["version"], ["run-1"])
    assert repeated["status"] == "ready"
    assert repeated["runs"][0]["state"] == "completed"


def test_question_progress_tracks_a_normal_pause_and_resume_without_reopening_user_resolution(
    tmp_path,
):
    notebook = ResearchNotebook(Store(tmp_path / "native"))
    question = notebook.create("查证执行过程", ["book-1"])
    notebook.attach_runs(question["id"], question["version"], ["run-1"])
    notebook.sync_run({"run_id": "run-1", "state": "paused", "version": 2})
    assert notebook.question(question["id"])["status"] == "ready"
    notebook.sync_run({"run_id": "run-1", "state": "ready", "version": 3})
    assert notebook.question(question["id"])["status"] == "researching"
    notebook.sync_run({"run_id": "run-1", "state": "completed", "version": 4})
    assert notebook.question(question["id"])["status"] == "ready"
