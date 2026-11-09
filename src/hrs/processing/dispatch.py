"""Deterministic admission. Native Codex owns inference; this module owns capacity."""

import logging
import sqlite3
import time
from copy import deepcopy
from uuid import uuid4

from hrs.processing.errors import LeaseConflict, WorkflowStopped
from hrs.processing.hashing import digest

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

    def reserve(self, document_id, *, active_session_tokens=()):
        created = []
        try:
            return self._reserve(document_id, active_session_tokens, created)
        except BaseException:
            # A batch can commit several claims before a later validation fails.
            # None of those undisclosed reservations may retain native capacity.
            with self.store.lock:
                committed = {session["session_token"]: session for session in self.sessions()}
                for session in created:
                    current = committed.get(session["session_token"], session)
                    if current["state"] in {"starting", "idle"}:
                        self.end_session(current, "dispatch_reservation_failed")
            raise

    def _reserve(self, document_id, active_session_tokens, created):
        """Lease concrete work before allocating shared capacity to speculative warmup."""
        from hrs.processing.runtime_control import effective_policy
        from hrs.processing.scheduling import ready_tasks

        policy = effective_policy(self.store.root)
        with self.store.lock:
            tracked = []
            for token in active_session_tokens:
                session = self.session(token, allow_closed=True)
                if session["document_id"] != document_id:
                    raise ValueError("Tracked native sessions must belong to this document")
                tracked.append(session)
            self.heartbeat(document_id, tracked)
            self.expire()
            plan = self.store.scheduling_plan(document_id)
            concrete = ready_tasks(self.store.checkpoints.read(document_id))
            live = [
                s
                for s in self.sessions()
                if s["document_id"] == document_id and s["state"] != "exited"
            ]
            legacy_warm = (
                not policy["overlap_reading"]
                and plan["workers"]["ocr"] >= 2
                and not plan["metrics"]["review"]["queued"]
                and self.ledger.capacity_used() < plan["capacity"] - 1
                and not any(s["stage"] == "review" for s in live)
            )
        assignments = []
        if not plan["paused"] and plan["cooldown_until"] <= time.time():
            for stage in ("verify", "review", "ocr", "navigation", "organization"):
                if not any(task["stage"] == stage for task in concrete):
                    continue
                with self.store.lock:
                    self.make_room(stage, plan)
                    live = [
                        s
                        for s in self.sessions()
                        if s["document_id"] == document_id and s["state"] != "exited"
                    ]
                    if (
                        policy["overlap_reading"]
                        and stage in {"review", "organization"}
                        and any(s["stage"] == stage for s in live)
                    ):
                        continue
                missing = plan["workers"][stage] - plan["active"][stage]
                if stage == "ocr" and legacy_warm:
                    missing -= 1
                for _ in range(max(0, missing)):
                    with self.store.lock:
                        self.make_room(stage, plan)
                        if self.ledger.capacity_used() >= plan["capacity"]:
                            break
                        session = self.new_session(document_id, stage, plan)
                        if session is None:
                            break
                        created.append(session)
                    token = session["session_token"]
                    # Evidence hashing remains outside the command mutex, so pause stays responsive.
                    try:
                        task = self.store.tasks.claim_task(
                            document_id,
                            stage,
                            "reserved:" + token,
                            source_first=True,
                            scheduled=True,
                            session=session,
                        )
                    except BaseException:
                        self.store.runtime_control.release(token)
                        raise
                    if task is None:
                        self.store.runtime_control.release(token)
                        break
                    assignments.append(self.assignment(session))
            with self.store.lock:
                live = [
                    s
                    for s in self.sessions()
                    if s["document_id"] == document_id and s["state"] != "exited"
                ]
                # Warmup uses only the shared slots remaining after executable work.
                warm_stages = (
                    ("review", "organization")
                    if policy["overlap_reading"]
                    else (("review",) if legacy_warm else ())
                )
                for stage in warm_stages:
                    target = plan["workers"][stage] if policy["overlap_reading"] else 1
                    missing = target - sum(s["stage"] == stage for s in live)
                    for _ in range(max(0, missing)):
                        if self.ledger.capacity_used() >= plan["capacity"]:
                            break
                        warm = self.new_session(document_id, stage, plan, prewarm=True)
                        if warm is None:
                            break
                        created.append(warm)
                        warm.update(
                            task_id=None, prewarmed=True, overlap_reading=policy["overlap_reading"]
                        )
                        self.ledger.save_session(warm)
                        live.append(warm)
                        assignments.append(self.assignment(warm))
        return {
            "assignments": assignments,
            "retired_sessions": [
                token
                for token in active_session_tokens
                if self.session(token, allow_closed=True)["state"] == "exited"
            ],
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

    def heartbeat(self, document_id, sessions):
        """Renew declared busy claims under the still-live coordinator, before expiring slots."""
        now = time.time()
        with self.ledger.connect(write=True):
            doc = self.store.checkpoints.read(document_id)
            request = doc.get("run_request", {})
            if (
                request.get("state") != "running"
                or not request.get("token")
                or request.get("lease_expires_at", 0) <= now
                or request.get("pause_requested")
                or request.get("cancel_requested")
            ):
                return
            for session in sessions:
                if (
                    session["state"] != "busy"
                    or session["expires_at"] <= now
                    or (
                        session.get("coordinator_token")
                        and session["coordinator_token"] != request.get("token")
                    )
                ):
                    continue
                task = next(
                    (t for t in doc["tasks"] if t["task_id"] == session.get("task_id")), None
                )
                claim = (task or {}).get("claim", {})
                if (
                    not task
                    or task["state"] != "leased"
                    or claim.get("expires_at", 0) <= now
                    or task["stage"] != session["stage"]
                    or claim.get("lease_token") != session.get("lease_token")
                    or claim.get("worker_id") != session.get("worker_id")
                    or digest(task.get("inputs")) != claim.get("input_sha256")
                ):
                    continue
                duration = session.get("lease_seconds") or (
                    claim["expires_at"] - claim.get("reserved_at", claim["started_at"])
                )
                if not 0 < duration <= 86400:
                    continue
                deadline = min(now + duration, request["lease_expires_at"])
                model = claim.get("actual_model") or session.get("actual_model")
                if not model:
                    continue
                if not self.store.runtime_control.renew(
                    model, session["session_token"], expires_at=deadline, now=now
                ):
                    continue
                if deadline > claim["expires_at"]:
                    claim["expires_at"] = deadline
                    session.update(expires_at=deadline, lease_seconds=duration)
                    self.store.tasks.commit(doc, session)

    def make_room(self, stage, plan):
        """Waiting warm agents yield globally; active original reads and task leases do not."""
        from hrs.processing.contracts import model_for_stage
        from hrs.processing.scheduling import ready_tasks

        model = model_for_stage(stage)
        gate = self.store.runtime_control.gate(model)
        if not gate["allowed"]:
            return
        capacity = min(plan["capacity"], self.store.runtime_control.snapshot()["account"]["limit"])
        while True:
            occupied = self.ledger.capacity_used()
            model_occupied = self.ledger.model_capacity_used(model)
            full = occupied >= capacity
            model_full = model_occupied >= gate["limit"]
            overcommitted = occupied > capacity or model_occupied > gate["limit"]
            if not full and not model_full:
                return
            waiting = []
            for session in self.sessions():
                if (
                    session["state"] != "idle"
                    or not session.get("prewarmed")
                    or self.inflight(session)
                ):
                    continue
                if model_full and not full and model_for_stage(session["stage"]) != model:
                    continue
                doc = self.store.checkpoints.read(session["document_id"])
                if not overcommitted and any(
                    task["stage"] == session["stage"]
                    and (
                        not session.get("preflight_page")
                        or task["page"] == session["preflight_page"]
                    )
                    for task in ready_tasks(doc)
                ):
                    continue
                waiting.append(session)
            if not waiting:
                return
            # Keep expensive frozen observations when a plain waiting agent can yield first.
            retained = min(waiting, key=lambda s: bool(s.get("preflight")))
            self.end_session(retained, "capacity_for_ready_work")

    def new_session(self, document_id, stage, plan, *, prewarm=False):
        from hrs.processing.contracts import model_for_stage

        model = model_for_stage(stage)
        if prewarm and any(
            scope["state"] != "normal"
            for scope in self.store.runtime_control.snapshot()["scopes"]
            if scope["scope"] == "account" or scope.get("model") == model
        ):
            return None
        session = {
            "session_token": uuid4().hex,
            "document_id": document_id,
            "stage": stage,
            "agent_type": ROLES[stage],
            "state": "starting",
            "reserved_at": time.time(),
            "expires_at": time.time() + max(180, plan["capacity"] * 10),
            "completed": 0,
            "max_tasks": plan["batch_size"],
            "actual_model": model,
        }
        request = self.store.checkpoints.read(document_id).get("run_request", {})
        if (
            request.get("state") == "running"
            and request.get("token")
            and request.get("lease_expires_at", 0) > time.time()
        ):
            session["coordinator_token"] = request["token"]
        with self.store.lock:
            decision = self.store.runtime_control.admit(
                model,
                session["session_token"],
                occupied=self.ledger.model_capacity_used(model),
                total_occupied=self.ledger.capacity_used(),
                lease_seconds=max(180, plan["capacity"] * 10),
            )
        return session if decision["allowed"] else None

    @staticmethod
    def assignment(session):
        return {
            key: session[key] for key in ("session_token", "document_id", "stage", "agent_type")
        }

    def expire(self):
        now = time.time()
        for session in self.sessions():
            if session["state"] == "exited" or session["expires_at"] > now:
                continue
            if self.inflight(session):
                self.fail_runtime(
                    session["session_token"],
                    session.get("worker_id", "expired-worker"),
                    "timeout",
                    details={"scope": "model", "event_id": "expired:" + session["session_token"]},
                )
            else:
                self.end_session(session, "lease_expired")

    @staticmethod
    def inflight(session):
        return session["state"] in {"starting", "busy"} or (
            session["state"] == "idle"
            and bool(session.get("preflight"))
            and not session["preflight"].get("reading")
        )

    def end_session(self, session, reason):
        """Return an unstarted reservation and close its slot in the same transaction."""
        doc = None
        if session["state"] == "starting" and session.get("task_id"):
            doc = self.store.checkpoints.read(session["document_id"])
            task = next(task for task in doc["tasks"] if task["task_id"] == session["task_id"])
            if task["state"] == "leased" and task["claim"]["lease_token"] == session["lease_token"]:
                task["state"] = "ready"
        session.update(state="exited", exit_reason=reason, exited_at=time.time())
        with self.ledger.connect(write=True):
            self.store.runtime_control.release(session["session_token"])
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
        from hrs.processing.read_ahead import ReadAhead

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
                if session.get("preflight") and "reading" not in session["preflight"]:
                    return (
                        {
                            "state": "wait",
                            "sequence": self.ledger.sequence(),
                            "instructions": "Freeze your original observations before requesting the candidate.",
                        },
                        None,
                        None,
                    )
                task = self.store.tasks.claim_task(
                    session["document_id"],
                    session["stage"],
                    worker_id,
                    source_first=True,
                    scheduled=True,
                    session=session,
                )
                if task is None:
                    if (
                        session.get("overlap_reading")
                        and session["stage"] == "review"
                        and not session.get("preflight")
                    ):
                        if packet := ReadAhead(self.store).begin(session, worker_id):
                            return packet
                    doc = self.store.status(session["document_id"])
                    if (
                        session.get("prewarmed")
                        and (session.get("overlap_reading") or not session["completed"])
                        and (
                            session["stage"] != "organization"
                            or not any(page["state"] == "pending" for page in doc["pages"])
                        )
                        and any(
                            task["stage"]
                            in (
                                {"ocr", "review", "verify", "navigation"}
                                if session["stage"] == "organization"
                                else {"ocr"}
                            )
                            and task["state"] in {"ready", "leased"}
                            for task in doc["tasks"]
                        )
                    ):
                        if session["expires_at"] < time.time() + 60:
                            session["expires_at"] = time.time() + 180
                            self.ledger.save_session(session)
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
            if session.get("preflight") and not task.get("source_reading"):
                task = ReadAhead(self.store).bind(session, task)
            task = {**task, **task["claim"], "processing_policy": doc.get("processing_policy", {})}
            task["session_max_tasks"] = session.get("max_tasks", 2)
        if task.get("source_reading", {}).get("reading", {}).get("preflight"):
            access = task["source_reading"]["image_access"]
            return task, {**access, "image_receipt": access["receipt"]}, None
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

    def freeze(self, token, worker_id, reading):
        from hrs.processing.read_ahead import ReadAhead

        with self.store.lock:
            session = self.session(token, worker_id)
            if session.get("preflight"):
                ReadAhead(self.store).freeze(session, worker_id, reading)
                if not session.get("task_id"):
                    task, _, _ = self.open(token, worker_id)
                    if task.get("state") == "wait":
                        return task
            else:
                self.store.review_candidate(
                    session["document_id"], session["task_id"], session["lease_token"], reading
                )
            session = self.session(token, worker_id)
        task = self.store.get_task(session["document_id"], session["task_id"])
        candidate = self.store.candidate_text(
            session["document_id"], session["task_id"], session["lease_token"], limit=4000
        )
        return {
            "candidate": candidate,
            "input_sha256": task["claim"]["input_sha256"],
            "source_reading_sha256": task["source_reading"]["sha256"],
        }

    def submit(self, token, worker_id, result):
        session = self.session(token, worker_id, allow_closed=True)
        submission_sha256 = digest(result)
        if receipt := session.get("submissions", {}).get(submission_sha256):
            return {
                **self.publish_ready(receipt),
                "already_committed": True,
                "session_state": session["state"],
                "sequence": self.ledger.sequence(),
            }
        if session["state"] != "busy" or session["expires_at"] <= time.time():
            raise ValueError("Session has no active task to submit")
        session["submission_sha256"] = submission_sha256
        result = deepcopy(result)
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
                    **self.publish_ready(saved),
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
            **self.publish_ready(receipt),
            "session_state": session["state"],
            "completed": session["completed"],
            "sequence": self.ledger.sequence(),
        }

    def publish_ready(self, receipt):
        """The existing full-book gate, immediately after the last organization commit."""
        if receipt.get("state") != "ready":
            return receipt
        try:
            publication = self.store.publish(receipt["document_id"])
        except WorkflowStopped:
            # An explicit pause/cancel wins over automatic publication.
            return {**receipt, "state": self.store.status(receipt["document_id"])["state"]}
        return {**receipt, "state": "published", "publication": publication}

    def close(self, token, worker_id, *, failure_code=None):
        if failure_code is not None:
            return self.fail_runtime(token, worker_id, failure_code, details={"scope": "account"})
        with self.store.lock:
            session = self.session(token, worker_id, allow_closed=True)
            if session["state"] == "exited":
                return {"state": "exited"}
            if session["state"] == "busy":
                raise ValueError("Finish or report the active task before closing its session")
            self.end_session(session, "worker_finished")
            return {"state": "exited"}

    def fail_runtime(self, token, worker_id, code, *, details):
        """One transaction handles observed failures and MCP reports for the same lease."""
        from hrs.processing.contracts import TECHNICAL_FAILURE_CODES, model_for_stage

        if code not in TECHNICAL_FAILURE_CODES:
            raise ValueError("Use a supported technical failure code")
        with self.store.lock, self.ledger.connect(write=True):
            session = self.session(token, worker_id, allow_closed=True)
            if session["state"] == "exited":
                return {"state": "exited", "duplicate": True}
            doc = self.store.checkpoints.read(session["document_id"])
            task = next(
                (
                    t
                    for t in doc["tasks"]
                    if t["task_id"] == session.get("task_id")
                    and t["state"] == "leased"
                    and t["claim"]["lease_token"] == session.get("lease_token")
                ),
                None,
            )
            details = {
                **details,
                "model": session.get("actual_model", model_for_stage(session["stage"])),
            }
            if task is not None:
                self.store.tasks.report_task_failure(
                    doc["id"],
                    task["task_id"],
                    session["lease_token"],
                    code,
                    details=details,
                )
                session = self.session(token, worker_id, allow_closed=True)
            elif session.get("task_id"):
                # A late report must not affect a result already committed or another lease.
                self.end_session(session, "stale_runtime_failure")
                return {"state": "exited", "stale": True}
            elif session["state"] == "cooldown":
                self.end_session(session, code)
                return {"state": "exited", "duplicate": True}
            else:
                pressure = self.store.runtime_control.failure(
                    code,
                    "session:" + token,
                    model=details["model"],
                    scope=details.get("scope", "model"),
                    retry_after_seconds=details.get("retry_after"),
                    observed_capacity=(
                        self.ledger.capacity_used()
                        if details.get("scope") == "account"
                        else self.ledger.model_capacity_used(details["model"])
                    ),
                )
                scheduler = doc.setdefault("scheduler", {})
                if details.get("scope") == "account":
                    scheduler["cooldown_until"] = pressure.get("retry_at", time.time() + 30)
                scheduler.setdefault("runtime_failures", []).append(
                    {
                        "session_token": token,
                        "stage": session["stage"],
                        "code": code,
                        "at": time.time(),
                        "details": details,
                    }
                )
                self.store.checkpoints.write(doc)
            self.end_session(session, code)
            return {"state": "exited", "failure_code": code}

    def runtime_stop_reason(self, document_id):
        """Stop an owned idle coordinator once all remaining automatic work is blocked."""
        from hrs.processing.contracts import model_for_stage

        with self.store.lock:
            self.expire()
            doc = self.store.checkpoints.read(document_id)
            remaining = [t for t in doc["tasks"] if t["state"] in {"ready", "leased"}]
            if not remaining or doc.get("run_request", {}).get("pause_requested"):
                return None
            if any(
                session["document_id"] == document_id
                and session["expires_at"] > time.time()
                and self.inflight(session)
                for session in self.sessions()
            ):
                return None
            if any(
                t["state"] == "leased" and t["claim"]["expires_at"] > time.time() for t in remaining
            ):
                return None
            gates = [
                self.store.runtime_control.gate(model_for_stage(t["stage"]))
                for t in remaining
                if not t.get("technical_blocked")
            ]
            if any(g.get("state") != "blocked" for g in gates):
                return None
            return {
                "type": "RuntimeFailure",
                "code": "tool_error",
                "scope": "model",
                "model": "gpt-6.1-sol",
                "message": "Automatic runtime recovery budget exhausted; checkpoints retained",
                "reason": "runtime_blocked",
                "event_id": "blocked:" + doc.get("run_request", {}).get("token", ""),
            }
