"""The durable ledger commits changed records, not stale whole-book snapshots."""

from hrs_codex.checkpoints import Checkpoints


def test_independent_page_commits_merge_and_emit_durable_events(tmp_path):
    repository = Checkpoints(tmp_path)
    identity = "a" * 24
    repository.write({"id": identity, "pages": [{"page": 1}, {"page": 2}], "tasks": []})
    first, second = repository.read(identity), repository.read(identity)
    first["pages"][0]["state"] = "approved"
    second["pages"][1]["state"] = "pending"
    repository.write(first)
    repository.write(second)
    retained = Checkpoints(tmp_path)
    assert [page["state"] for page in retained.read(identity)["pages"]] == ["approved", "pending"]
    events = retained.events_after(0)
    assert [event["sequence"] for event in events] == [1, 2, 3]
    assert events[-1]["changes"] == [{"kind": "page", "key": "2", "version": 2}]
    assert (tmp_path / "ledger.sqlite3").is_file()
    assert not (repository.directory(identity) / "document.json").exists()


def test_legacy_workspace_requires_quiesced_explicit_migration(tmp_path):
    import json

    import pytest

    identity = "b" * 24
    legacy = tmp_path / "documents" / identity / "document.json"
    legacy.parent.mkdir(parents=True)
    document = {"id": identity, "pages": [], "tasks": [], "draft": {"text": "人工草稿"}}
    legacy.write_text(json.dumps(document), encoding="utf-8")
    original = legacy.read_bytes()
    repository = Checkpoints(tmp_path)
    assert repository.read(identity) == document
    with pytest.raises(ValueError, match="read-only"):
        repository.write(document)
    receipt = repository.migrate(lambda doc: None)
    assert receipt["documents"] == [identity]
    assert Checkpoints(tmp_path).read(identity) == document
    assert not legacy.exists()
    assert legacy.with_name("document.legacy.json").read_bytes() == original


def test_routine_claim_checks_its_page_but_full_audit_detects_other_page_damage(tmp_path):
    import pytest
    from test_store import document

    store, identity = document(tmp_path, count=2)
    snapshot = store.status(identity)
    store.artifact_path(snapshot, snapshot["pages"][1]["image"]).write_bytes(b"damaged second page")
    claim = store.claim_task(identity, "ocr", "first-page-worker")
    assert claim["page"] == 1
    with pytest.raises(ValueError, match="image hash mismatch"):
        store.verify_evidence(store.status(identity))


def test_migration_can_retain_a_crashed_run_after_its_ownership_lease_expires(tmp_path):
    import json

    identity = "c" * 24
    path = tmp_path / "documents" / identity / "document.json"
    path.parent.mkdir(parents=True)
    document = {
        "id": identity,
        "pages": [],
        "tasks": [],
        "run_request": {"state": "running", "lease_expires_at": 1},
    }
    path.write_text(json.dumps(document), encoding="utf-8")
    repository = Checkpoints(tmp_path)
    repository.migrate(lambda doc: None)
    assert repository.read(identity) == document
