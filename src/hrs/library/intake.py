"""Own temporary S3 downloads and reclaim only abandoned native intake copies."""

import json
import logging
import math
import re
import shutil
import stat
import time
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

from filelock import FileLock, Timeout

from hrs.processing.snapshots import atomic_json

logger = logging.getLogger(__name__)
MAX_AGE = 24 * 60 * 60
NAME = re.compile(r"hrs-intake-([0-9a-f]{32})-[a-z0-9_]{8}\Z")


def _alias(path):
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _root(cache_root):
    root = Path(cache_root).resolve() / "intake"
    try:
        if _alias(root):
            raise ValueError("Native intake directory cannot be a filesystem alias")
    except FileNotFoundError:
        pass
    return root


def _owned_stale(directory, cutoff):
    match = NAME.fullmatch(directory.name)
    if not match or _alias(directory) or not directory.is_dir():
        return False
    marker = directory / "owner.json"
    if _alias(marker) or marker.stat().st_size > 4096:
        return False
    record = json.loads(marker.read_text("utf-8"))
    if not isinstance(record, dict):
        return False
    created = record.get("created_at")
    return (
        record.get("owner") == "hrs-native-intake"
        and record.get("version") == 1
        and record.get("run_id") == str(UUID(hex=match[1]))
        and type(created) in {int, float}
        and math.isfinite(created)
        and created <= cutoff
    )


def _contains_alias(directory):
    pending = [directory]
    while pending:
        for path in pending.pop().iterdir():
            if _alias(path):
                return True
            if path.is_dir():
                pending.append(path)
    return False


def cleanup_intake(cache_root, *, now=None):
    """Reclaim marked downloads older than 24h; active locks and unknown data survive."""
    root = _root(cache_root)
    if not root.is_dir():
        return 0
    cutoff = (time.time() if now is None else now) - MAX_AGE
    removed = 0
    for directory in root.iterdir():
        lock_path = root / f"{directory.name}.lock"
        try:
            if not _owned_stale(directory, cutoff) or _contains_alias(directory):
                continue
            try:
                if _alias(lock_path):
                    continue
            except FileNotFoundError:
                pass
            with FileLock(lock_path, timeout=0):
                if not _owned_stale(directory, cutoff) or _contains_alias(directory):
                    continue
                shutil.rmtree(directory)
                removed += 1
            lock_path.unlink(missing_ok=True)
        except Timeout:
            continue
        except FileNotFoundError:
            continue
        except (OSError, ValueError, TypeError):
            # Recovery of one stale copy must not block another book's import.
            logger.warning("Abandoned intake cleanup deferred: %s", directory.name)
    return removed


@contextmanager
def intake_directory(cache_root, run_id):
    """A download exists only while its owner holds the matching external lock."""
    root = _root(cache_root)
    root.mkdir(parents=True, exist_ok=True)
    cleanup_intake(cache_root)
    run_id = str(UUID(str(run_id)))
    temporary = TemporaryDirectory(prefix=f"hrs-intake-{UUID(run_id).hex}-", dir=root)
    directory = Path(temporary.name)
    lock_path = root / f"{directory.name}.lock"
    try:
        with FileLock(lock_path):
            try:
                atomic_json(
                    directory / "owner.json",
                    {
                        "owner": "hrs-native-intake",
                        "version": 1,
                        "created_at": time.time(),
                        "run_id": run_id,
                    },
                )
                yield directory
            finally:
                temporary.cleanup()
    finally:
        temporary.cleanup()
        lock_path.unlink(missing_ok=True)
