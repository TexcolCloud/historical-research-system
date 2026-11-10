"""Research backup admission and retained SQLite evidence, with synthetic content."""

import pytest

from hrs.processing.backup import restore_workspace, snapshot_workspace
from hrs.processing.research_workflow import ResearchWorkflow
from hrs.processing.store import Store
from tests.unit.research.test_workflow import completed, snapshot


@pytest.mark.parametrize("owner", ["worker", "coordinator"])
def test_research_must_drain_before_native_snapshot(tmp_path, owner):
    store = Store(tmp_path / "native")
    workflow = ResearchWorkflow(store)
    run = workflow.enqueue(snapshot())
    if owner == "worker":
        workflow.claim(run["run_id"], "synthetic-worker", stage="author")
    else:
        assert store.runtime_control.reserve_coordinator(
            "gpt-6.1-sol", "research-coordinator:backup-test"
        )["allowed"]
    archive = tmp_path / "native.zip"
    with pytest.raises(ValueError, match="research executor"):
        snapshot_workspace(store, archive)
    assert not archive.exists()
    workflow.pause(run["run_id"])
    store.runtime_control.release("research-coordinator:backup-test")
    snapshot_workspace(store, archive)


def test_completed_card_and_frozen_sources_survive_native_backup(tmp_path):
    store = Store(tmp_path / "native")
    workflow = ResearchWorkflow(store)
    run = completed(workflow)
    card_id = run["card_ids"][0]
    before = workflow.card(card_id)
    archive = tmp_path / "native.zip"
    snapshot_workspace(store, archive)
    restored = ResearchWorkflow(Store(restore_workspace(archive, tmp_path / "restored")))
    assert restored.card(card_id) == before
    assert restored.snapshot(run["run_id"]) == workflow.snapshot(run["run_id"])
    assert restored.entity(restored.list_entities()[0]["id"])["mentions"]
