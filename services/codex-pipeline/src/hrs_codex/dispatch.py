"""Deterministic admission. Native Codex owns inference; this module owns capacity."""

import logging
import sqlite3
import time
from uuid import uuid4

from hrs_codex.errors import LeaseConflict
from hrs_codex.hashing import digest

ROLES = {
    "ocr": "historical_ocr",
    "review": "historical_review",
    "verify": "historical_review",
    "navigation": "historical_organization",
    "organization": "historical_organization",
}


class Dispatch:
    def __init__(self, store):
        self.store = store

    @property
    def ledger(self):
        ledger = self.store.checkpoints.ledger
        if ledger is None:
            raise ValueError("Migrate the stopped legacy workspace before dispatch")
        return ledger

    def sessions(self):
        return self.ledger.sessions()

    def reserve(self, document_id):
        """Reserve concrete task leases before asking the coordinator to spawn anything."""
        with self.store.lock:
            self.expire()
            plan = self.store.scheduling_plan(document_id)
            occupied = self.ledger.capacity_used()
            assignments = []
            if not plan["paused"] and plan["cooldown_until"] <= time.time():
                if (
                    plan["workers"]["ocr"] >= 2
                    and not plan["metrics"]["review"]["queued"]
                    and occupied < plan["capacity"]
                    and not any(
                        session["stage"] == "review"
                        and session["document_id"] == document_id
                        and session["state"] != "exited"
                        for session in self.sessions()
                    )
                ):
                    warm = {
                        "session_token": uuid4().hex,
                        "document_id": document_id,
                        "stage": "review",
                        "agent_type": ROLES["review"],
                        "state": "starting",
                        "reserved_at": time.time(),
                        "expires_at": time.time() + 180,
                        "completed": 0,
                        "task_id": None,
                        "prewarmed": True,
                    }
                    self.ledger.save_session(warm)
                    assignments.append(
                        {
                            key: warm[key]
                            for key in ("session_token", "document_id", "stage", "agent_type")
                        }
                    )
                    occupied += 1
        if not plan["paused"] and plan["cooldown_until"] <= time.time():
            for stage in ("verify", "review", "ocr", "navigation", "organization"):
                for _ in range(max(0, plan["workers"][stage] - plan["active"][stage])):
                    if occupied >= plan["capacity"]:
                        break
                    token = uuid4().hex
                    session = {
                        "session_token": token,
                        "document_id": document_id,
                        "stage": stage,
                        "agent_type": ROLES[stage],
                        "state": "starting",
                        "reserved_at": time.time(),
                        "expires_at": time.time() + 180,
                        "completed": 0,
                    }
                    task = self.store.tasks.claim_task(
                        document_id,
                        stage,
                        "reserved:" + token,
                        source_first=True,
                        scheduled=True,
                        session=session,
                    )
                    if task is None:
                        break
                    assignments.append(
                        {
                            key: session[key]
                            for key in ("session_token", "document_id", "stage", "agent_type")
                        }
                    )
                    occupied += 1
        return {
            "assignments": assignments,
            "plan": plan,
            "sequence": self.ledger.sequence(),
            "sessions": [
                {
                    key: session.get(key)
                    for key in ("session_token", "document_id", "stage", "state", "expires_at")
                }
                for session in self.sessions()
                if session["state"] != "exited"
            ],
        }

    def expire(self):
        now = time.time()
        for session in self.sessions():
            if session["state"] == "exited" or session["expires_at"] > now:
                continue
            self.end_session(session, "lease_expired")

    def end_session(self, session, reason):
        """Return an unstarted reservation and close its slot in the same transaction."""
        doc = None
        if session["state"] == "starting" and session.get("task_id"):
            doc = self.store.checkpoints.read(session["document_id"])
            task = next(task for task in doc["tasks"] if task["task_id"] == session["task_id"])
            if task["state"] == "leased" and task["claim"]["lease_token"] == session["lease_token"]:
                task["state"] = "ready"
        session.update(state="exited", exit_reason=reason, exited_at=time.time())
        if doc is not None:
            self.store.tasks.commit(doc, session)
        else:
            self.ledger.save_session(session)

    def session(self, token, worker_id=None, *, allow_closed=False):
        session = next(
            (session for session in self.sessions() if session["session_token"] == token), None
        )
        if session is None or (
            not allow_closed
            and (session["state"] == "exited" or session["expires_at"] <= time.time())
        ):
            raise ValueError("Session expired or exited; request a new assignment")
        if worker_id is not None and (
            not worker_id.strip() or session.get("worker_id", worker_id) != worker_id
        ):
            raise ValueError("Native worker identity differs from this session")
        return session

    def open(self, token, worker_id):
        with self.store.lock:
            session = self.session(token, worker_id)
            if session["state"] == "cooldown":
                raise ValueError(
                    "Session is cooling down after a technical failure; retain the task and exit"
                )
            if session["state"] == "starting" and session.get("task_id") is None:
                session.update(
                    state="idle",
                    worker_id=worker_id,
                    startup_seconds=time.time() - session["reserved_at"],
                    activated_at=time.time(),
                )
                self.ledger.save_session(session)
            if session["state"] == "idle":
                task = self.store.tasks.claim_task(
                    session["document_id"],
                    session["stage"],
                    worker_id,
                    source_first=True,
                    scheduled=True,
                    session=session,
                )
                if task is None:
                    doc = self.store.status(session["document_id"])
                    if (
                        session.get("prewarmed")
                        and not session["completed"]
                        and any(
                            task["stage"] == "ocr" and task["state"] in {"ready", "leased"}
                            for task in doc["tasks"]
                        )
                    ):
                        return (
                            {
                                "state": "wait",
                                "sequence": self.ledger.sequence(),
                                "instructions": "Call wait_for_changes(after_sequence=sequence), then open_task_packet again. This is normal prewarm waiting, not a failure.",
                            },
                            None,
                            None,
                        )
                    session.update(
                        state="exited", exited_at=time.time(), exit_reason="no_stage_work"
                    )
                    self.ledger.save_session(session)
                    return {"state": "exited", "sequence": self.ledger.sequence()}, None, None
            doc = self.store.checkpoints.read(session["document_id"])
            task = next(task for task in doc["tasks"] if task["task_id"] == session["task_id"])
            if task["state"] != "leased" or task["claim"]["lease_token"] != session["lease_token"]:
                raise ValueError("Session no longer owns this active task")
            if task["inputs"].get("prior_worker_id") == worker_id:
                raise ValueError("Review requires a separate native worker identity")
            if session["state"] == "starting":
                session.update(
                    startup_seconds=time.time() - session["reserved_at"], activated_at=time.time()
                )
                task["claim"].update(reserved_at=session["reserved_at"], started_at=time.time())
            task["claim"]["worker_id"] = worker_id
            session.update(
                worker_id=worker_id, state="busy", expires_at=task["claim"]["expires_at"]
            )
            self.store.tasks.commit(doc, session)
            task = {**task, **task["claim"], "processing_policy": doc.get("processing_policy", {})}
        metadata, pixels = (
            self.store.original_page(
                session["document_id"],
                task["page"],
                task_id=task["task_id"],
                lease_token=task["lease_token"],
            )
            if task["page"] is not None
            else (None, None)
        )
        return task, metadata, pixels

    def submit(self, token, worker_id, result):
        session = self.session(token, worker_id, allow_closed=True)
        submission_sha256 = digest(result)
        if receipt := session.get("submissions", {}).get(submission_sha256):
            return {
                **receipt,
                "already_committed": True,
                "session_state": session["state"],
                "sequence": self.ledger.sequence(),
            }
        if session["state"] != "busy" or session["expires_at"] <= time.time():
            raise ValueError("Session has no active task to submit")
        session["submission_sha256"] = submission_sha256
        started = time.perf_counter()
        try:
            receipt = self.store.tasks.submit_task(
                session["document_id"],
                session["task_id"],
                session["lease_token"],
                result,
                session=session,
            )
        except LeaseConflict:
            retained = self.session(token, worker_id, allow_closed=True)
            if saved := retained.get("submissions", {}).get(submission_sha256):
                return {
                    **saved,
                    "already_committed": True,
                    "session_state": retained["state"],
                    "sequence": self.ledger.sequence(),
                }
            raise
        try:
            self.ledger.record_submission_timing(token, time.perf_counter() - started)
        except sqlite3.Error:
            logging.getLogger(__name__).warning(
                "Committed result retained; submission telemetry unavailable"
            )
        return {
            **receipt,
            "session_state": session["state"],
            "completed": session["completed"],
            "sequence": self.ledger.sequence(),
        }

    def close(self, token, worker_id):
        with self.store.lock:
            session = self.session(token, worker_id, allow_closed=True)
            if session["state"] == "exited":
                return {"state": "exited"}
            if session["state"] == "busy":
                raise ValueError("Finish or report the active task before closing its session")
            self.end_session(session, "worker_finished")
            return {"state": "exited"}
