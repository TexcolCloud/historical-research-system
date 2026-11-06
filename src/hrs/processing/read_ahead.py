"""Independent source observations overlap OCR, without exposing a candidate."""

import time
from uuid import uuid4

from hrs.processing.hashing import digest
from hrs.processing.task_inspection import validate_original_reading


class ReadAhead:
    def __init__(self, store):
        self.store = store

    def begin(self, session, worker_id):
        doc = self.store.checkpoints.read(session["document_id"])
        reserved = {
            s.get("preflight_page")
            for s in self.store.dispatch.sessions()
            if s["document_id"] == doc["id"]
            and s["state"] != "exited"
            and s["expires_at"] > time.time()
        }
        page = next(
            (
                p["page"]
                for p in doc["pages"]
                if p["state"] == "awaiting_ocr" and p["page"] not in reserved
            ),
            None,
        )
        if page is None:
            return None
        metadata, pixels = self.store.original_page(doc["id"], page)
        access = {
            **metadata,
            "receipt": uuid4().hex,
            "worker_id": worker_id,
            "opened_at": time.time(),
        }
        session.update(
            preflight_page=page,
            preflight={"access": access},
            task_id=None,
            state="idle",
            expires_at=time.time() + 900,
        )
        session.pop("lease_token", None)
        self.store.checkpoints.ledger.save_session(session)
        return (
            {"state": "preflight", "stage": "review", "page": page, "document_id": doc["id"]},
            {**metadata, "image_receipt": access["receipt"]},
            pixels,
        )

    def freeze(self, session, worker_id, reading):
        preflight = session["preflight"]
        if preflight.get("reading") is not None:
            if preflight["reading"] != reading:
                raise ValueError("Original reading cannot change after freezing")
            return
        doc = self.store.checkpoints.read(session["document_id"])
        validate_original_reading(
            self.store.tasks.context,
            doc,
            {"page": session["preflight_page"]},
            preflight["access"],
            reading,
        )
        if preflight["access"]["worker_id"] != worker_id:
            raise ValueError("Independent original reader identity differs")
        preflight.update(reading=reading, frozen_at=time.time())
        preflight["sha256"] = digest(preflight)
        self.store.checkpoints.ledger.save_session(session)

    def bind(self, session, task):
        preflight = session["preflight"]
        if digest({k: v for k, v in preflight.items() if k != "sha256"}) != preflight["sha256"]:
            raise ValueError("Preflight source observation hash mismatch")
        doc = self.store.checkpoints.read(session["document_id"])
        retained = next(t for t in doc["tasks"] if t["task_id"] == task["task_id"])
        access = preflight["access"]
        if (
            task["page"] != session["preflight_page"]
            or access["source_sha256"] != doc["source"]["sha256"]
            or access["image_sha256"] != doc["pages"][task["page"] - 1]["image_sha256"]
            or access["worker_id"] != task["claim"]["worker_id"]
        ):
            raise ValueError("Preflight original does not match the claimed page")
        retained["image_access"] = {**access, "task_id": task["task_id"]}
        self.store.tasks.commit(doc, session)
        # The complete frozen source-only receipt travels with the final decision.
        return self.store.review_candidate(
            doc["id"],
            task["task_id"],
            task["claim"]["lease_token"],
            {**preflight["reading"], "preflight": preflight},
        )
