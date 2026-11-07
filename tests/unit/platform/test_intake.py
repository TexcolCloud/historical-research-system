import json
import os
import subprocess
import sys
from uuid import uuid4

import pytest
from filelock import FileLock

from hrs.library.intake import MAX_AGE, cleanup_intake, intake_directory


def abandoned(cache, *, created_at=0):
    run_id = uuid4()
    directory = cache / "intake" / f"hrs-intake-{run_id.hex}-abcdefgh"
    directory.mkdir(parents=True)
    (directory / "owner.json").write_text(
        json.dumps(
            {
                "owner": "hrs-native-intake",
                "version": 1,
                "run_id": str(run_id),
                "created_at": created_at,
            }
        ),
        encoding="utf-8",
    )
    (directory / "original.pdf").write_bytes(b"synthetic abandoned copy")
    return directory


@pytest.mark.parametrize("fail", [False, True])
def test_owned_download_is_removed_after_success_or_failure(tmp_path, fail):
    directory = None
    try:
        with intake_directory(tmp_path, uuid4()) as directory:
            (directory / "original.pdf").write_bytes(b"synthetic download")
            assert cleanup_intake(tmp_path, now=10**12) == 0
            if fail:
                raise RuntimeError("synthetic import failure")
    except RuntimeError:
        assert fail
    assert not directory.exists()
    assert list((tmp_path / "intake").iterdir()) == []


def test_cleanup_reclaims_expired_copy_but_keeps_active_recent_and_unknown_data(tmp_path):
    stale = abandoned(tmp_path)
    active = abandoned(tmp_path)
    recent = abandoned(tmp_path, created_at=MAX_AGE)
    unknown = tmp_path / "intake" / "unknown-import"
    unknown.mkdir()
    (unknown / "original.pdf").write_bytes(b"not owned")
    lock_path = active.parent / f"{active.name}.lock"
    with FileLock(lock_path):
        assert cleanup_intake(tmp_path, now=MAX_AGE + 1) == 1
    assert not stale.exists()
    assert active.exists() and recent.exists() and unknown.exists()
    assert cleanup_intake(tmp_path, now=MAX_AGE + 1) == 1
    assert not active.exists() and recent.exists() and unknown.exists()


def test_cleanup_reclaims_a_hard_exited_import_after_its_retention_window(tmp_path):
    program = """
import os, sys
from pathlib import Path
from uuid import uuid4
from hrs.library.intake import intake_directory
with intake_directory(Path(sys.argv[1]), uuid4()) as directory:
    (directory / 'original.pdf').write_bytes(b'synthetic interrupted download')
    os._exit(7)
"""
    process = subprocess.run([sys.executable, "-c", program, str(tmp_path)], check=False)
    assert process.returncode == 7
    directory = next(path for path in (tmp_path / "intake").iterdir() if path.is_dir())
    created = json.loads((directory / "owner.json").read_text("utf-8"))["created_at"]
    assert cleanup_intake(tmp_path, now=created + MAX_AGE - 1) == 0
    assert cleanup_intake(tmp_path, now=created + MAX_AGE + 1) == 1
    assert list((tmp_path / "intake").iterdir()) == []


@pytest.mark.parametrize("marker", [[], {"owner": "someone-else"}, {"created_at": float("nan")}])
def test_cleanup_does_not_claim_invalid_or_foreign_marker(tmp_path, marker):
    directory = abandoned(tmp_path)
    path = directory / "owner.json"
    if isinstance(marker, dict):
        marker = {**json.loads(path.read_text("utf-8")), **marker}
    path.write_text(json.dumps(marker), encoding="utf-8")
    assert cleanup_intake(tmp_path, now=MAX_AGE + 1) == 0
    assert directory.exists()


def test_cleanup_rejects_filesystem_aliases(tmp_path):
    cache, outside = tmp_path / "cache", tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "original.pdf"
    sentinel.write_bytes(b"must survive")
    directory = abandoned(cache)
    link = directory / "linked-evidence"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except (OSError, NotImplementedError) as error:
        pytest.skip(f"Directory symlinks unavailable: {error}")
    assert cleanup_intake(cache, now=MAX_AGE + 1) == 0
    assert sentinel.read_bytes() == b"must survive" and directory.exists()
    link.unlink()
    namespace = tmp_path / "aliased-cache"
    namespace.mkdir()
    os.symlink(outside, namespace / "intake", target_is_directory=True)
    with pytest.raises(ValueError, match="alias"):
        cleanup_intake(namespace)
    with pytest.raises(ValueError, match="alias"):
        with intake_directory(namespace, uuid4()):
            pytest.fail("Aliased intake must never be entered")
    assert sentinel.exists()


def test_cleanup_never_follows_a_dangling_lock_alias(tmp_path, monkeypatch):
    from hrs.library import intake

    directory = abandoned(tmp_path)
    external = tmp_path / "outside.lock"
    lock_path = directory.parent / f"{directory.name}.lock"
    try:
        os.symlink(external, lock_path)
    except (OSError, NotImplementedError):
        alias = intake._alias
        monkeypatch.setattr(intake, "_alias", lambda path: path == lock_path or alias(path))

    def refuse_alias(*args, **kwargs):
        pytest.fail("A dangling lock alias must be rejected before FileLock opens it")

    monkeypatch.setattr(intake, "FileLock", refuse_alias)
    assert cleanup_intake(tmp_path, now=MAX_AGE + 1) == 0
    assert not external.exists() and directory.exists()
