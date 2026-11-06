"""Original-bound human handoff, drafts protection and final decisions."""

from __future__ import annotations

import time

from hrs.processing.acceptance import decision_scope as resolve_decision_scope
from hrs.processing.hashing import digest
from hrs.processing.workflow_rules import (
    new_task,
    request_continuation,
    require_active,
)


class HumanReviewWorkflow:
    def __init__(self, context, navigation, publications, runs):
        self.context = context
        self.navigation = navigation
        self.publications = publications
        self.runs = runs

    def human_queue(self) -> list[dict]:
        """All unresolved page reviews are available to people, including repeated failures."""
        with self.context.repository.lock:
            rows = []
            for document_id in self.context.repository.ids():
                doc = self.context.repository.read(document_id)
                for page in doc["pages"]:
                    if page["state"] != "pending":
                        continue
                    attempts = [
                        t
                        for t in doc["tasks"]
                        if t["page"] == page["page"]
                        and t.get("result", {}).get("verdict") == "pending"
                    ]
                    rows.append(
                        {
                            "document_id": doc["id"],
                            "title": doc["title"],
                            "page": page["page"],
                            "unresolved_attempts": len(attempts),
                            "issues": attempts[-1]["result"]["issues"],
                        }
                    )
            return rows

    def request_human_review(
        self, document_id: str, page: int, issue: str, evidence: dict, worker_id: str
    ) -> dict:
        """Retain an explicit Codex audit dispute without impersonating a human."""
        if (
            not isinstance(issue, str)
            or not issue.strip()
            or not isinstance(worker_id, str)
            or not worker_id.strip()
            or worker_id.strip().startswith("human:")
        ):
            raise ValueError(
                "A machine auditor identity and an unresolved source issue are required"
            )
        with self.context.verified(document_id) as doc:
            if type(page) is not int or not 1 <= page <= len(doc["pages"]):
                raise ValueError("Unknown physical page")
            target = doc["pages"][page - 1]
            history = [t for t in doc["tasks"] if t["page"] == page]
            if any(t["stage"] == "human_review" for t in history):
                raise ValueError("Human approvals and drafts cannot be replaced by a machine audit")
            text = target.get("approved") or target.get("candidate") or target.get("ocr")
            if (
                not text
                or not isinstance(evidence, dict)
                or evidence.get("source_sha256") != doc["source"]["sha256"]
                or evidence.get("image_sha256") != target["image_sha256"]
                or evidence.get("markdown_sha256") != text["sha256"]
            ):
                raise ValueError("Audit evidence does not match the current source, page and text")
            observations = evidence.get("observations")
            if (
                not evidence.get("original_details")
                or not isinstance(observations, list)
                or not observations
                or any(not isinstance(s, str) or not s.strip() for s in observations)
            ):
                raise ValueError(
                    "Human handoff requires retained original-detail evidence and observations"
                )
            self.context.evidence.verify_details(doc, {"page": page}, evidence)
            prior = next(
                (
                    t
                    for t in reversed(history)
                    if t["stage"] == "machine_handoff" and t["state"] == "pending"
                ),
                None,
            )
            if (
                prior
                and prior["inputs"]["audit_evidence"] == evidence
                and prior["result"]["issues"] == [issue.strip()]
                and prior["receipt"]["worker_id"] == worker_id.strip()
            ):
                return {
                    "document_id": document_id,
                    "page": page,
                    "state": "pending",
                    "receipt": prior["receipt"],
                }
            if doc.get("run_request", {}).get("state") == "running" or any(
                t["state"] == "leased" and t["claim"]["expires_at"] > time.time()
                for t in doc["tasks"]
            ):
                raise ValueError(
                    "Wait for the active coordinator and workers before an audit handoff"
                )
            if doc["state"] == "published":
                self.publications.begin_revision(document_id, issue, pages=[page])
                doc = self.context.repository.read(document_id)
                target = doc["pages"][page - 1]
            for task in doc["tasks"]:
                if task["stage"] == "organization" or (
                    task["page"] == page and task["state"] in {"ready", "leased", "pending"}
                ):
                    task["state"] = "superseded"
            task = new_task("machine_handoff", page)
            inputs = {
                "document_id": document_id,
                "source_sha256": doc["source"]["sha256"],
                "page": page,
                "image_sha256": target["image_sha256"],
                "markdown": text["markdown"],
                "markdown_sha256": text["sha256"],
                "audit_evidence": evidence,
            }
            result = {
                "verdict": "pending",
                "issues": [issue.strip()],
                "observations": evidence["observations"],
                "original_details": evidence["original_details"],
            }
            receipt = {
                "task_id": task["task_id"],
                "worker_id": worker_id.strip(),
                "input_sha256": digest(inputs),
                "result_sha256": digest(result),
                "approval_type": "machine",
                "human_approval": False,
                "sealed_gold_approval": False,
                "origin": "codex_original_audit_handoff",
                "created_at": time.time(),
            }
            task.update(
                state="pending",
                inputs=inputs,
                claim={"input_sha256": digest(inputs)},
                result=result,
                receipt=receipt,
            )
            doc["tasks"].append(task)
            target.pop("approved", None)
            target.update(
                state="pending",
                review={**receipt, "verdict": "pending", "issues": result["issues"]},
            )
            doc.update(state="pending", organization=None, publication=None)
            request = request_continuation(doc)
            request.update(state="waiting_human", error=None, not_before=0, ready_after=0)
            request["history"].append(
                {
                    "event": "audit_handoff",
                    "at": time.time(),
                    "page": page,
                    "evidence_sha256": digest(evidence),
                }
            )
            self.context.repository.write(doc)
            return {
                "document_id": document_id,
                "page": page,
                "state": "pending",
                "receipt": receipt,
            }

    def packet(self, doc: dict, number: int) -> dict:
        if type(number) is not int or not 1 <= number <= len(doc["pages"]):
            raise ValueError("Unknown physical page")
        page = doc["pages"][number - 1]
        if page["state"] != "pending" or doc["publication"]:
            raise ValueError("Only unpublished pending pages can receive human review")
        history = [t for t in doc["tasks"] if t["page"] == number]
        pending = next(t for t in reversed(history) if t["state"] == "pending")
        text = (
            pending["result"]["markdown"]
            if pending["stage"] == "human_review"
            else pending["inputs"]["markdown"]
        )
        return {
            "document_id": doc["id"],
            "title": doc["title"],
            "page": number,
            "source_sha256": doc["source"]["sha256"],
            "image_sha256": page["image_sha256"],
            "pending_task_id": pending["task_id"],
            "markdown": text,
            **(
                {
                    "suggested_markdown": pending["result"].get("suggested_markdown")
                    or pending["result"].get("markdown")
                }
                if pending["stage"] != "human_review"
                and (
                    pending["result"].get("suggested_markdown") or pending["result"].get("markdown")
                )
                else {}
            ),
            "ocr_markdown": page["ocr"]["markdown"],
            "history": [
                {
                    "task_id": t["task_id"],
                    "stage": t["stage"],
                    "state": t["state"],
                    "result": t.get("result"),
                    "receipt": t.get("receipt"),
                    **({"source_reading": t["source_reading"]} if t.get("source_reading") else {}),
                }
                for t in history
            ],
        }

    def human_packet(self, document_id: str, page: int) -> dict:
        with self.context.verified(document_id) as doc:
            packet = self.packet(doc, page)
            return {**packet, "input_sha256": digest(packet)}

    def record_human_review(
        self,
        document_id: str,
        page: int,
        *,
        input_sha256: str,
        reviewer: str = "本地使用者",
        markdown: str,
        note: str = "本地确认",
        verdict: str,
        decision_scope: str | None = None,
        resolutions: list[dict] | None = None,
    ) -> dict:
        """Record an explicit local form submission. Deliberately not exposed through MCP."""
        if verdict not in {"approved", "pending"} or not reviewer.strip() or not note.strip():
            raise ValueError(
                "Human review requires a reviewer, reason and approved/pending decision"
            )
        if not isinstance(markdown, str):
            raise ValueError("Human review requires full page Markdown")
        if resolutions is not None and (
            not isinstance(resolutions, list)
            or any(
                not isinstance(item, dict)
                or not isinstance(item.get("id"), str)
                or item.get("action") not in {"keep", "adopt", "edit", "defer"}
                for item in resolutions
            )
            or len({item["id"] for item in resolutions}) != len(resolutions)
            or (verdict == "approved" and any(item["action"] == "defer" for item in resolutions))
        ):
            raise ValueError("Review resolutions require distinct issue IDs and final actions")
        # A draft preserves work without granting approval, including when permission
        # needs repair. Final decisions must bind to a valid current scope.
        decision_scope = (
            resolve_decision_scope(self.context.repository.root, decision_scope)
            if verdict == "approved"
            else "draft"
        )
        with self.context.verified(document_id) as doc:
            require_active(doc, allow_paused=True)
            packet = self.packet(doc, page)
            if digest(packet) != input_sha256:
                raise ValueError("Review input changed; reload before deciding")
            previous = next(t for t in doc["tasks"] if t["task_id"] == packet["pending_task_id"])
            task = new_task("human_review", page)
            receipt = {
                "task_id": task["task_id"],
                "worker_id": "human:" + reviewer.strip(),
                "reviewer": reviewer.strip(),
                "input_sha256": input_sha256,
                "approval_type": "human",
                "human_approval": verdict == "approved",
                "sealed_gold_approval": False,
                "origin": "local_human_review_form",
                "created_at": time.time(),
            }
            if decision_scope == "workflow_test":
                receipt.update(
                    approval_type="workflow_test",
                    human_approval=False,
                    origin="user_authorized_workflow_test",
                    content_verified=False,
                )
            elif decision_scope == "draft":
                receipt.update(
                    approval_type="human_draft", origin="local_human_draft", human_approval=False
                )
            result = {
                "input_sha256": input_sha256,
                "markdown": markdown,
                "verdict": verdict,
                "note": note.strip(),
                "issues": previous["result"].get("issues", [note.strip()])
                if verdict == "pending"
                else [],
                **({"resolutions": resolutions} if resolutions is not None else {}),
            }
            task.update(
                state="completed" if verdict == "approved" else "pending",
                inputs=packet,
                claim={"input_sha256": input_sha256},
                result=result,
                receipt={**receipt, "result_sha256": digest(result)},
            )
            previous["state"] = "superseded"
            task["review_of"] = previous["task_id"]
            doc["tasks"].append(task)
            target = doc["pages"][page - 1]
            target["review"] = {**receipt, "verdict": verdict, "issues": result["issues"]}
            target["state"] = "approved" if verdict == "approved" else "pending"
            if verdict == "approved":
                target["approved"] = {**receipt, "markdown": markdown, "sha256": digest(markdown)}
            self.navigation.refresh_document(doc)
            if all(p["state"] == "approved" for p in doc["pages"]):
                if not any(
                    t["stage"] == "navigation" and t["state"] in {"ready", "leased"}
                    for t in doc["tasks"]
                ) and not any(
                    t["stage"] == "organization" and t["state"] != "superseded"
                    for t in doc["tasks"]
                ):
                    doc["tasks"].append(new_task("organization"))
                doc["state"] = "processing"
                request_continuation(doc, followup=True)
            else:
                doc["state"] = "pending"
                if verdict == "approved" and any(t["state"] == "ready" for t in doc["tasks"]):
                    request_continuation(doc, followup=True)
            self.context.repository.write(doc)
            return {
                "document_id": document_id,
                "page": page,
                "state": doc["state"],
                "page_state": target["state"],
                "receipt": task["receipt"],
            }
