"""New study tools must not disable retained JSON-only MCP reading contracts."""

import asyncio
import json

from mcp import Client

from hrs.processing.hashing import digest
from tests.integration.codex.test_mcp import server_parameters, value


def test_legacy_stdio_retains_document_reads_and_rejects_new_studies(tmp_path):
    identity = "c" * 24
    directory = tmp_path / "documents" / identity
    directory.mkdir(parents=True)
    document = {
        "id": identity,
        "title": "Retained legacy publication",
        "state": "published",
        "created_at": 1,
        "pages": [],
        "tasks": [],
        "draft": {"text": "人工修订史料。[^1]\n\n[^1]: 原刊注释。"},
        "published_versions": [{"revision": 1, "physical_pages": [9]}],
    }
    original_source = directory / "source.bin"
    original_source.write_bytes(b"synthetic retained legacy source")
    document["source"] = {"path": "source.bin", "sha256": digest(original_source.read_bytes())}
    document["organization"] = {
        "task_id": "retained-organization",
        "articles": [{"id": "retained-article", "pages": []}],
    }
    organization_result = {"articles": document["organization"]["articles"]}
    document["tasks"] = [
        {
            "task_id": "retained-organization",
            "stage": "organization",
            "state": "completed",
            "page": 0,
            "result": organization_result,
            "receipt": {"result_sha256": digest(organization_result)},
        }
    ]
    document["publication"] = {
        "markdown_path": str(directory / "document.md"),
        "markdown_sha256": digest(document["draft"]["text"]),
        "organization_sha256": digest(document["organization"]),
    }
    (directory / "publication.json").write_text(
        json.dumps(document["publication"]), encoding="utf8"
    )
    legacy = directory / "document.json"
    legacy.write_text(json.dumps(document), encoding="utf8")
    original = legacy.read_bytes()
    exported = directory / "document.md"
    exported.write_text(document["draft"]["text"], encoding="utf8", newline="\n")
    exported_bytes = exported.read_bytes()

    async def scenario():
        async with Client(
            server_parameters(tmp_path), mode="legacy", read_timeout_seconds=15
        ) as client:
            instructions = await client.call_tool("workflow_instructions")
            assert not instructions.is_error
            assert value(await client.call_tool("list_documents"))[0]["id"] == identity
            retained = value(
                await client.call_tool(
                    "document_status", {"document_id": identity, "include_content": True}
                )
            )
            assert retained["draft"] == document["draft"]
            assert retained["published_versions"] == document["published_versions"]
            assert not (
                await client.call_tool("document_status", {"document_id": identity})
            ).is_error
            # The published branch validates and returns the retained Markdown export; no rewrite.
            published = value(await client.call_tool("publish_document", {"document_id": identity}))
            assert published == document["publication"]
            unavailable = await client.call_tool(
                "study_analysis_status", {"analysis_id": "missing"}
            )
            assert unavailable.is_error
            assert any(
                "migrate-ledger" in block.text
                for block in unavailable.content
                if block.type == "text"
            )

    asyncio.run(scenario())
    assert legacy.read_bytes() == original
    assert exported.read_bytes() == exported_bytes
    assert not (tmp_path / "ledger.sqlite3").exists()
    assert not legacy.with_name("document.legacy.json").exists()
