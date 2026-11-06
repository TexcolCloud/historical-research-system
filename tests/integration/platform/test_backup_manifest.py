"""Independent object bytes, SQL and native checkpoints restore without the source bucket."""

import hashlib
import json
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from hrs import backups
from hrs.config import Settings
from hrs.processing.application import prepare
from hrs.processing.store import Store
from hrs.processing.workspace import identity


class Objects:
    def __init__(self, bucket):
        self.bucket = bucket
        self.data = {}
        self.multipart = []
        self.client = self

    def check(self):
        pass

    def get_paginator(self, operation):
        def paginate(**options):
            if operation == "list_multipart_uploads":
                return [{"Uploads": self.multipart}]
            return [
                {
                    "Contents": [
                        {"Key": key, "Size": len(body), "ETag": hashlib.sha256(body).hexdigest()}
                        for key, (body, metadata, content_type) in sorted(self.data.items())
                        if key.startswith(options["Prefix"])
                    ]
                }
            ]

        return SimpleNamespace(paginate=paginate)

    def get_object(self, *, Bucket, Key):
        assert Bucket == self.bucket
        body, metadata, content_type = self.data[Key]
        return {
            "Body": BytesIO(body),
            "ContentLength": len(body),
            "Metadata": metadata,
            "ContentType": content_type,
        }

    def upload_file(self, filename, bucket, key, ExtraArgs):
        assert bucket == self.bucket
        self.data[key] = (
            Path(filename).read_bytes(),
            ExtraArgs["Metadata"],
            ExtraArgs["ContentType"],
        )


@pytest.fixture
def source_backup(tmp_path, monkeypatch):
    settings = Settings(
        database_url="postgresql+psycopg://unused:unused@127.0.0.1/source",
        s3_endpoint="http://127.0.0.1:1",
        s3_bucket="source",
        s3_access_key="unused",
        s3_secret_key="unused",
        project_root=tmp_path,
        cache_root=tmp_path / "cache",
        codex_root=tmp_path / "native",
    )
    source = tmp_path / "synthetic-source.png"
    Image.new("RGB", (30, 40), "white").save(source)
    store = Store(settings.codex_root)
    document = prepare(store, str(source))
    store.request_run(document["id"])
    objects = Objects("source")
    objects.data["hrs/v2/objects/retained"] = (
        b"source-bound text",
        {"sha256": "example"},
        "text/plain",
    )
    objects.data["hrs/v2/uploads/session/object.info"] = (
        b"retained upload metadata",
        {},
        "application/json",
    )
    monkeypatch.setattr(backups, "require_quiesced", lambda *_: None)
    monkeypatch.setattr(backups, "_native_runs", lambda _: [])
    monkeypatch.setattr(backups, "objects_for", lambda _: objects)
    monkeypatch.setattr(
        backups,
        "dump_database",
        lambda settings, name, path: path.write_bytes(b"synthetic SQL dump"),
    )
    restored = []
    monkeypatch.setattr(
        backups,
        "restore_database",
        lambda settings, name, path: restored.append((name, path.read_bytes())),
    )
    return settings, store, document, objects, restored


def test_complete_backup_restores_objects_after_source_bucket_is_gone(
    source_backup, tmp_path, monkeypatch
):
    settings, store, document, source_objects, restored = source_backup
    before = dict(source_objects.data)
    receipt = backups.backup(settings, destination=tmp_path / "backup")
    assert receipt["complete"] and receipt["objects"] == 2
    source_objects.data.clear()  # Original bucket is unavailable to the restore path.
    target = Objects("empty-target")
    monkeypatch.setattr(backups, "objects_for", lambda _: target)
    result = backups.restore(settings, receipt, "test", native_destination=tmp_path / "restored")
    assert target.data == before
    assert restored == [("hrs_restore_test_0", b"synthetic SQL dump")]
    after = Store(Path(result["native_workspace"]))
    assert after.status(document["id"])["pages"] == store.status(document["id"])["pages"]
    after.verify_evidence(after.status(document["id"]))
    assert result["object_store"]["verified"] is True


