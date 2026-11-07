"""Native research tools: bounded evidence access and transactional submissions only."""

from functools import lru_cache

from mcp.server.mcpserver import Image
from mcp.server.mcpserver.exceptions import ToolError

from hrs.processing.research_workflow import ResearchWorkflow
from hrs.research.diagnostics import failure_details


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
            for stage in ("author", "enrich", "verify")
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
    def research_search(
        task_token: str, query: str, book_id: str | None = None, limit: int = 8
    ) -> dict:
        """Search indexed primary publications using the shared hybrid/rerank pipeline."""
        if not 1 <= limit <= 20:
            raise ValueError("Research retrieval limit must be between 1 and 20")
        workflow.prepare_search(task_token)
        try:
            result = evidence().search(query, book_id, semantic=True, limit=limit)
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
                limit=limit,
            )
            raise
        from hrs.research.sources import retrieval_preview

        preview = retrieval_preview(result)
        workflow.record_search(
            task_token,
            result,
            book_id=book_id,
            limit=limit,
            preview_summary={
                key: preview.get(key)
                for key in ("total_hits", "returned_hits", "omitted_hits", "truncated")
            },
        )
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
        result = workflow.related(task_token, entity_id, hops=hops, limit=limit)
        entity_ids = [
            node["id"]
            for node in result["nodes"]
            if node.get("kind") in {"person", "organization", "place", "event"}
        ]
        allowed = evidence().authorized_entity_ids(entity_ids)
        retained = {
            node["id"]
            for node in result["nodes"]
            if node["id"] not in entity_ids or node["id"] in allowed
        }
        result["edges"] = [
            edge
            for edge in result["edges"]
            if edge["source_id"] in retained and edge["target_id"] in retained
        ]
        connected = {
            entity_id,
            *(edge["source_id"] for edge in result["edges"]),
            *(edge["target_id"] for edge in result["edges"]),
        }
        result["nodes"] = [node for node in result["nodes"] if node["id"] in retained & connected]
        return result

    @server.tool()
    @checked
    def research_submit(task_token: str, worker_id: str, result: dict) -> dict:
        """Validate and commit the exact stage schema; native decisions never grant human approval."""
        return workflow.submit(task_token, worker_id, result)

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
