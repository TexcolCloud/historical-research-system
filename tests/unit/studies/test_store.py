"""Study records are personal revisions, never source approval records."""

import json

import pytest

from hrs.library.errors import ServiceError
from hrs.processing.store import Store
from hrs.studies.store import StudyStore


def test_versions_conflicts_and_history_are_atomic(tmp_path):
    store = StudyStore(Store(tmp_path))
    topic = store.create("topic", {"title": "专题", "archived": False})
    changed = store.save("topic", topic["id"], {"title": "修改"}, expected_version=1)
    assert changed["version"] == 2
    assert store.save("topic", topic["id"], {"title": "修改"}, expected_version=1) == changed
    with pytest.raises(ServiceError) as conflict:
        store.save("topic", topic["id"], {"title": "不同修改"}, expected_version=1)
    assert conflict.value.status_code == 409
    assert store.read("topic", topic["id"]) == changed
    assert [row["version"] for row in store.history("topic", topic["id"])] == [2, 1]
    assert store.list("topic")["total"] == 1


def test_list_and_history_summaries_omit_canvas_documents(tmp_path):
    store = StudyStore(Store(tmp_path))
    topic = store.create("topic", {"title": "专题", "materials": [{"id": "pin"}]})
    canvas = store.create(
        "canvas",
        {"title": "画布", "document": {"nodes": [{"id": "node"}], "edges": []}},
        topic_id=topic["id"],
    )
    result = store.list("canvas", topic_id=topic["id"], summary=True)["items"][0]
    assert result["node_count"] == 1 and result["edge_count"] == 0 and "document" not in result
    assert "document" not in store.history("canvas", canvas["id"], summary=True)[0]
    summary = store.list("topic", summary=True)["items"][0]
    assert summary["material_count"] == 1 and "materials" not in summary


def test_legacy_workspace_rejects_studies_without_implicit_migration(tmp_path):
    legacy = tmp_path / "documents" / ("a" * 24) / "document.json"
    legacy.parent.mkdir(parents=True)
    retained = json.dumps(
        {"id": "a" * 24, "pages": [], "tasks": [], "draft": "个人已修改草稿"}
    ).encode()
    legacy.write_bytes(retained)
    native = Store(tmp_path)
    assert native.checkpoints.ledger is None
    with pytest.raises(ServiceError) as conflict:
        StudyStore(native)
    assert conflict.value.status_code == 409
    assert "migrate-ledger" in conflict.value.detail
    assert legacy.read_bytes() == retained
    assert not (tmp_path / "ledger.sqlite3").exists()
    assert not (tmp_path / ".workspace.json").exists()
