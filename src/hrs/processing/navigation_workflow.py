"""Directory derivation and source-bound human navigation decisions."""

from __future__ import annotations

import re
import time

from hrs.processing.acceptance import decision_scope as resolve_decision_scope
from hrs.processing.hashing import digest
from hrs.processing.workflow_rules import (
    new_task,
    require_active,
)


class NavigationWorkflow:
    def __init__(self, context):
        self.context = context

    def refresh_document(self, doc: dict) -> None:
        from hrs.processing.navigation import (
            fingerprint,
            observation,
            resolve_navigation,
            toc_evidence,
        )
        from hrs.processing.navigation_verification import (
            prepare_link_verification,
            reconcile_link_tasks,
        )

        for p in doc["pages"]:
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
        toc = toc_evidence(doc)
        if (
            toc
            and fingerprint(toc) != (doc.get("navigation") or {}).get("parsed_toc_sha256")
            and not any(
                t["stage"] == "navigation" and t["state"] in {"ready", "leased"}
                for t in doc["tasks"]
            )
        ):
            doc["tasks"].append(new_task("navigation"))
        prepare_link_verification(doc)
        if (
            all(p.get("approved") for p in doc["pages"])
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
        decision_id: str | None = None,
        decision_scope: str | None = None,
    ) -> dict:
        request_values = [entry_id, expected_sha256, target_page, target_anchor]
        if decision_scope is not None:
            request_values.append(decision_scope)
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

                target = target_evidence(p, target_anchor)
            decisions = nav.setdefault("decisions", {})
            if entry_id in decisions:
                nav.setdefault("decision_history", []).append(
                    {"entry_id": entry_id, **decisions[entry_id]}
                )
            decisions[entry_id] = {
                "source_sha256": entry["source_sha256"],
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
