"""Explicit, quiesced JSON-to-ledger cutover with a restartable sealing journal."""

import json
import time
from contextlib import ExitStack

from filelock import FileLock

from hrs.processing.checkpoints import atomic_json, read_snapshot
from hrs.processing.hashing import digest, file_digest
from hrs.processing.ledger import Ledger


def migrate(repository, validate):
    with repository.lock, ExitStack() as locks:
        journal = repository.root / ".ledger-migration.json"
        receipt = json.loads(journal.read_text("utf-8")) if journal.exists() else None
        if receipt and receipt.get("completed_at"):
            return receipt
        paths = sorted((repository.root / "documents").glob("*/document.json"))
        if receipt is None:
            if repository.ledger is not None:
                return {"documents": repository.ids(), "already_current": True}
            documents, sources = [], {}
            for path in paths:
                locks.enter_context(FileLock(path.parent / ".executor.lock", timeout=0))
                raw = read_snapshot(path)
                document = json.loads(raw)
                request = document.get("run_request", {})
                if (
                    request.get("state") == "running"
                    and request.get("lease_expires_at", 0) > time.time()
                ) or any(
                    task["state"] == "leased"
                    and task.get("claim", {}).get("expires_at", 0) > time.time()
                    for task in document.get("tasks", [])
                ):
                    raise ValueError(
                        "Stop all executors and wait for active leases before migration"
                    )
                validate(document)
                documents.append(document)
                sources[path.relative_to(repository.root).as_posix()] = digest(raw)
            temporary = repository.root / ".ledger-migration.sqlite3"
            temporary.unlink(missing_ok=True)
            prepared = Ledger(temporary)
            for document in documents:
                prepared.write(document)
                if prepared.read(document["id"]) != document:
                    raise ValueError("Migrated document differs from the retained source")
            # sqlite backup includes WAL commits; publish one closed, self-contained database.
            ready = repository.root / ".ledger-ready.sqlite3"
            ready.unlink(missing_ok=True)
            prepared.backup(ready)
            receipt = {
                "schema_version": 1,
                "documents": [doc["id"] for doc in documents],
                "sources": sources,
                "prepared_at": time.time(),
                "database_sha256": file_digest(ready),
            }
            atomic_json(journal, receipt)
        ready = repository.root / ".ledger-ready.sqlite3"
        target = repository.root / "ledger.sqlite3"
        if not target.exists():
            if file_digest(ready) != receipt["database_sha256"]:
                raise ValueError("Prepared migration database hash mismatch")
            ready.replace(target)
        for relative, expected in receipt["sources"].items():
            path = repository.root / relative
            sealed = path.with_name("document.legacy.json")
            retained = path if path.exists() else sealed
            if file_digest(retained) != expected:
                raise ValueError(
                    "Legacy snapshot changed during migration; leave workspace stopped"
                )
            if path.exists():
                path.replace(sealed)
        receipt["completed_at"] = time.time()
        atomic_json(journal, receipt)
        repository.ledger = Ledger(target)
        return receipt
