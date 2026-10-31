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
    def prepare_document(source: str, title: str = "") -> dict:
        """Retain a local PDF/image, render physical pages, and create recoverable OCR tasks."""
        return prepare(store, source, title)

    @server.tool()
    def list_documents() -> list[dict]:
        """List retained local documents and their progress."""
        return store.list_documents()

    @server.tool()
    def document_status(document_id: str) -> dict:
        """Read pages, task states and evidence; clean pages remain readable before publication."""
        return store.status(document_id)

    @server.tool()
    def claim_task(document_id: str, stage: str, worker_id: str) -> dict | None:
        """Lease one ocr/review/verify/organization task to a Codex subagent. Null means none ready."""
        return store.claim_task(document_id, stage, worker_id)

    @server.tool()
    def task_details(document_id: str, task_id: str) -> dict:
        """Read retained task inputs and its evidence hashes."""
        return store.get_task(document_id, task_id)

    @server.tool(structured_output=False)
    def original_image(document_id: str, page: int) -> list:
        """Open the retained original page as image pixels, with its SHA-256 evidence."""
        document = store.status(document_id)
        row = next((row for row in document["pages"] if row["page"] == page), None)
        if row is None:
            raise ValueError("Unknown physical page")
        path = (Path(document["directory"]) / row["image"]).resolve()
        if not path.is_relative_to(Path(document["directory"]).resolve()):
            raise ValueError("Original image path escapes document directory")
        from hashlib import sha256

        data = path.read_bytes()
        actual = sha256(data).hexdigest()
        if actual != row["image_sha256"]:
            raise ValueError("Original image evidence hash mismatch")
        return [
            TextContent(type="text", text=json.dumps({"page": page, "image_sha256": actual})),
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
