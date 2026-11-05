"""Backups retain durable state and originals, never credentials or live sockets."""

import json
from zipfile import ZipFile

import pytest
from PIL import Image

from hrs_codex.application import prepare
from hrs_codex.store import Store


def test_workspace_backup_restores_queued_work_and_source_to_new_root(tmp_path):
    from hrs_codex.backup import restore_workspace, snapshot_workspace

    source = tmp_path / "source.png"
    Image.new("RGB", (30, 40), "white").save(source)
    store = Store(tmp_path / "workspace")
    doc = prepare(store, str(source), "Retained source")
    store.request_run(doc["id"])
    (store.root / ".workbench.json").write_text(json.dumps({"token": "never archive"}))
    archive = tmp_path / "native.zip"
    manifest = snapshot_workspace(store, archive)
    restored = restore_workspace(archive, tmp_path / "restored")
    after = Store(restored).status(doc["id"])
    assert after["run_request"] == store.status(doc["id"])["run_request"]
    assert after["pages"] == doc["pages"]
    assert manifest["documents"] == [doc["id"]]
    assert not (restored / ".workbench.json").exists()
    Store(restored).verify_evidence(after)


def test_tampered_archive_is_rejected_before_creating_a_destination(tmp_path):
    from hrs_codex.backup import restore_workspace

    archive = tmp_path / "tampered.zip"
    with ZipFile(archive, "w") as stream:
        stream.writestr("documents/source", b"changed")
        stream.writestr(
            "backup-manifest.json",
            json.dumps(
                {
                    "schema_version": 1,
                    "kind": "quiesced-codex-workspace",
                    "files": {"documents/source": "0" * 64},
                }
            ),
        )
    destination = tmp_path / "restored"
    with pytest.raises(ValueError, match="hash mismatch"):
        restore_workspace(archive, destination)
    assert not destination.exists()


def test_evidence_failure_has_a_stable_category_and_keeps_value_error_compatibility(tmp_path):
    from hrs_codex.errors import EvidenceMismatch

    source = tmp_path / "source.png"
    Image.new("RGB", (30, 40), "white").save(source)
    store = Store(tmp_path / "workspace")
    doc = prepare(store, str(source))
    store.artifact_path(doc, doc["source"]["path"]).write_bytes(b"tampered")
    with pytest.raises(EvidenceMismatch) as failure:
        store.verify_evidence(doc)
    assert isinstance(failure.value, ValueError)
    assert failure.value.code == "evidence_mismatch"
