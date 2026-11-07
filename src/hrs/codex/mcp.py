"""Local MCP tools; models run in Codex subagents, never inside this server."""

import json
from collections import Counter
from functools import wraps
from importlib.resources import files
from pathlib import Path

from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import TextContent

from hrs.processing.application import prepare as prepare
from hrs.processing.contracts import FailureCode
from hrs.processing.details import original_detail as render_original_detail
from hrs.processing.store import Store


def contract_errors(function):
    """Expose actionable validation failures, without masking them as model outages."""

    @wraps(function)
    def checked(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (ValueError, StopIteration) as error:
            raise ToolError(str(error) or "Unknown document task") from error

    return checked


def compact_document(doc):
    """Bound coordinator context without hiding document-wide progress or retained evidence."""
    pages, tasks = doc["pages"], doc["tasks"]
    doc["page_count"] = len(pages)
    doc["page_counts"] = dict(Counter(p["state"] for p in pages))
    doc["all_pages_approved"] = all(p["state"] == "approved" for p in pages)
    selected = pages if len(pages) <= 20 else pages[:8] + pages[-12:]
    doc["page_list_truncated"] = len(selected) < len(pages)
    doc["pages"] = [
        {
            **{k: p[k] for k in ("page", "state", "route", "page_kind") if k in p},
            **{
                field: {
                    k: v for k, v in p[field].items() if k in {"sha256", "task_id", "approval_type"}
                }
                for field in ("ocr", "candidate", "approved")
                if p.get(field)
            },
        }
        for p in selected
    ]
    counts = Counter((t["stage"], t["state"]) for t in tasks)
    doc["task_counts"] = {
        stage: {state: count for (s, state), count in counts.items() if s == stage}
        for stage, _ in counts
    }
    seen = Counter()
    selected_tasks = []
    for task in reversed(tasks):
        bucket = (task["stage"], task["state"])
        if task["state"] != "leased" and seen[bucket] >= 8:
            continue
        seen[bucket] += 1
        selected_tasks.append(
            {k: task[k] for k in ("task_id", "stage", "page", "state", "retry_after") if k in task}
        )
    doc["tasks"] = list(reversed(selected_tasks))
    doc["task_list_truncated"] = len(selected_tasks) < len(tasks)
    nav = doc.get("navigation") or {}
    doc["navigation"] = {
        k: nav[k] for k in ("state", "version", "sha256", "coverage_complete") if k in nav
    }
    doc["navigation"]["entry_counts"] = dict(Counter(e["status"] for e in nav.get("entries", [])))
    if doc.get("organization"):
        doc["organization"] = {
            "task_id": doc["organization"]["task_id"],
            "article_count": len(doc["organization"]["articles"]),
        }
    doc.pop("published_versions", None)
    return doc


def create_server(root: Path) -> MCPServer:
    store = Store(root)
    server = MCPServer(
        "Historical Research",
        instructions=(
            "Use native Codex subagents for OCR, review, correction verification and organization. "
            "Runtime sessions use dispatch_work and open_task_packet, which includes the stage contract. "
            "Direct clients read workflow_instructions first. Every reviewer inspects original pixels before judging. "
            "Publish only after every page and organization task passes. Verdicts are machine decisions."
        ),
    )

    @server.tool()
    @contract_errors
    def workflow_instructions() -> str:
        """Read the complete Codex subagent workflow and submission contracts."""
        return files("hrs.codex").joinpath("workflow.md").read_text(encoding="utf-8")

    @server.tool()
    @contract_errors
    def workspace_status() -> dict:
        """Identify the exact workspace shared by these tools, the executor and the workbench."""
        from hrs.processing.workspace import identity

        return identity(store)

    @server.tool()
    @contract_errors
    def open_workbench(
        document_id: str | None = None, page: int | None = None, entry_id: str | None = None
    ) -> dict:
        """Open this workspace's reading platform or SQL-free fallback. Select a human content page or directory entry."""
        from hrs.codex.workbench_lifecycle import ensure_workbench

        return ensure_workbench(store, document_id=document_id, page=page, entry_id=entry_id)

    @server.tool()
    @contract_errors
    def process_document(source: str, title: str = "") -> dict:
        """Import and enqueue a document; the durable executor delegates to native Codex subagents."""
        document = prepare(store, source, title)
        request = store.request_run(document["id"])
        return {
            "document_id": document["id"],
            "run_request": request,
            "workbench": open_workbench(document["id"]),
        }

    @server.tool()
    @contract_errors
    def resume_document(document_id: str) -> dict:
        """Explicitly retry technical run failures; human drafts and content issues remain protected."""
        request = store.request_run(document_id, resume=True)
        return {
            "document_id": document_id,
            "run_request": request,
            "workbench": open_workbench(document_id),
        }

    @server.tool()
    @contract_errors
    def pause_document(document_id: str) -> dict:
        """Pause owned execution without deleting checkpoints; explicit resume is required."""
        return store.pause_document(document_id)

    @server.tool()
    @contract_errors
    def scheduling_plan(document_id: str) -> dict:
        """Measured stage capacity and bounded batches; pending human pages never stop other pages."""
        return store.scheduling_snapshot(document_id)

    @server.tool()
    @contract_errors
    def wait_for_work(document_id: str, seconds: int = 15) -> dict:
        """Bounded cooldown wait, then return refreshed stage capacity (no model inference)."""
        import time

        time.sleep(max(0, min(seconds, 30)))
        return store.scheduling_snapshot(document_id)

    @server.tool()
    @contract_errors
    def report_task_failure(
        document_id: str,
        task_id: str,
        lease_token: str,
        code: FailureCode,
    ) -> dict:
        """Retain technical failures separately from source uncertainty and reduce admissions."""
        scope = "account" if code in {"authentication", "quota_exhausted"} else "model"
        return store.report_task_failure(
            document_id, task_id, lease_token, code, details={"scope": scope}
        )

    @server.tool()
    @contract_errors
    def navigation_status(document_id: str, start: int = 0, limit: int = 20) -> dict:
        """Read directory navigation candidates, printed/physical page links and conflicts."""
        if start < 0 or not 1 <= limit <= 40:
            raise ValueError("Navigation cursor needs start >= 0 and limit between 1 and 40")
        nav = store.status(document_id).get("navigation") or {
            "state": "not_detected",
            "entries": [],
        }
        entries = nav.get("entries", [])
        return {
            **{k: nav[k] for k in ("state", "version", "sha256", "coverage_complete") if k in nav},
            "entries": entries[start : start + limit],
            "total": len(entries),
            "next_start": start + limit if start + limit < len(entries) else None,
        }

    @server.tool()
    @contract_errors
    def fallback_page(
        document_id: str, page: int, reason: str, new_evidence: str | None = None
    ) -> dict:
        """Bounded content fallback to full-page OCR; keep previous candidates and reasons."""
        return store.fallback_page(document_id, page, reason, new_evidence=new_evidence)

    @server.tool()
    @contract_errors
    def begin_revision(document_id: str, reason: str, pages: list[int] | None = None) -> dict:
        """Explicitly reopen published machine pages, preserving the active published version and human decisions."""
        result = store.begin_revision(document_id, reason, pages=pages)
        store.request_run(document_id)
        return {"revision": result, "workbench": open_workbench(document_id)}

    @server.tool()
    @contract_errors
    def prepare_document(source: str, title: str = "") -> dict:
        """Retain a local PDF/image, render physical pages, and create recoverable OCR tasks."""
        return prepare(store, source, title)

    @server.tool()
    @contract_errors
    def list_documents() -> list[dict]:
        """List retained local documents and their progress."""
        return store.list_documents()

    @server.tool()
    @contract_errors
    def document_status(document_id: str, include_content: bool = False) -> dict:
        """Read compact progress by default; explicitly request full retained content when needed."""
        doc = store.status(document_id)
        if include_content:
            return doc
        return compact_document(doc)

    @server.tool()
    @contract_errors
    def approved_page_text(document_id: str, page: int) -> dict:
        """Read one approved page with its hash; organization workers can inspect large documents page by page."""
        return store.approved_page_text(document_id, page)

    @server.tool()
    @contract_errors
    def approved_pages_text(
        document_id: str, task_id: str, lease_token: str, after_page: int = 0, limit: int = 8
    ) -> dict:
        """Read complete frozen approved pages in bounded batches, retaining footnotes and hashes."""
        return store.task_inspection.approved_pages_text(
            document_id, task_id, lease_token, after_page=after_page, limit=limit
        )

    @server.tool()
    @contract_errors
    def approved_page_catalog(
        document_id: str, task_id: str, lease_token: str, after_page: int = 0, limit: int = 20
    ) -> dict:
        """Page through an organization's/navigation's FULL frozen approved-page catalog; no text inference."""
        import time

        task = store.get_task(document_id, task_id)
        if (
            task["stage"] not in {"organization", "navigation"}
            or task["state"] != "leased"
            or task["claim"]["lease_token"] != lease_token
            or task["claim"]["expires_at"] <= time.time()
        ):
            raise ValueError("Approved catalog requires this active organization/navigation lease")
        if after_page < 0 or not 1 <= limit <= 40:
            raise ValueError("Catalog needs after_page >= 0 and limit between 1 and 40")
        if task["inputs"].get("navigation_mode") == "verify_links" and not task.get(
            "navigation_original_reading"
        ):
            raise ValueError("Freeze navigation original observations before reading the catalog")
        rows = [p for p in task["inputs"]["pages"] if p["page"] > after_page]
        selected = rows[:limit]
        return {
            "pages": [{k: v for k, v in p.items() if k != "markdown"} for p in selected],
            "total": len(task["inputs"]["pages"]),
            "next_after_page": selected[-1]["page"] if len(rows) > len(selected) else None,
        }

    @server.tool()
    @contract_errors
    def claim_task(document_id: str, stage: str, worker_id: str) -> dict | None:
        """Lease one ocr/review/verify/organization task to a Codex subagent. Null means none ready."""
        task = store.claim_task(document_id, stage, worker_id, source_first=True, scheduled=True)
        if task and stage == "organization":
            for page in task["inputs"]["pages"]:
                page.pop("markdown", None)
            task["content_access"] = (
                "Use approved_page_text for each page; hashes bind the entire collection."
            )
        if task and stage in {"organization", "navigation"}:
            task["snapshot_page_count"] = len(task["inputs"]["pages"])
            task["inputs"]["pages"] = (
                [p for p in task["inputs"]["pages"] if p.get("kind") == "toc"]
                if stage == "navigation" and task["inputs"].get("navigation_mode") != "verify_links"
                else task["inputs"]["pages"][:20]
            )
            task["content_access"] = (
                "Use approved_page_catalog with this task/lease to enumerate ALL frozen pages, then approved_page_text. The displayed page list may be partial."
            )
            task["inputs"].pop("previous_entries", None)
        return source_packet(task)

    def source_packet(task):
        if task:
            task.pop("verification_inputs", None)
            task.pop("navigation_reading_history", None)
        if (
            task
            and task.get("inputs", {}).get("navigation_mode") == "verify_links"
            and not task.get("navigation_original_reading")
        ):
            task["inputs"].pop("entries", None)
            task["content_access"] = (
                "Inspect every listed original_image with this task/lease, then call "
                "navigation_candidates to freeze original_readings before candidate exposure."
            )
        if task and task.get("source_first_required") and not task.get("source_reading"):
            for key in ("markdown", "original_markdown", "corrections"):
                task["inputs"].pop(key, None)
            if "extraction_plan" in task["inputs"]:
                task["inputs"]["extraction_plan"].pop("native_markdown", None)
            task["content_access"] = (
                "Open original_image with task_id and lease_token, inspect original pixels, then "
                "call review_candidate with image_receipt, image_sha256, observations and unresolved. "
                "The candidate is withheld until those source observations are frozen."
            )
        if task:
            for key in ("source_reading_history", "image_accesses", "technical_failures"):
                task.pop(key, None)
            if task.get("source_reading"):
                task["source_reading"] = {"sha256": task["source_reading"]["sha256"]}
                plan = task["inputs"].get("extraction_plan", {})
                plan.pop("native_markdown", None)
                if len(task["inputs"].get("markdown", "")) > 2000:
                    task["candidate_codepoints"] = len(task["inputs"].pop("markdown"))
                    task["content_access"] = (
                        "Read the COMPLETE candidate through candidate_text in bounded ranges before submitting a verdict."
                    )
        return task

    @server.tool()
    @contract_errors
    def navigation_candidates(
        document_id: str,
        task_id: str,
        lease_token: str,
        original_readings: list[dict],
    ) -> dict:
        """Freeze original-first observations, then expose this bounded link-verification set."""
        import time

        from hrs.processing.navigation_verification import freeze_link_observations

        with store.context.verified_task(document_id, task_id) as doc:
            task = next(t for t in doc["tasks"] if t["task_id"] == task_id)
            if (
                task["inputs"].get("navigation_mode") != "verify_links"
                or task["state"] != "leased"
                or task["claim"]["lease_token"] != lease_token
                or task["claim"]["expires_at"] <= time.time()
            ):
                raise ValueError("Navigation candidates require this active verification lease")
            result = freeze_link_observations(doc, task, original_readings)
            store.checkpoints.write(doc)
            return result

    @server.tool()
    @contract_errors
    def dispatch_work(document_id: str, active_session_tokens: list[str] | None = None) -> dict:
        """Reserve concrete tasks and workspace capacity BEFORE spawning the assigned native roles."""
        return store.dispatch.reserve(
            document_id, active_session_tokens=active_session_tokens or ()
        )

    @server.tool(structured_output=False)
    @contract_errors
    def open_task_packet(session_token: str, worker_id: str, wait_seconds: int = 25) -> list:
        """Bind a native worker; return stage rules, lease, policy and original pixels in one packet."""
        import time

        from hrs.codex.runtime_policy import worker_instructions

        deadline = time.monotonic() + max(0, min(wait_seconds, 30))
        task, metadata, data = store.dispatch.open(session_token, worker_id)
        while task.get("state") == "wait" and time.monotonic() < deadline:
            sequence = task["sequence"]
            while store.checkpoints.ledger.sequence() <= sequence and time.monotonic() < deadline:
                time.sleep(0.2)
            task, metadata, data = store.dispatch.open(session_token, worker_id)
        if task.get("state") in {"wait", "exited"}:
            return [TextContent(type="text", text=json.dumps(task))]
        stage = task["stage"]
        if stage in {"organization", "navigation"}:
            for page in task["inputs"]["pages"]:
                page.pop("markdown", None)
            task["snapshot_page_count"] = len(task["inputs"]["pages"])
            task["inputs"]["pages"] = task["inputs"]["pages"][:20]
        packet = {
            "instructions": worker_instructions(
                stage, task.get("inputs", {}).get("navigation_mode")
            ),
            "task": task if task.get("state") == "preflight" else source_packet(task),
            "original": metadata,
        }
        if stage == "organization":
            packet["approved_pages"] = store.task_inspection.approved_pages_text(
                task["inputs"]["document_id"], task["task_id"], task["lease_token"]
            )
        if task.get("state") == "preflight":
            packet["instructions"] += (
                "\nThis is a source-only preflight: no candidate or task lease exists yet. Inspect original pixels now; call freeze_observations with session_token, worker_id and original_reading. If it returns wait, call open_task_packet; your observations stay frozen. Never invent a task ID or input hash."
            )
        elif task.get("source_reading"):
            packet["candidate"] = store.candidate_text(
                task["document_id"] if "document_id" in task else task["inputs"]["document_id"],
                task["task_id"],
                task["lease_token"],
                limit=4000,
            )
            packet["instructions"] += (
                "\nYour preflight observations are already frozen and bound to this task. Compare the supplied candidate; read any remaining ranges. Do not freeze observations again."
            )
        content = [TextContent(type="text", text=json.dumps(packet, ensure_ascii=False))]
        if data is not None:
            content.append(Image(data=data, format="png").to_image_content())
        return content

    @server.tool()
    @contract_errors
    def freeze_observations(session_token: str, worker_id: str, original_reading: dict) -> dict:
        """Freeze independent source observations; return the first complete bounded candidate range."""
        return store.dispatch.freeze(session_token, worker_id, original_reading)

    @server.tool()
    @contract_errors
    def submit_packet(session_token: str, worker_id: str, result: dict) -> dict:
        """Atomically save result, follow-up tasks, native session outcome and a minimal receipt."""
        return store.dispatch.submit(session_token, worker_id, result)

    @server.tool()
    @contract_errors
    def finish_worker(
        session_token: str, worker_id: str, failure_code: FailureCode | None = None
    ) -> dict:
        """Release an idle native worker; active pages must finish or report a technical failure."""
        if failure_code is not None:
            scope = "account" if failure_code in {"authentication", "quota_exhausted"} else "model"
            return store.dispatch.fail_runtime(
                session_token, worker_id, failure_code, details={"scope": scope}
            )
        return store.dispatch.close(session_token, worker_id)

    @server.tool()
    @contract_errors
    def wait_for_changes(after_sequence: int, seconds: int = 30) -> dict:
        """Wait on the durable change feed without asking a model to poll or plan."""
        import time

        deadline = time.monotonic() + max(0, min(seconds, 30))
        while store.checkpoints.ledger.sequence() <= after_sequence and time.monotonic() < deadline:
            time.sleep(0.25)
        return {"sequence": store.checkpoints.ledger.sequence()}

    @server.tool(structured_output=False)
    @contract_errors
    def original_details(document_id: str, page: int, boxes: list[list[float]]) -> list:
        """Inspect up to six bounded original crops in one request; each retains its own hash receipt."""
        if not 1 <= len(boxes) <= 6:
            raise ValueError("Request one to six original crop boxes")
        content = []
        for box in boxes:
            metadata, data = render_original_detail(store, document_id, page, box)
            content.extend(
                [
                    TextContent(type="text", text=json.dumps(metadata)),
                    Image(data=data, format="png").to_image_content(),
                ]
            )
        return content

    @server.tool()
    @contract_errors
    def review_candidate(
        document_id: str, task_id: str, lease_token: str, original_reading: dict
    ) -> dict:
        """Freeze original-image observations and then reveal the review/verification candidate."""
        return source_packet(
            store.review_candidate(document_id, task_id, lease_token, original_reading)
        )

    @server.tool()
    @contract_errors
    def candidate_text(
        document_id: str, task_id: str, lease_token: str, start: int = 0, limit: int = 2000
    ) -> dict:
        """Read candidate Markdown in bounded Unicode-codepoint ranges AFTER original observations are frozen."""
        return store.candidate_text(document_id, task_id, lease_token, start=start, limit=limit)

    @server.tool()
    @contract_errors
    def task_details(document_id: str, task_id: str) -> dict:
        """Read retained task inputs and its evidence hashes."""
        return source_packet(store.get_task(document_id, task_id))

    @server.tool(structured_output=False)
    @contract_errors
    def original_image(
        document_id: str, page: int, task_id: str | None = None, lease_token: str | None = None
    ) -> list:
        """Open the retained original page as image pixels, with its SHA-256 evidence."""
        metadata, data = store.original_page(
            document_id, page, task_id=task_id, lease_token=lease_token
        )
        return [
            TextContent(type="text", text=json.dumps(metadata)),
            Image(data=data, format="png").to_image_content(),
        ]

    @server.tool(structured_output=False)
    @contract_errors
    def original_detail(document_id: str, page: int, box: list[float]) -> list:
        """Inspect a normalized source-page crop: PDFs rerender at 432 dpi; rasters use native pixels."""
        metadata, data = render_original_detail(store, document_id, page, box)
        return [
            TextContent(type="text", text=json.dumps(metadata)),
            Image(data=data, format="png").to_image_content(),
        ]

    @server.tool()
    @contract_errors
    def submit_task(document_id: str, task_id: str, lease_token: str, result: dict) -> dict:
        """Commit a Codex machine decision bound to claimed input_sha256; retain originals and drafts."""
        return store.submit_task(document_id, task_id, lease_token, result)

    @server.tool()
    @contract_errors
    def human_review_queue() -> list[dict]:
        """Read unresolved pages awaiting explicit human review; cannot record human decisions."""
        return store.human_queue()

    @server.tool()
    @contract_errors
    def request_human_review(
        document_id: str, page: int, issue: str, evidence: dict, worker_id: str
    ) -> dict:
        """Route an explicit original-image audit dispute to humans, retaining versions and machine provenance."""
        result = store.request_human_review(document_id, page, issue, evidence, worker_id)
        return {"handoff": result, "workbench": open_workbench(document_id, page)}

    @server.tool()
    @contract_errors
    def retry_task(document_id: str, task_id: str) -> dict:
        """Reopen pending work; failed verify restarts review, retaining all historical evidence."""
        return store.retry_task(document_id, task_id)

    @server.tool()
    @contract_errors
    def publish_document(document_id: str) -> dict:
        """Validate all evidence and publish reviewed Markdown and article mapping into the local library."""
        return store.publish(document_id)

    @server.tool()
    @contract_errors
    def search_library(query: str, limit: int = 20, book_id: str | None = None,
                       semantic: bool = True) -> dict:
        """Shared platform evidence search; unavailable infrastructure is explicitly labelled."""
        try:
            return research_evidence().search(query, book_id, semantic=semantic, limit=limit)
        except Exception as error:  # noqa: BLE001 - direct standalone clients keep labelled literal lookup
            if book_id is not None:
                raise ToolError("Platform search is unavailable; book-scoped search requires its index") from error
            return {"query": query, "backend": "local_literal_fallback", "available": False,
                    "hits": store.search(query, limit=limit),
                    "warning": "Hybrid retrieval is unavailable; these are local substring matches only",
                    "error_type": type(error).__name__}

    from hrs.codex.research_tools import register_research_tools

    research_evidence = register_research_tools(server, store, contract_errors)

    return server
