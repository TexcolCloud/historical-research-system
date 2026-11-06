"""Atomic document snapshots and verified compare-before-commit transactions."""

import json
from contextlib import contextmanager
from pathlib import Path

from filelock import FileLock

from hrs.processing.hashing import digest
from hrs.processing.ledger import Ledger
from hrs.processing.migration import migrate
from hrs.processing.snapshots import read_snapshot


class Checkpoints:
    def __init__(self, root: Path):
        self.created = False
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        # One process lock serializes local document mutations; split by
        # document only if measured local queue contention warrants it.
        self.lock = FileLock(self.root / ".store.lock", timeout=30)
        path = self.root / "ledger.sqlite3"
        if path.exists():
            # Opening an established WAL reader must not wait behind a model command.
            self.ledger = Ledger(path)
            return
        with self.lock:
            legacy = any((self.root / "documents").glob("*/document.json"))
            self.created = not path.exists() and not legacy
            self.ledger = Ledger(path) if path.exists() or not legacy else None

    def directory(self, document_id: str) -> Path:
        if len(document_id) != 24 or any(c not in "0123456789abcdef" for c in document_id):
            raise ValueError("Invalid document ID")
        return self.root / "documents" / document_id

    def read(self, document_id: str) -> dict:
        self.directory(document_id)
        if self.ledger is not None:
            return self.ledger.read(document_id)
        return json.loads(read_snapshot(self.directory(document_id) / "document.json"))

    def write(self, doc: dict) -> None:
        self.directory(doc["id"])
        self.require_writable()
        self.ledger.write(doc)

    def require_writable(self):
        if self.ledger is None:
            raise ValueError(
                "Legacy workspace is read-only; stop executors and migrate-ledger first"
            )
        journal = self.root / ".ledger-migration.json"
        if journal.exists() and not json.loads(journal.read_text("utf-8")).get("completed_at"):
            raise ValueError(
                "Migration cutover incomplete; keep executors stopped and rerun migrate-ledger"
            )

    def migrate(self, validate):
        return migrate(self, validate)

    def ids(self):
        return (
            self.ledger.ids()
            if self.ledger
            else sorted(
                path.parent.name for path in (self.root / "documents").glob("*/document.json")
            )
        )

    def exists(self, document_id):
        return document_id in self.ids()

    def revision(self, document_id):
        if self.ledger:
            return self.ledger.revision(document_id)
        return digest(read_snapshot(self.directory(document_id) / "document.json"))

    def events_after(self, sequence, limit=1000):
        if self.ledger is None:
            return []
        return self.ledger.events_after(sequence, limit)

    @contextmanager
    def validated(self, document_id, validate, select=None):
        """Validate an immutable snapshot without blocking readers or lease heartbeats."""
        for _ in range(8):
            revision = self.revision(document_id)
            doc = self.read(document_id)
            selected = select(doc) if select else None
            validate(doc)
            with self.lock:
                if self.revision(document_id) != revision:
                    current = self.read(document_id)
                    if not select or select(current) != selected:
                        continue
                    doc = current
                yield doc
                return
        raise ValueError(
            "Document changed during evidence validation; retry with the current snapshot"
        )

    def evidence_path(self, doc: dict, relative: str) -> Path:
        directory = self.directory(doc["id"]).resolve()
        path = (directory / relative).resolve()
        if not path.is_relative_to(directory):
            raise ValueError("Evidence path escapes document directory")
        return path