def test_restore_refuses_occupied_bucket_before_creating_database(
    source_backup, tmp_path, monkeypatch
):
    settings, _, _, _, restored = source_backup
    receipt = backups.backup(settings, destination=tmp_path / "backup")
    target = Objects("occupied")
    target.data["hrs/v2/existing"] = (b"must survive", {}, "text/plain")
    before = dict(target.data)
    monkeypatch.setattr(backups, "objects_for", lambda _: target)
    with pytest.raises(ValueError, match="empty target"):
        backups.restore(settings, receipt, "test", native_destination=tmp_path / "restored")
    assert not restored and target.data == before
    assert not (tmp_path / "restored").exists()


def test_restore_prevalidates_archive_hashes_before_target_changes(source_backup, tmp_path):
    settings, _, _, _, restored = source_backup
    receipt = backups.backup(settings, destination=tmp_path / "backup")
    archive = tmp_path / "backup/objects.zip"
    archive.write_bytes(archive.read_bytes() + b"altered")
    with pytest.raises(ValueError, match="hash or length"):
        backups.restore(settings, receipt, "test", native_destination=tmp_path / "restored")
    assert not restored
    assert not (tmp_path / "restored").exists()


def test_incomplete_uploads_never_receive_complete_backup_receipt(source_backup, tmp_path):
    settings, _, _, objects, _ = source_backup
    objects.multipart = [{"UploadId": "still-in-progress"}]
    with pytest.raises(ValueError, match="incomplete uploads"):
        backups.backup(settings, destination=tmp_path / "backup")
    assert not (tmp_path / "backup/manifest.json").exists()
    assert (tmp_path / "backup/.incomplete.json").is_file()


def test_interrupted_restore_retains_diagnostic_and_never_claims_completion(
    source_backup, tmp_path, monkeypatch
):
    settings, _, _, _, restored = source_backup
    receipt = backups.backup(settings, destination=tmp_path / "backup")
    target = Objects("empty-target")
    monkeypatch.setattr(backups, "objects_for", lambda _: target)

    def fail_upload(*args, **kwargs):
        raise OSError("unavailable target")

    monkeypatch.setattr(target, "upload_file", fail_upload)
    with pytest.raises(RuntimeError, match="Restore stopped during objects"):
        backups.restore(settings, receipt, "interrupted", native_destination=tmp_path / "restored")
    assert restored == [("hrs_restore_interrupted_0", b"synthetic SQL dump")]
    records = list(settings.cache_root.glob("restore-*.json"))
    assert len(records) == 1
    record = json.loads(records[0].read_text("utf-8"))
    assert record["state"] == "incomplete" and record["phase"] == "objects"
    assert record["database"] == "hrs_restore_interrupted_0"
    assert not (tmp_path / "restored").exists()


def test_historical_native_backup_receipt_remains_restorable(source_backup, tmp_path, monkeypatch):
    from hrs.processing.backup import snapshot_workspace

    settings, store, document, _, restored = source_backup
    archive = tmp_path / "old-native.zip"
    snapshot_workspace(store, archive)
    manifest = {
        "schema_version": 2,
        "kind": "quiesced-platform-databases",
        "databases": {"historical_database": {"key": "database"}},
        "native_workspace": {"key": "native"},
    }

    class HistoricalObjects:
        def read_bytes(self, reference):
            assert reference == {"key": "manifest"}
            return json.dumps(manifest).encode()

        def materialize(self, reference, destination):
            destination.write_bytes(
                archive.read_bytes() if reference["key"] == "native" else b"old SQL dump"
            )
            return destination

    monkeypatch.setattr(backups, "objects_for", lambda _: HistoricalObjects())
    result = backups.restore(
        settings, {"key": "manifest"}, "legacy", native_destination=tmp_path / "old-restored"
    )
    assert restored == [("hrs_restore_legacy_0", b"old SQL dump")]
    assert (
        Store(Path(result["native_workspace"])).status(document["id"])["source"]
        == store.status(document["id"])["source"]
    )


