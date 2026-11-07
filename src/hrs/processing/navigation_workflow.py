"""Directory derivation and source-bound human navigation decisions."""

from __future__ import annotations

import re
import time

from hrs.processing.acceptance import decision_scope as resolve_decision_scope
from hrs.processing.hashing import digest
from hrs.processing.workflow_rules import (
    new_task,
    request_continuation,
    require_active,
)


class NavigationWorkflow:
    def __init__(self, context):
        self.context = context

    def refresh_document(self, doc: dict, *, retry_unresolved: bool = False) -> None:
        from hrs.processing.navigation import observation, resolve_navigation
        from hrs.processing.navigation_coverage import coverage_report, parse_plan
        from hrs.processing.navigation_verification import (
            prepare_link_verification,
            reconcile_link_tasks,
        )
        from hrs.processing.page_observation import bind_proposal

        for p in doc["pages"]:
            if p.get("approved") and p.get("structure_proposal") and not p.get("observation"):
                try:
                    bind_proposal(p)
                    p.pop("structure_binding_warning", None)
                except ValueError as error:
                    p["structure_binding_warning"] = str(error)
            if (
                p.get("approved")
                and not p.get("observation")
                and re.search(
                    r"(?mi)^\s*(?:#{1,6}\s*)?(?:目[ \t]*录|table of contents|contents)\s*$",
                    p["approved"]["markdown"],
                )
            ):
                p["page_kind"] = "toc"
                p["kind_basis"] = "title-candidate-requires-navigation-original-review"
            if (
                p.get("approved")
                and p.get("observation")
                and p["observation"].get("markdown_sha256") != p["approved"]["sha256"]
            ):
                try:
                    p["observation"] = observation(p, p["observation"])
                except ValueError:
                    p.pop("observation", None)
        doc["navigation"] = resolve_navigation(doc)
        reconcile_link_tasks(doc)
        coverage = coverage_report(doc)
        plan = parse_plan(doc, retry_unresolved=retry_unresolved)
        if plan["core"] and not any(
            t["stage"] == "navigation" and t["state"] in {"ready", "leased"} for t in doc["tasks"]
        ):
            task = new_task("navigation")
            task["parse_contract_version"] = 2
            if retry_unresolved:
                task["retry_unresolved"] = True
            doc["tasks"].append(task)
        prepare_link_verification(doc)
        if (
            all(p.get("approved") for p in doc["pages"])
            and coverage["coverage_complete"]
            and not doc.get("organization")
            and not any(
                t["stage"] == "organization" and t["state"] != "superseded" for t in doc["tasks"]
            )
            and not any(
                t["stage"] == "navigation" and t["state"] in {"ready", "leased"}
                for t in doc["tasks"]
            )
        ):
            doc["tasks"].append(new_task("organization"))

    def repair_structure(
        self, document_id: str, *, apply: bool = False, replan_navigation: bool = False
    ) -> dict:
        """Explicitly restore derived structure from original-review evidence, preserving approvals."""
        from hrs.processing.navigation_coverage import coverage_report
        from hrs.processing.page_observation import restore_observations

        with self.context.verified(document_id) as doc:
            if apply:
                require_active(doc, allow_paused=True)
            if apply and (
                any(
                    task["state"] == "leased"
                    and task.get("claim", {}).get("expires_at", 0) > time.time()
                    for task in doc["tasks"]
                )
                or (
                    doc.get("run_request", {}).get("state") == "running"
                    and doc["run_request"].get("lease_expires_at", 0) > time.time()
                )
            ):
                raise ValueError("Wait for active workers before repairing page structure")
            report = restore_observations(doc, apply=apply)
            old_contract = (doc.get("navigation") or {}).get("coverage_contract_version", 0)
            superseded = []
            if replan_navigation:
                for task in doc["tasks"]:
                    inputs = task.get("inputs") or task.get("verification_inputs", {})
                    if (
                        task["stage"] == "navigation"
                        and task["state"] in {"ready", "leased", "pending"}
                        and inputs.get("navigation_mode") != "verify_links"
                        and inputs.get(
                            "coverage_contract_version", task.get("parse_contract_version", 0)
                        )
                        < 2
                    ):
                        superseded.append(
                            {
                                "task_id": task["task_id"],
                                "state": task["state"],
                                "input_sha256": task.get("claim", {}).get("input_sha256"),
                            }
                        )
                        if apply:
                            task.update(
                                state="superseded",
                                superseded_at=time.time(),
                                superseded_reason="explicit_bounded_navigation_replan",
                            )
            if apply:
                nav = doc.get("navigation") or {}
                doc["navigation"] = nav
                nav["coverage_contract_version"] = 2
                if superseded:
                    nav.setdefault("parse_replans", []).append(
                        {
                            "at": time.time(),
                            "superseded_tasks": superseded,
                            "origin": "explicit_bounded_navigation_replan",
                        }
                    )
                self.refresh_document(doc, retry_unresolved=replan_navigation)
                if (
                    replan_navigation
                    and (superseded or old_contract < 2 or report["restored_pages"])
                    and not any(
                        doc.get("run_request", {}).get(flag)
                        for flag in ("pause_requested", "cancel_requested")
                    )
                    and any(
                        task["stage"] == "navigation" and task["state"] == "ready"
                        for task in doc["tasks"]
                    )
                ):
                    request_continuation(doc, resume=True)
                self.context.repository.write(doc)
            return {
                **report,
                "document_id": document_id,
                "applied": apply,
                "navigation_replanned": replan_navigation,
                "superseded_tasks": superseded,
                **coverage_report(doc),
            }

    def refresh_navigation(self, document_id: str) -> dict:
        """Refresh source-bound derived metadata; never run recognition or grant content approval."""
        with self.context.verified(document_id) as doc:
            before = (
                doc.get("navigation", {}).get("sha256"),
                [(task["task_id"], task["state"]) for task in doc["tasks"]],
            )
            self.refresh_document(doc)
            if before != (
                doc["navigation"]["sha256"],
                [(task["task_id"], task["state"]) for task in doc["tasks"]],
            ):
                self.context.repository.write(doc)
            return doc

    def navigation_decision(
        self,
        document_id: str,
        entry_id: str,
        *,
        expected_sha256: str,
        target_page: int | None,
        target_anchor: str = "",
        target_spans: list[dict] | None = None,
        target_title: str | None = None,
        decision_id: str | None = None,
        decision_scope: str | None = None,
    ) -> dict:
        request_values = [entry_id, expected_sha256, target_page, target_anchor]
        if decision_scope is not None:
            request_values.append(decision_scope)
        if target_spans is not None or target_title is not None:
            request_values.append({"target_spans": target_spans, "target_title": target_title})
        request_sha = digest(request_values)
        with self.context.repository.lock:
            doc = self.context.repository.read(document_id)
            nav = doc.get("navigation") or {}
            if decision_id:
                if not isinstance(decision_id, str) or len(decision_id) > 128:
                    raise ValueError("Invalid decision identity")
                receipt = doc.get("navigation_receipts", {}).get(decision_id)
                if receipt:
                    if receipt["request_sha256"] != request_sha:
                        raise ValueError("Decision identity belongs to a different request")
                    return nav
            require_active(doc, allow_paused=True)
            scope = resolve_decision_scope(self.context.repository.root, decision_scope)
            if nav.get("sha256") != expected_sha256:
                raise ValueError("Navigation changed; reload before confirming")
            entry = next(e for e in nav["entries"] if e["id"] == entry_id)
            target = None
            if target_page is None and (target_spans is not None or target_title is not None):
                raise ValueError("Unlinked navigation cannot contain target title fragments")
            if target_page is not None:
                if type(target_page) is not int or not 1 <= target_page <= len(doc["pages"]):
                    raise ValueError("Unknown target physical page")
                p = doc["pages"][target_page - 1]
                if (
                    not p.get("approved")
                    or not target_anchor
                    or p["approved"]["markdown"].count(target_anchor) != 1
                ):
                    raise ValueError(
                        "Navigation confirmation needs an approved unique target anchor"
                    )
                from hrs.processing.navigation import target_evidence
                from hrs.processing.navigation_matching import (
                    candidate_evidence,
                    comparison_text,
                    target_catalog,
                )
                from hrs.processing.navigation_spans import target_spans as retained_spans
                from hrs.processing.navigation_spans import validated_spans

                if target_spans is not None or target_title is not None:
                    if not target_spans or not isinstance(target_title, str) or not target_title:
                        raise ValueError("A logical target title requires its source fragments")
                    resolved = validated_spans(doc, target_spans, boundary=True)
                    if resolved[0]["page"] != target_page or resolved[0]["anchor"] != target_anchor:
                        raise ValueError("Target title fragments must begin at the selected anchor")
                    matches = [
                        record["target"]
                        for record in target_catalog(doc)
                        if comparison_text(target_title) == record["title"]
                        and retained_spans(record["target"]) == resolved
                    ]
                    if len(matches) != 1:
                        raise ValueError("Target title fragments do not identify one current title")
                    candidate = matches[0]
                else:
                    matches = [
                        candidate
                        for candidate in entry.get("candidates", [])
                        if candidate["physical_page"] == target_page
                        and candidate["anchor"] == target_anchor
                    ]
                    if len(matches) > 1:
                        raise ValueError(
                            "Choose the complete target title fragments before confirming"
                        )
                    candidate = matches[0] if matches else None
                target = (
                    candidate_evidence(doc, candidate)
                    if candidate
                    else target_evidence(p, target_anchor)
                )
            decisions = nav.setdefault("decisions", {})
            if entry_id in decisions:
                nav.setdefault("decision_history", []).append(
                    {"entry_id": entry_id, **decisions[entry_id]}
                )
            decisions[entry_id] = {
                "source_sha256": entry["source_sha256"],
                **(
                    {"source_spans_sha256": entry["source_spans_sha256"]}
                    if entry.get("source_spans_sha256")
                    else {}
                ),
                "target": target,
                "at": time.time(),
                "approval_type": "human_navigation",
                "content_approval": False,
                "final": True,
                **(
                    {
                        "approval_type": "workflow_test",
                        "content_verified": False,
                        "human_approval": False,
                        "origin": "user_authorized_workflow_test",
                    }
                    if scope == "workflow_test"
                    else {}
                ),
            }
            if decision_id:
                doc.setdefault("navigation_receipts", {})[decision_id] = {
                    "request_sha256": request_sha,
                    "entry_id": entry_id,
                    "at": time.time(),
                }
            self.refresh_document(doc)
            self.context.repository.write(doc)
            return doc["navigation"]
