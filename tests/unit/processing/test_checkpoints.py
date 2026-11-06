"""A checked write must use the latest snapshot, without weakening path constraints."""

from pathlib import Path

import pytest


def test_checkpoint_validation_restarts_after_a_concurrent_commit(tmp_path):
    from hrs.processing.checkpoints import Checkpoints

    repository = Checkpoints(tmp_path)
    identity = "a" * 24
    repository.write({"id": identity, "title": "before"})
    seen = []

    def validate(snapshot):
        seen.append(snapshot["title"])
        if len(seen) == 1:
            other = Checkpoints(tmp_path)
            with other.lock:
                other.write({"id": identity, "title": "concurrent"})

    with repository.validated(identity, validate) as snapshot:
        assert snapshot["title"] == "concurrent"
        repository.write({**snapshot, "title": "final"})
    assert seen == ["before", "concurrent"]
    assert repository.read(identity)["title"] == "final"
    with pytest.raises(ValueError, match="escapes"):
        repository.evidence_path({"id": identity}, "../../outside")


def test_a_transient_snapshot_sharing_conflict_does_not_break_status(tmp_path, monkeypatch):
    import json

    from hrs.processing.snapshots import atomic_json, read_snapshot

    document = {"id": "b" * 24, "title": "committed"}
    # JSON remains an import/export contract, not the live task database.
    path = tmp_path / "document.json"
    atomic_json(path, document)
    original = Path.open
    conflicts = []

    def open_snapshot(self, *args, **kwargs):
        if self == path and len(conflicts) < 2:
            conflicts.append(self)
            raise PermissionError(13, "Synthetic Windows sharing conflict", str(self))
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_snapshot)
    assert json.loads(read_snapshot(path)) == document
    assert len(conflicts) == 2