@pytest.fixture
def multiple_backup(source_backup, tmp_path):
    settings, store, document, _, _ = source_backup
    other = Store(tmp_path / "additional-native")
    original = store.document_directory(document["id"]) / document["source"]["path"]
    prepare(other, str(original))
    identity(other)
    receipt = backups.backup(settings, destination=tmp_path / "backup", native_roots=[other.root])
    return source_backup, other, receipt


def test_original_three_file_v3_manifest_still_restores(source_backup, tmp_path, monkeypatch):
    settings, _, document, _, _ = source_backup
    receipt = backups.backup(settings, destination=tmp_path / "backup")
    manifest = Path(receipt["manifest"])
    saved = json.loads(manifest.read_text("utf-8"))
    saved.pop("native_workspaces")
    manifest.write_text(json.dumps(saved), "utf-8")
    assert set(saved["files"]) == {"native-workspace.zip", "database.dump", "objects.zip"}
    monkeypatch.setattr(backups, "objects_for", lambda _: Objects("empty"))

    result = backups.restore(settings, receipt, "v3", native_destination=tmp_path / "restored")

    assert Path(result["native_workspace"]) == tmp_path / "restored"
    assert Store(tmp_path / "restored").status(document["id"])["id"] == document["id"]


@pytest.mark.parametrize(
    "corruption", ["archive", "identity", "unsafe_identity", "missing_archive"]
)
def test_every_native_archive_and_mapping_is_checked_before_restore_targets(
    multiple_backup, tmp_path, monkeypatch, corruption
):
    (settings, _, _, _, restored), _, receipt = multiple_backup
    manifest = Path(receipt["manifest"])
    saved = json.loads(manifest.read_text("utf-8"))
    additional = saved["native_workspaces"][1]
    if corruption == "archive":
        archive = manifest.parent / additional["archive"]
        archive.write_bytes(archive.read_bytes() + b"changed")
    elif corruption == "identity":
        additional["workspace_id"] = "different-retained-identity"
    elif corruption == "unsafe_identity":
        additional["workspace_id"] = "../outside"
    else:
        saved["files"].pop(additional["archive"])
    manifest.write_text(json.dumps(saved), "utf-8")
    target = Objects("empty")
    monkeypatch.setattr(backups, "objects_for", lambda _: target)

    with pytest.raises(ValueError):
        backups.restore(settings, receipt, "corrupt", native_destination=tmp_path / "restored")

    assert not restored and not target.data
    assert not (tmp_path / "restored").exists()
    assert not list(settings.cache_root.glob("restore-*.json"))


def test_partial_multi_workspace_restore_keeps_completed_workspace_and_phase_journal(
    multiple_backup, tmp_path, monkeypatch
):
    (settings, store, document, _, _), other, receipt = multiple_backup
    monkeypatch.setattr(backups, "objects_for", lambda _: Objects("empty"))
    original_restore = backups.restore_workspace
    calls = []

    def fail_second_workspace(source, destination):
        calls.append(Path(destination))
        if len(calls) == 2:
            raise OSError("Synthetic second-target write failure")
        return original_restore(source, destination)

    monkeypatch.setattr(backups, "restore_workspace", fail_second_workspace)
    destination = tmp_path / "restored"
    with pytest.raises(RuntimeError, match="Restore stopped during native_workspace"):
        backups.restore(settings, receipt, "partial", native_destination=destination)

    primary_id, other_id = identity(store)["workspace_id"], identity(other)["workspace_id"]
    after = Store(destination / primary_id)
    assert after.status(document["id"])["pages"] == store.status(document["id"])["pages"]
    after.verify_evidence(after.status(document["id"]))
    records = list(settings.cache_root.glob("restore-*.json"))
    assert len(records) == 1
    progress = json.loads(records[0].read_text("utf-8"))
    assert progress["state"] == "incomplete" and progress["phase"] == "native_workspace"
    assert progress["workspace_id"] == other_id
    assert progress["restored_native_workspaces"] == [primary_id]
    assert progress["native_workspaces"] == {
        primary_id: str(destination / primary_id),
        other_id: str(destination / other_id),
    }


