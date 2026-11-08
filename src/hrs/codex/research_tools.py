"""Native research tools: bounded evidence access and transactional submissions only."""

import json
import logging
from functools import lru_cache
from typing import Annotated

from mcp.server.mcpserver import Image
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field, ValidationError

from hrs.processing.errors import EvidenceMismatch
from hrs.processing.research_workflow import ResearchWorkflow
from hrs.research.diagnostics import failure_details, redact_error

logger = logging.getLogger(__name__)


def rejection_diagnostics(error):
    """Keep all validation paths in audit; group repeated fields for a bounded native reply."""
    raw = (
        error.errors(include_input=False, include_url=False)
        if isinstance(error, ValidationError)
        else getattr(error, "validation_issues", None)
        or [{"loc": (), "type": "evidence_mismatch", "msg": str(error)}]
    )
    issues, groups = [], {}
    for issue in raw:
        path = list(issue["loc"])
        message = redact_error(issue["msg"])
        issues.append({"path": path, "type": issue["type"], "message": message})
        pattern = tuple("*" if isinstance(part, int) else part for part in path)
        key = (pattern, issue["type"], message)
        group = groups.setdefault(
            key,
            {
                "path": list(pattern),
                "type": issue["type"],
                "message": message[:400],
                "occurrences": 0,
                "indices": [],
                "omitted_indices": 0,
                "examples": [],
            },
        )
        group["occurrences"] += 1
        group["indices"].append([part for part in path if isinstance(part, int)])
        if len(group["examples"]) < 2:
            group["examples"].append(path)
    summary = {
        "error": "submission_rejected",
        "message": "Correct the failed positions in indices, in order of '*' in each path. Other positions are unchanged. Follow the frozen claim schema.",
        "issue_count": len(issues),
        "issues": list(groups.values()),
        "omitted_issue_groups": 0,
        "omitted_issues": 0,
    }
    while len(json.dumps(summary, ensure_ascii=False)) > 5000:
        largest = max(summary["issues"], key=lambda issue: len(issue["indices"]))
        if len(largest["indices"]) > 2:
            retained = max(2, len(largest["indices"]) // 2)
            largest["omitted_indices"] += len(largest["indices"]) - retained
            del largest["indices"][retained:]
        elif len(summary["issues"]) > 1:
            removed = summary["issues"].pop()
            summary["omitted_issue_groups"] += 1
            summary["omitted_issues"] += removed["occurrences"]
        else:
            break
    return {"issues": issues, "issue_count": len(issues)}, summary


def status_summary(status):
    """Dispatch metadata stays small even when a book contains thousands of article tasks."""
    tasks, policy = status["tasks"], status["policy"]
    active = [task for task in tasks if task["state"] == "leased"]
    failures = [task["last_failure"] for task in tasks if task.get("last_failure")]
    last = max(failures, key=lambda item: item.get("at", 0), default={})
    detail = last.get("details") or status.get("last_runtime_failure") or {}
    return {
        "run_id": status["run_id"],
        "book_id": status["book_id"],
        "state": status["state"],
        "version": status["version"],
        "counts": status["counts"],
        "policy": {
            key: policy[key]
            for key in ("version", "models", "coordinator_model", "reasoning_effort")
            if key in policy
        },
        "ready_by_stage": {
            stage: sum(task["stage"] == stage and task["state"] == "ready" for task in tasks)
            for stage in dict.fromkeys(
                ("author", "enrich", "verify", *(task["stage"] for task in tasks))
            )
        },
        "active": {
            "count": len(active),
            "tasks": [
                {key: task.get(key) for key in ("task_id", "stage", "article_id", "worker_id")}
                for task in active[:4]
            ],
            "omitted": max(0, len(active) - 4),
        },
        "omitted_tasks": len(tasks),
        "retry_at": status.get("next_retry_at", 0),
        "last_error": {
            "code": last.get("code") or detail.get("code"),
            "message": str(detail.get("message") or detail.get("reason") or "")[:512],
            "reason": str(detail.get("reason") or "")[:160],
            "origin": detail.get("origin"),
            "at": last.get("at"),
        },
    }


def register_research_tools(server, store, checked):
    workflow = ResearchWorkflow(store)

    @lru_cache(maxsize=1)
    def evidence():
        from hrs.config import Settings
        from hrs.processing.workspace import resolve_workspace
        from hrs.research.sources import ResearchEvidence
        from hrs.storage.db import engine_for

        settings = Settings.load()
        if resolve_workspace(settings.codex_root) != store.root:
            raise ToolError(
                "MCP workspace differs from the platform; reload its configured root before research"
            )
        return ResearchEvidence(settings, engine_for(settings))

    @lru_cache(maxsize=1)
    def research_workspace():
        from hrs.research.api import Research

        source = evidence()
        return Research(source.settings, source.engine).workspace

    @server.tool()
    @checked
    def research_status(run_id: str) -> dict:
        """Read bounded research progress and ready stages; no task or content approval."""
        return status_summary(workflow.status(run_id))

    @server.tool()
    @checked
    def research_claim(run_id: str, worker_id: str, stage: str) -> dict | None:
        """A native child claims one article task with frozen model, source and submission schema."""
        status = workflow.status(run_id)
        if status["policy"].get("version", 1) >= 2 and not status.get("native_execution_id"):
            raise ToolError(
                "Research v2 awaits its supervised native execution before claiming tasks"
            )
        return workflow.claim(run_id, worker_id, stage=stage, paged=None)

    @server.tool()
    @checked
    def research_packet_text(
        task_token: str, worker_id: str, offset: int = 0, limit: int = 1600
    ) -> dict:
        """Read the frozen complete task packet JSON in bounded windows before other research tools."""
        return workflow.packet_text(task_token, worker_id, offset, limit)

    @server.tool()
    @checked
    def research_source(
        task_token: str, offset: int = 0, limit: int = 1600, article_id: str | None = None
    ) -> dict:
        """Read a bounded frozen source range; advance end until complete before submitting."""
        return workflow.read_source(task_token, offset, min(limit, 2000), article_id=article_id)

    @server.tool()
    @checked
    def research_locate_quotes(
        task_token: str,
        worker_id: str,
        quotes: Annotated[
            list[Annotated[str, Field(min_length=1, max_length=4000)]],
            Field(
                min_length=1,
                max_length=20,
                description="1–20 exact quotations; at most 16000 characters combined.",
            ),
        ],
        article_id: str | None = None,
        offset: Annotated[int, Field(ge=0)] = 0,
        limit: Annotated[int, Field(ge=1, le=20)] = 20,
    ) -> dict:
        """Locate exact quotes in a fully read frozen source; ambiguity stays explicit, no inference."""
        return workflow.locate_quotes(
            task_token,
            worker_id,
            quotes,
            article_id=article_id,
            offset=offset,
            limit=limit,
        )

    @server.tool()
    @checked
    def research_candidate(task_token: str, worker_id: str, observations: dict) -> dict:
        """After full independent source reading freeze article_id/text_sha256/summary observations."""
        return workflow.candidate(task_token, worker_id, observations, paged=None)

    @server.tool()
    @checked
    def research_candidate_text(
        task_token: str, worker_id: str, offset: int = 0, limit: int = 1600
    ) -> dict:
        """Read bounded canonical candidate JSON after source-first observations; advance end until total."""
        return workflow.candidate_text(task_token, worker_id, offset, limit)

    @server.tool()
    @checked
    def research_plan(task_token: str, worker_id: str, questions: list[dict]) -> dict:
        """Persist a bounded research plan before retrieval; the native worker owns its judgments."""
        return workflow.record_plan(task_token, worker_id, questions)

    @server.tool()
    @checked
    def research_target_card(
        task_token: str,
        worker_id: str,
        card_id: str,
        version: int | None = None,
    ) -> dict:
        """Freeze an authorized counterpart and its source versions; verification reads retained targets."""
        return workflow.target_card(
            task_token,
            worker_id,
            card_id,
            version,
            authorize=lambda card: evidence().require_card(card["id"], card["version"]),
        )

    @server.tool()
    @checked
    def research_target_card_text(
        task_token: str,
        worker_id: str,
        card_ref: str,
        offset: int = 0,
        limit: int = 1600,
    ) -> dict:
        """Read the complete frozen counterpart in bounded windows; its source must be independently read."""
        return workflow.target_card_text(task_token, worker_id, card_ref, offset, min(limit, 2000))

    @server.tool()
    @checked
    def research_search(
        task_token: str,
        query: str,
        book_id: str | None = None,
        limit: int = 8,
        book_ids: list[str] | None = None,
    ) -> dict:
        """Search indexed primary publications using the shared hybrid/rerank pipeline."""
        if not 1 <= limit <= 20:
            raise ValueError("Research retrieval limit must be between 1 and 20")
        scope = workflow.prepare_search(task_token, query=query) or {}
        frozen_books = scope.get("book_ids") or []
        if book_ids is not None and (
            not isinstance(book_ids, list)
            or len(book_ids) > 200
            or any(not isinstance(value, str) or not value for value in book_ids)
        ):
            raise ValueError("Research search scope requires at most 200 book IDs")
        if book_id and book_ids:
            raise ValueError("Select book_id or book_ids, not both research scopes")
        selected = sorted(set(book_ids or ([book_id] if book_id else frozen_books)))
        if frozen_books and not set(selected).issubset(frozen_books):
            raise ValueError("Research search cannot leave the frozen question scope")
        options = {"book_ids": selected} if selected and not book_id else {}
        if frozen_books:
            options["trusted_scope"] = True
        try:
            result = evidence().search(query, book_id, semantic=True, limit=limit, **options)
        except Exception as error:  # noqa: BLE001 - retain an actual failed retrieval, then propagate it
            workflow.record_search(
                task_token,
                {
                    "query": query,
                    "backend": "hybrid",
                    "available": False,
                    "hits": [],
                    "metrics": {},
                    "error": {"type": type(error).__name__, "message": str(error)[:2000]},
                },
                book_id=book_id,
                book_ids=selected,
                limit=limit,
            )
            raise
        from hrs.research.sources import retrieval_preview

        preview = retrieval_preview(result)
        receipt = workflow.record_search(
            task_token,
            result,
            book_id=book_id,
            book_ids=selected,
            limit=limit,
            preview_summary={
                key: preview.get(key)
                for key in ("total_hits", "returned_hits", "omitted_hits", "truncated")
            },
        )
        if receipt and receipt.get("receipt_id"):
            preview["receipt_id"] = receipt["receipt_id"]
        return preview

    @server.tool()
    @checked
    def research_read_evidence(
        task_token: str, article_id: str, offset: int = 0, limit: int = 1600
    ) -> dict:
        """Freeze a server-loaded published article, then read/cite its immutable returned identity."""
        workflow.read_source(task_token, 0, 1)
        if not article_id.startswith("source-"):
            snapshot = evidence().freeze_source(article_id)
            indexes = workflow.add_source(task_token, snapshot)
            if len(indexes) != 1:
                raise ValueError("A research evidence request must freeze one complete article")
            article_id = indexes[0]["id"]
        return workflow.read_source(task_token, offset, min(limit, 2000), article_id=article_id)

    @server.tool()
    @checked
    def research_original(task_token: str, page: int, article_id: str | None = None):
        """Inspect original pixels for a frozen research source; cannot reopen or approve OCR."""
        reference = workflow.source_reference(task_token, article_id=article_id, page=page)
        metadata, pixels = store.original_page(reference["document_id"], page)
        if reference.get("image_sha256") and metadata["image_sha256"] != reference["image_sha256"]:
            raise ValueError("Retained source image differs from the research snapshot")
        return Image(data=pixels, format="png")

    @server.tool()
    @checked
    def research_entities(
        query: str = "", book_id: str | None = None, limit: int = 20
    ) -> list[dict]:
        """Find retained entity identities; names alone never authorise an identity merge."""
        if not 1 <= limit <= 50:
            raise ValueError("Entity result limit must be between 1 and 50")
        return evidence().entities(query, book_id=book_id, limit=limit)

    @server.tool()
    @checked
    def research_related(task_token: str, entity_id: str, hops: int = 1, limit: int = 20) -> dict:
        """Explore bounded source-backed identity/event neighbours as leads, not new historical claims."""
        workflow.read_source(task_token, 0, 1)
        evidence().require_entity(entity_id)
        # Preserve task/stage authorization, then validate complete edge provenance
        # through the HTTP facade before truncating discovery metadata for MCP.
        workflow.related(task_token, entity_id, hops=hops, limit=limit)
        from hrs.research.graph import graph_leads

        graph = research_workspace().graph(entity_id, hops=hops, limit=min(limit, 8))
        return graph_leads(graph, requested_limit=limit)

    @server.tool()
    @checked
    def research_submit(task_token: str, worker_id: str, result: dict) -> dict:
        """Validate and commit the exact stage schema; native decisions never grant human approval."""
        try:
            return workflow.submit(task_token, worker_id, result)
        except (ValidationError, EvidenceMismatch) as error:
            diagnostics, reply = rejection_diagnostics(error)
            try:
                receipt = workflow.record_submission_rejection(
                    task_token, worker_id, result, diagnostics
                )
                if receipt.get("recorded"):
                    reply["rejection_ref"] = receipt["rejection_ref"]
            except Exception:  # noqa: BLE001 - audit failure cannot hide the original rejection or approve content
                logger.debug("Could not retain rejected research proposal", exc_info=True)
            raise ToolError(json.dumps(reply, ensure_ascii=False, separators=(",", ":"))) from error

    @server.tool()
    @checked
    def research_fail(
        task_token: str, worker_id: str, code: str, details: dict | None = None
    ) -> dict:
        """Persist an actual failure; details should include reason/message and actual scope/retry_after diagnostics."""
        diagnostics = failure_details(details)
        if not diagnostics or not (diagnostics.get("message") or diagnostics.get("reason")):
            raise ValueError(
                "An actual technical failure requires a concrete details.reason or details.message"
            )
        return workflow.fail(task_token, worker_id, code, details=diagnostics)

    return evidence
