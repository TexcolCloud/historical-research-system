"""Local MCP tools; models run in Codex subagents, never inside this server."""

import json
from pathlib import Path

from mcp.server.mcpserver import Image, MCPServer
from mcp.types import TextContent

from hrs_codex.details import original_detail as render_original_detail
from hrs_codex.render import render_document
from hrs_codex.store import Store


def prepare(store: Store, source: str, title: str = "") -> dict:
    from hashlib import file_digest

    path = Path(source).expanduser().resolve(strict=True)
    with path.open("rb") as stream:
        document_id = file_digest(stream, "sha256").hexdigest()[:24]
    manifest = render_document(path, store.root / "documents" / document_id)
    return store.register_manifest(manifest, title=title)


def create_server(root: Path) -> MCPServer:
    store = Store(root)
    server = MCPServer(
        "Historical Research",
        instructions=(
            "Use native Codex subagents for OCR, review, correction verification and organization. "
            "Read workflow_instructions first. Every reviewer opens original_image before judging. "
            "Publish only after every page and organization task passes. Verdicts are machine decisions."
        ),
    )

    @server.tool()
    def workflow_instructions() -> str:
        """Read the complete Codex subagent workflow and submission contracts."""
        return Path(__file__).with_name("workflow.md").read_text(encoding="utf-8")

    @server.tool()
    def workspace_status() -> dict:
        """Identify the exact workspace shared by these tools, the executor and the workbench."""
        from hrs_codex.workspace import identity

        return identity(store)

    @server.tool()
    def open_workbench() -> dict:
        """Start or reuse this workspace's loopback dashboard and native Codex executor."""
        from hrs_codex.workspace import ensure_workbench

        return ensure_workbench(store)

    @server.tool()
    def process_document(source: str, title: str = "") -> dict:
        """Import and enqueue a document; the durable executor delegates to native Codex subagents."""
        document = prepare(store, source, title)
        request = store.request_run(document["id"])
        return {
            "document_id": document["id"],
            "run_request": request,
            "workbench": open_workbench(),
        }

    @server.tool()
    def resume_document(document_id: str) -> dict:
        """Explicitly retry technical run failures; human drafts and content issues remain protected."""
        request = store.request_run(document_id, resume=True)
        return {"document_id": document_id, "run_request": request, "workbench": open_workbench()}

    @server.tool()
    def fallback_page(
        document_id: str, page: int, reason: str, new_evidence: str | None = None
    ) -> dict:
        """Bounded content fallback to full-page OCR; keep previous candidates and reasons."""
        return store.fallback_page(document_id, page, reason, new_evidence=new_evidence)

    @server.tool()
    def begin_revision(document_id: str, reason: str, pages: list[int] | None = None) -> dict:
        """Explicitly reopen published machine pages, preserving the active published version and human decisions."""
        result = store.begin_revision(document_id, reason, pages=pages)
        store.request_run(document_id)
        return {"revision": result, "workbench": open_workbench()}

    @server.tool()
    def prepare_document(source: str, title: str = "") -> dict:
        """Retain a local PDF/image, render physical pages, and create recoverable OCR tasks."""
        return prepare(store, source, title)

    @server.tool()
    def list_documents() -> list[dict]:
        """List retained local documents and their progress."""
        return store.list_documents()

    @server.tool()
    def document_status(document_id: str, include_content: bool = False) -> dict:
        """Read compact progress by default; explicitly request full retained content when needed."""
        doc = store.status(document_id)
        if include_content:
            return doc
        for page in doc["pages"]:
            for field in ("ocr", "candidate", "approved"):
                if field in page:
                    page[field].pop("markdown", None)
            page.pop("fallback_history", None)
            if "extraction_plan" in page:
                page["extraction_plan"].pop("probe_evidence", None)
                page["extraction_plan"].pop("native_markdown", None)
        doc["tasks"] = [
            {k: v for k, v in task.items() if k not in {"inputs", "result"}}
            for task in doc["tasks"]
        ]
        doc.pop("published_versions", None)
        return doc

    @server.tool()
    def approved_page_text(document_id: str, page: int) -> dict:
        """Read one approved page with its hash; organization workers can inspect large documents page by page."""
        doc = store.status(document_id)
        if type(page) is not int or not 1 <= page <= len(doc["pages"]):
            raise ValueError("Unknown physical page")
        row = doc["pages"][page - 1]
        if row["state"] != "approved":
            raise ValueError("Page has unresolved content")
        store._verify_evidence(doc)
        return {
            "document_id": document_id,
            "page": page,
            "image_sha256": row["image_sha256"],
            "markdown": row["approved"]["markdown"],
            "markdown_sha256": row["approved"]["sha256"],
        }

    @server.tool()
    def claim_task(document_id: str, stage: str, worker_id: str) -> dict | None:
        """Lease one ocr/review/verify/organization task to a Codex subagent. Null means none ready."""
        task = store.claim_task(document_id, stage, worker_id)
        if task and stage == "organization":
            for page in task["inputs"]["pages"]:
                page.pop("markdown", None)
            task["content_access"] = (
                "Use approved_page_text for each page; hashes bind the entire collection."
            )
        return task

    @server.tool()
    def task_details(document_id: str, task_id: str) -> dict:
        """Read retained task inputs and its evidence hashes."""
        return store.get_task(document_id, task_id)

    @server.tool(structured_output=False)
    def original_image(document_id: str, page: int) -> list:
        """Open the retained original page as image pixels, with its SHA-256 evidence."""
        metadata, data = store.original_page(document_id, page)
        return [
            TextContent(type="text", text=json.dumps(metadata)),
            Image(data=data, format="png").to_image_content(),
        ]

    @server.tool(structured_output=False)
    def original_detail(document_id: str, page: int, box: list[float]) -> list:
        """Inspect a normalized source-page crop: PDFs rerender at 432 dpi; rasters use native pixels."""
        metadata, data = render_original_detail(store, document_id, page, box)
        return [
            TextContent(type="text", text=json.dumps(metadata)),
            Image(data=data, format="png").to_image_content(),
        ]

    @server.tool()
    def submit_task(document_id: str, task_id: str, lease_token: str, result: dict) -> dict:
        """Commit a Codex machine decision bound to claimed input_sha256; retain originals and drafts."""
        return store.submit_task(document_id, task_id, lease_token, result)

    @server.tool()
    def human_review_queue() -> list[dict]:
        """Read unresolved pages awaiting explicit human review; cannot record human decisions."""
        return store.human_queue()

    @server.tool()
    def retry_task(document_id: str, task_id: str) -> dict:
        """Reopen pending work; failed verify restarts review, retaining all historical evidence."""
        return store.retry_task(document_id, task_id)

    @server.tool()
    def publish_document(document_id: str) -> dict:
        """Validate all evidence and publish reviewed Markdown and article mapping into the local library."""
        return store.publish(document_id)

    @server.tool()
    def search_library(query: str, limit: int = 20) -> list[dict]:
        """Search published local text with page provenance; only reviewed documents are indexed."""
        return store.search(query, limit=limit)

    return server