def test_backup_cli_passes_each_explicit_additional_root(source_backup, monkeypatch, capsys):
    import sys

    from hrs.api.cli import main

    settings, _, _, _, _ = source_backup
    calls = []
    monkeypatch.setattr(Settings, "load", lambda: settings)
    monkeypatch.setattr(backups, "backup", lambda selected, **options: calls.append(options) or {})
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "hrs",
            "backup",
            "--destination",
            "backup",
            "--additional-native-root",
            "one",
            "--additional-native-root",
            "two",
        ],
    )

    main()

    assert calls == [{"destination": "backup", "native_roots": ["one", "two"]}]
    assert json.loads(capsys.readouterr().out) == {}


def test_new_sql_dependency_during_snapshot_never_commits_a_complete_manifest(
    source_backup, tmp_path, monkeypatch
):
    settings, _, document, _, _ = source_backup
    calls = []

    def sql_dependencies(_):
        calls.append(True)
        return (
            []
            if len(calls) == 1
            else [
                {
                    "id": "late-run",
                    "source": document["source"],
                    "result": {
                        "codex_workspace_id": "not-snapshotted",
                        "codex_document_id": document["id"],
                    },
                }
            ]
        )

    monkeypatch.setattr(backups, "_native_runs", sql_dependencies)
    with pytest.raises(ValueError, match="not-snapshotted"):
        backups.backup(settings, destination=tmp_path / "backup")

    assert not (tmp_path / "backup/manifest.json").exists()
    assert (
        json.loads((tmp_path / "backup/.incomplete.json").read_text("utf-8"))["state"]
        == "incomplete"
    )


def test_old_incomplete_v3_sql_mapping_fails_restore_coverage_with_retained_targets(
    source_backup, tmp_path, monkeypatch
):
    settings, _, document, _, _ = source_backup
    receipt = backups.backup(settings, destination=tmp_path / "backup")
    manifest_path = Path(receipt["manifest"])
    manifest = json.loads(manifest_path.read_text("utf-8"))
    manifest.pop("native_workspaces")
    manifest_path.write_text(json.dumps(manifest), "utf-8")
    monkeypatch.setattr(backups, "objects_for", lambda _: Objects("empty"))
    monkeypatch.setattr(
        backups,
        "_native_runs",
        lambda _: [
            {
                "id": "historical-missing-run",
                "source": document["source"],
                "result": {
                    "codex_workspace_id": "not-retained-in-old-v3",
                    "codex_document_id": document["id"],
                },
            }
        ],
    )
    destination = tmp_path / "restored"

    with pytest.raises(RuntimeError, match="Restore stopped during native_coverage"):
        backups.restore(settings, receipt, "oldmissing", native_destination=destination)

    assert destination.is_dir()
    records = list(settings.cache_root.glob("restore-*.json"))
    assert len(records) == 1
    progress = json.loads(records[0].read_text("utf-8"))
    assert progress["phase"] == "native_coverage" and progress["state"] == "incomplete"
    assert progress["restored_native_workspaces"] == [manifest["workspace_id"]]


@pytest.mark.parametrize("target_kind", ["directory", "broken_symlink"])
def test_restore_refuses_existing_native_target_before_database_creation(
    source_backup, tmp_path, monkeypatch, target_kind
):
    settings, _, _, _, restored = source_backup
    receipt = backups.backup(settings, destination=tmp_path / "backup")
    monkeypatch.setattr(backups, "objects_for", lambda _: Objects("empty"))
    destination = tmp_path / "occupied-native"
    if target_kind == "directory":
        destination.mkdir()
        (destination / "retained").write_bytes(b"Existing data")
    else:
        try:
            destination.symlink_to(tmp_path / "missing-link-target", target_is_directory=True)
        except OSError:
            pytest.skip("Creating directory symlinks is unavailable")

    with pytest.raises(ValueError, match="absent native restore"):
        backups.restore(settings, receipt, "occupied", native_destination=destination)

    assert not restored
    assert not list(settings.cache_root.glob("restore-*.json"))
    if target_kind == "directory":
        assert (destination / "retained").read_bytes() == b"Existing data"
    else:
        assert destination.is_symlink() and not (tmp_path / "missing-link-target").exists()
