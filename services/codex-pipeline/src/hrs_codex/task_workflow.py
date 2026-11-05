"""Source import, task leasing and machine submissions."""

from __future__ import annotations

import time
from uuid import uuid4

from hrs_codex.contracts import MachineStage, TaskReceipt
from hrs_codex.errors import LeaseConflict
from hrs_codex.hashing import digest
from hrs_codex.task_results import apply_navigation, apply_ocr, apply_organization, apply_review
from hrs_codex.workflow_rules import (
    STAGES,
    new_task,
    require_active,
)


class TaskWorkflow:
    def __init__(self, context, navigation):
        self.context = context
        self.navigation = navigation

    def scheduling_plan(self, document_id: str) -> dict:
        from hrs_codex.scheduling import scheduling_plan

        with self.context.repository.lock:
            doc = self.context.repository.read(document_id)
            plan = scheduling_plan(doc)
            if self.context.repository.ledger:
                from collections import Counter
                from statistics import median

                ledger = self.context.repository.ledger
                sessions = [
                    session
                    for session in ledger.sessions()
                    if session["document_id"] == document_id
                ]
                plan["runtime_counts"] = dict(Counter(session["state"] for session in sessions))
                plan["workspace_occupied"] = ledger.capacity_used()
                for stage, metric in plan["metrics"].items():
                    timings = [
                        seconds
                        for session in sessions
                        if session["stage"] == stage
                        for seconds in session.get("submission_timings", [])
                    ]
                    if timings:
                        metric["phase_medians"]["submission_seconds"] = round(
                            median(timings[-20:]), 3
                        )
            scheduler = doc.setdefault("scheduler", {})
            if (
                scheduler.get("allocation") != plan["workers"]
                or time.time() - scheduler.get("rebalanced_at", 0) >= 30
            ):
                scheduler.update(allocation=plan["workers"], rebalanced_at=time.time())
                self.context.repository.write(doc)
            return plan

    def upgrade_review_policy(self, document_id: str, protected_pages=()) -> dict:
        """Explicit resume migration; retain earlier decisions and all human-owned work."""
        with self.context.repository.lock:
            doc = self.context.repository.read(document_id)
            if doc.get("processing_policy", {}).get("review_failure") == "bounded":
                return {"reopened_pages": []}
            if any(t["state"] == "leased" for t in doc["tasks"]):
                raise ValueError("Pause active execution before changing review policy")
            previous = dict(doc.get("processing_policy", {}))
            doc.setdefault("processing_policy", {}).update(review_failure="bounded")
            reopened = []
            for page in doc["pages"]:
                if page["state"] != "pending" or page["page"] in protected_pages:
                    continue
                history = [t for t in doc["tasks"] if t["page"] == page["page"]]
                if any(t["stage"] == "human_review" for t in history) or page.get(
                    "automatic_review_attempts"
                ):
                    continue
                for task in history:
                    if task["state"] == "pending":
                        task["state"] = "superseded"
                page["state"] = "awaiting_review"
                doc["tasks"].append(new_task("review", page["page"]))
                reopened.append(page["page"])
            doc.setdefault("policy_history", []).append(
                {
                    "previous": previous,
                    "at": time.time(),
                    "reopened_pages": reopened,
                    "protected_pages": list(protected_pages),
                    "reason": "explicit_resume_bounded_review",
                }
            )
            self.context.repository.write(doc)
            return {"reopened_pages": reopened}

    def report_task_failure(
        self, document_id: str, task_id: str, lease_token: str, code: str
    ) -> dict:
        if code not in {
            "rate_limit",
            "429",
            "service_unavailable",
            "transport",
            "timeout",
            "tool_error",
        }:
            raise ValueError("Use a supported technical failure code")
        with self.context.repository.lock:
            doc = self.context.repository.read(document_id)
            task = next(t for t in doc["tasks"] if t["task_id"] == task_id)
            if task["state"] != "leased" or task["claim"]["lease_token"] != lease_token:
                raise LeaseConflict("Task does not have this active lease")
            failures = task.setdefault("technical_failures", [])
            failures.append({"code": code, "at": time.time(), "claim": dict(task["claim"])})
            task["state"] = "ready"
            task["retry_after"] = time.time() + min(300, 30 * 2 ** (len(failures) - 1))
            if code in {"rate_limit", "429", "service_unavailable"}:
                doc.setdefault("scheduler", {})["cooldown_until"] = task["retry_after"]
            ledger = self.context.repository.ledger
            session = (
                next(
                    (
                        session
                        for session in ledger.sessions()
                        if session["document_id"] == document_id
                        and session.get("task_id") == task_id
                        and session.get("lease_token") == lease_token
                        and session["state"] in {"starting", "busy"}
                    ),
                    None,
                )
                if ledger
                else None
            )
            if session:
                session.update(state="cooldown", expires_at=task["retry_after"], failure_code=code)
            self.commit(doc, session)
            return {
                "state": task["state"],
                "retry_after": task["retry_after"],
                "attempts": len(failures),
            }

    def claim_task(
        self,
        document_id: str,
        stage: MachineStage,
        worker_id: str,
        lease_seconds: int = 900,
        *,
        source_first: bool = False,
        scheduled: bool = False,
        session: dict | None = None,
    ) -> dict | None:
        if stage not in STAGES or not isinstance(worker_id, str) or not worker_id.strip():
            raise ValueError("A valid stage and nonempty worker ID are required")
        if not 1 <= lease_seconds <= 86400:
            raise ValueError("Lease must be between 1 and 86400 seconds")
        with self.context.verified_task(document_id, stage=stage) as doc:
            require_active(doc)
            if scheduled:
                from hrs_codex.scheduling import scheduling_plan

                plan = scheduling_plan(doc)
                if plan["cooldown_until"] > time.time():
                    return None
                ledger = self.context.repository.ledger
                if (
                    ledger
                    and ledger.capacity_used(session.get("session_token") if session else None)
                    >= plan["capacity"]
                ):
                    return None
                active = {
                    t["claim"]["worker_id"]
                    for t in doc["tasks"]
                    if t["stage"] == stage
                    and t["state"] == "leased"
                    and t["claim"]["expires_at"] > time.time()
                }
                all_active = {
                    t["claim"]["worker_id"]
                    for t in doc["tasks"]
                    if t["state"] == "leased" and t["claim"]["expires_at"] > time.time()
                }
                if worker_id in all_active or len(all_active) >= plan["capacity"]:
                    return None
                finished = sum(
                    t.get("receipt", {}).get("worker_id") == worker_id for t in doc["tasks"]
                )
                if finished >= plan["batch_size"] or (
                    worker_id not in active and len(active) >= plan["workers"][stage]
                ):
                    return None
            from hrs_codex.scheduling import task_priority

            for task in sorted(doc["tasks"], key=lambda t: task_priority(doc, t)):
                if task["stage"] != stage or task["state"] not in {"ready", "leased"}:
                    continue
                if task.get("retry_after", 0) > time.time():
                    continue
                if task["state"] == "leased" and task["claim"]["expires_at"] > time.time():
                    continue
                inputs = self.context.inputs(doc, task)
                if inputs.get("prior_worker_id") == worker_id:
                    raise ValueError(
                        "Review and correction verification require a separate subagent"
                    )
                claim = {
                    "worker_id": worker_id,
                    "lease_token": uuid4().hex,
                    "expires_at": time.time() + lease_seconds,
                    "started_at": time.time(),
                    "input_sha256": digest(inputs),
                }
                if task.get("source_reading"):
                    task.setdefault("source_reading_history", []).append(task.pop("source_reading"))
                task.pop("image_access", None)
                task.pop("candidate_accesses", None)
                task["bounded_candidate_required"] = (
                    scheduled
                    and source_first
                    and stage in {"review", "verify"}
                    and len(inputs.get("markdown", "")) > 2000
                )
                if source_first and stage in {"review", "verify"}:
                    task["source_first_required"] = True
                task.update(state="leased", claim=claim, inputs=inputs)
                if session is not None:
                    session.update(task_id=task["task_id"], lease_token=claim["lease_token"])
                self.commit(doc, session)
                return {**task, **claim}
            return None

    def submit_task(
        self, document_id: str, task_id: str, lease_token: str, result: dict, session=None
    ) -> dict:
        with self.context.verified_task(document_id, task_id=task_id) as doc:
            require_active(doc)
            task = next(t for t in doc["tasks"] if t["task_id"] == task_id)
            claim = task.get("claim", {})
            if task["state"] != "leased" or claim.get("lease_token") != lease_token:
                raise LeaseConflict("Task does not have this active lease")
            if claim["expires_at"] <= time.time():
                raise LeaseConflict("Task lease expired; reclaim before submitting")
            if (
                result.get("input_sha256") != claim["input_sha256"]
                or digest(self.context.inputs(doc, task)) != claim["input_sha256"]
            ):
                raise ValueError("Task input hash mismatch")
            self.context.evidence.verify_details(doc, task, result)
            stage = task["stage"]
            automatic_verification = False
            if (
                stage == "review"
                and doc.get("processing_policy", {}).get("review_failure") == "human"
                and result.get("verdict") == "corrections"
            ):
                result = {
                    **result,
                    "verdict": "pending",
                    "issues": result.get("issues") or ["审核建议修改，请人工核对后确认。"],
                    "suggested_markdown": result.get("markdown"),
                }
            if task.get("source_first_required") and not task.get("source_reading"):
                raise ValueError(
                    "Record original-first observations through review_candidate before judging the candidate"
                )
            if task.get("bounded_candidate_required") and result.get("verdict") != "pending":
                covered = 0
                for access in sorted(task.get("candidate_accesses", []), key=lambda a: a["start"]):
                    if access["lease_token"] == lease_token and access["start"] <= covered:
                        covered = max(covered, access["end"])
                if covered < len(task["inputs"]["markdown"]):
                    raise ValueError(
                        "Read the complete candidate with candidate_text before submitting a verdict"
                    )
            page = doc["pages"][task["page"] - 1] if task["page"] is not None else None
            receipt: TaskReceipt = {
                "task_id": task_id,
                "worker_id": claim["worker_id"],
                "input_sha256": claim["input_sha256"],
                "approval_type": "machine",
                "human_approval": False,
                "created_at": time.time(),
            }
            if claim.get("started_at"):
                receipt.update(
                    duration_seconds=max(0, time.time() - claim["started_at"]),
                    queue_seconds=max(
                        0,
                        claim.get("reserved_at", claim["started_at"])
                        - task.get("created_at", claim["started_at"]),
                    ),
                    startup_seconds=max(
                        0, claim["started_at"] - claim.get("reserved_at", claim["started_at"])
                    ),
                )
            policy = doc.get("processing_policy", {})
            if policy:
                receipt["configured_model"] = policy.get(
                    "ocr_model"
                    if stage == "ocr"
                    else "organization_model"
                    if stage in {"organization", "navigation"}
                    else "review_model"
                )
            if task.get("source_reading"):
                receipt["source_reading_sha256"] = task["source_reading"]["sha256"]
                reading = task["source_reading"]
                receipt["original_reading_seconds"] = max(
                    0, reading["created_at"] - reading["image_access"]["opened_at"]
                )
                receipt["candidate_comparison_seconds"] = max(
                    0, time.time() - reading["created_at"]
                )
            if stage == "ocr":
                apply_ocr(doc, task, result, receipt)
            elif stage in {"review", "verify"}:
                result, automatic_verification = apply_review(doc, task, result, receipt)
            elif stage == "navigation":
                apply_navigation(doc, task, result, receipt)
            else:
                apply_organization(doc, task, result, receipt)
            task.update(
                state="pending"
                if result.get("verdict") == "pending" and not automatic_verification
                else "completed",
                result=result,
                receipt={**receipt, "result_sha256": digest(result)},
            )
            if (
                stage in {"review", "verify"}
                and page.get("approved")
                and result.get("page_observation")
            ):
                from hrs_codex.navigation import observation

                page["observation"] = observation(page, result["page_observation"])
                page["page_kind"] = page["observation"]["kind"]
            self.navigation.refresh_document(doc)
            if all(p["state"] == "approved" for p in doc["pages"]):
                if not any(
                    t["stage"] == "organization" and t["state"] != "superseded"
                    for t in doc["tasks"]
                ) and not any(
                    t["stage"] == "navigation" and t["state"] in {"ready", "leased"}
                    for t in doc["tasks"]
                ):
                    doc["tasks"].append(new_task("organization"))
                doc["state"] = (
                    "ready"
                    if doc["organization"]
                    else ("pending" if task["state"] == "pending" else "processing")
                )
            else:
                doc["state"] = (
                    "pending"
                    if any(p["state"] == "pending" for p in doc["pages"])
                    else "processing"
                )
            if session is not None:
                completed = session.get("completed", 0) + 1
                session.update(
                    completed=completed,
                    state="exited" if completed >= 2 else "idle",
                    expires_at=time.time() + 180,
                    last_completion_at=time.time(),
                )
            outcome = {
                "document_id": doc["id"],
                "task_id": task_id,
                "task_state": task["state"],
                "state": doc["state"],
            }
            if session is not None and session.get("submission_sha256"):
                session.setdefault("submissions", {})[session.pop("submission_sha256")] = outcome
            self.commit(doc, session)
            return outcome

    def commit(self, doc, session=None):
        if session is None:
            self.context.repository.write(doc)
            return
        with self.context.repository.ledger.connect(write=True):
            self.context.repository.write(doc)
            self.context.repository.ledger.save_session(session)

    def retry_task(self, document_id: str, task_id: str) -> dict:
        with self.context.repository.lock:
            doc = self.context.repository.read(document_id)
            task = next(t for t in doc["tasks"] if t["task_id"] == task_id)
            if task["state"] != "pending":
                raise ValueError(
                    "Only pending tasks can be retried; active leases expire automatically"
                )
            if task["stage"] == "human_review":
                raise ValueError("A human draft must be resolved in the human review workspace")
            if task["stage"] == "machine_handoff":
                raise ValueError(
                    "An explicit source dispute awaits human review; machine retry cannot reopen it"
                )
            task["state"] = "superseded"
            # A rejected correction needs a new proposal against retained OCR,
            # followed by another independent verification of that proposal.
            stage = "review" if task["stage"] == "verify" else task["stage"]
            replacement = new_task(stage, task["page"])
            replacement["retry_of"] = task_id
            doc["tasks"].append(replacement)
            if task["page"] is not None:
                doc["pages"][task["page"] - 1]["state"] = "awaiting_" + stage
            doc["state"] = "processing"
            self.context.repository.write(doc)
            return replacement

    def fallback_page(
        self, document_id: str, page: int, reason: str, *, new_evidence: str | None = None
    ) -> dict:
        """Recover content through bounded full-page OCR; never reinterpret runtime failures."""
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("Fallback requires a content reason")
        with self.context.verified(document_id) as doc:
            if doc.get("publication") or doc["state"] == "published":
                raise ValueError("Published evidence requires a separate revision workspace")
            if type(page) is not int or not 1 <= page <= len(doc["pages"]):
                raise ValueError("Unknown physical page")
            target = doc["pages"][page - 1]
            history = [t for t in doc["tasks"] if t["page"] == page]
            if target["state"] == "approved" or any(
                t["stage"] in {"human_review", "machine_handoff"} for t in history
            ):
                raise ValueError(
                    "Approved content and human decisions or drafts cannot be replaced"
                )
            if any(
                t["state"] == "leased" and t["claim"]["expires_at"] > time.time()
                for t in doc["tasks"]
            ):
                raise ValueError("Wait for active task leases before changing extraction route")
            route = target.get("route", target.get("extraction_plan", {}).get("route", "ocr"))
            if target["state"] != "pending" and not (
                route in {"native", "mixed"} and target.get("ocr")
            ):
                raise ValueError(
                    "Fallback requires a pending content review; retry technical errors as runs"
                )
            budget = target.get("fallback_budget", {"fullpage_switches": 0, "fullpage_retries": 0})
            if route in {"native", "mixed"}:
                if budget["fullpage_switches"] >= 1:
                    raise ValueError("Full-page fallback budget exhausted; human review required")
                budget["fullpage_switches"] += 1
            else:
                if budget["fullpage_retries"] >= 1:
                    raise ValueError("Full-page retry budget exhausted; human review required")
                if not isinstance(new_evidence, str) or not new_evidence.strip():
                    raise ValueError("Full-page OCR retry requires explicit new evidence")
                budget["fullpage_retries"] += 1
            record = {
                "created_at": time.time(),
                "from_route": route,
                "to_route": "ocr",
                "reason": reason.strip(),
                "new_evidence": new_evidence,
                "previous_extraction_plan": target.get("extraction_plan"),
                "previous_candidates": {
                    k: target[k] for k in ("ocr", "candidate", "approved", "review") if k in target
                },
                "superseded_tasks": [t["task_id"] for t in history],
            }
            for task in history:
                task["state"] = "superseded"
            for key in ("ocr", "candidate", "approved", "review"):
                target.pop(key, None)
            target.update(
                state="awaiting_ocr",
                route="ocr",
                fallback_budget=budget,
                extraction_plan={
                    "policy": "codex-pdf-routing-v1",
                    "route": "ocr",
                    "native_markdown": "",
                    "native_sha256": digest(""),
                    "regions": [],
                    "reason_codes": ["fullpage_fallback"],
                },
            )
            target.setdefault("fallback_history", []).append(record)
            doc["tasks"].append(new_task("ocr", page))
            doc["state"] = (
                "pending" if any(p["state"] == "pending" for p in doc["pages"]) else "processing"
            )
            self.context.repository.write(doc)
            return {
                "document_id": document_id,
                "page": page,
                "route": "ocr",
                "fallback_budget": budget,
                "state": doc["state"],
                "fallback": record,
            }
