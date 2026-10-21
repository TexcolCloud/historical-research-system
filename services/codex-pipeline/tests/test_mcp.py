"""Exercise the public MCP contracts through a real local stdio subprocess."""

import asyncio
import base64
import hashlib
import io
import json
import sys
from pathlib import Path

from mcp import Client, StdioServerParameters
from PIL import Image
from pypdf import PdfWriter


def server_parameters(root):
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "hrs_codex.cli", "--root", str(root), "mcp"],
        cwd=Path(__file__).resolve().parents[1],
        env={"PYTHONUTF8": "1"},
    )


def value(result):
    assert not result.is_error, result.content
    content = result.structured_content
    if content is None:
        content = json.loads(next(block.text for block in result.content if block.type == "text"))
    return content["result"] if isinstance(content, dict) and set(content) == {"result"} else content


async def submit(client, doc_id, task, result):
    return value(
        await client.call_tool(
            "submit_task",
            {
                "document_id": doc_id,
                "task_id": task["task_id"],
                "lease_token": task["lease_token"],
                "result": {"input_sha256": task["input_sha256"], **result},
            },
        )
    )


async def claim(client, doc_id, stage, worker):
    return value(
        await client.call_tool(
            "claim_task", {"document_id": doc_id, "stage": stage, "worker_id": worker}
        )
    )


def test_stdio_tools_original_pixels_correction_and_publication(tmp_path):
    source = tmp_path / "scan.png"
    Image.new("RGB", (40, 50), "white").save(source)
    root = tmp_path / "library"

    async def scenario():
        async with Client(server_parameters(root), mode="legacy", read_timeout_seconds=15) as client:
            listing = await client.list_tools()
            names = {tool.name for tool in listing.tools}
            assert {
                "workflow_instructions", "prepare_document", "claim_task", "submit_task",
                "original_image", "publish_document", "search_library", "retry_task",
            } <= names
            instructions = await client.call_tool("workflow_instructions")
            assert not instructions.is_error
            assert "subagent" in instructions.content[0].text.lower()
            document = value(
                await client.call_tool("prepare_document", {"source": str(source), "title": "MCP fixture"})
            )
            doc_id = document["id"]
            assert document["source"]["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
            assert value(await client.call_tool("list_documents"))[0]["id"] == doc_id
            image_result = await client.call_tool("original_image", {"document_id": doc_id, "page": 1})
            assert not image_result.is_error
            evidence = json.loads(next(block.text for block in image_result.content if block.type == "text"))
            image = next(block for block in image_result.content if block.type == "image")
            pixels = base64.b64decode(image.data)
            assert hashlib.sha256(pixels).hexdigest() == evidence["image_sha256"]
            assert evidence["image_sha256"] == document["pages"][0]["image_sha256"]
            with Image.open(io.BytesIO(pixels)) as retained:
                assert retained.size == (40, 50)
            ocr = await claim(client, doc_id, "ocr", "fixture-ocr")
            assert ocr["inputs"]["image_sha256"] == evidence["image_sha256"]
            assert await claim(client, doc_id, "ocr", "other-worker") is None
            await submit(client, doc_id, ocr, {"markdown": "1938年"})
            review = await claim(client, doc_id, "review", "fixture-review")
            assert review["inputs"]["markdown"] == "1938年"
            await submit(
                client, doc_id, review,
                {
                    "verdict": "corrections", "markdown": "1939年",
                    "corrections": [{"before": "1938", "after": "1939", "reason": "Synthetic correction fixture"}],
                },
            )
            rejected = await client.call_tool("publish_document", {"document_id": doc_id})
            assert rejected.is_error
            verify = await claim(client, doc_id, "verify", "fixture-independent-check")
            assert verify["inputs"]["markdown"] == "1939年"
            assert verify["inputs"]["original_markdown"] == "1938年"
            await submit(client, doc_id, verify, {"verdict": "approved", "issues": []})
            organization = await claim(client, doc_id, "organization", "fixture-organizer")
            await submit(client, doc_id, organization, {"articles": [{"title": "Fixture", "pages": [1]}]})
            published = value(await client.call_tool("publish_document", {"document_id": doc_id}))
            assert published["approval_type"] == "machine"
            assert published["human_approval"] is False
            assert "1939年" in Path(published["markdown_path"]).read_text("utf-8")
            assert "1938年" not in Path(published["markdown_path"]).read_text("utf-8")
            found = value(await client.call_tool("search_library", {"query": "1939"}))
            assert found[0]["document_id"] == doc_id
            assert found[0]["page"] == 1
        # A fresh subprocess reads the same evidence and completed state.
        async with Client(server_parameters(root), mode="legacy", read_timeout_seconds=15) as client:
            recovered = value(await client.call_tool("document_status", {"document_id": doc_id}))
            assert recovered["state"] == "published"
            assert recovered["pages"][0]["ocr"]["markdown"] == "1938年"
            assert recovered["pages"][0]["approved"]["markdown"] == "1939年"

    asyncio.run(scenario())


def test_stdio_pending_pdf_cannot_publish_and_retry_retains_decision(tmp_path):
    source = tmp_path / "scan.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=120)
    with source.open("wb") as stream:
        writer.write(stream)

    async def scenario():
        async with Client(server_parameters(tmp_path / "library"), mode="legacy", read_timeout_seconds=15) as client:
            document = value(await client.call_tool("prepare_document", {"source": str(source)}))
            doc_id = document["id"]
            invalid = await client.call_tool(
                "claim_task", {"document_id": doc_id, "stage": "cards", "worker_id": "fixture"}
            )
            assert invalid.is_error
            ocr = await claim(client, doc_id, "ocr", "fixture-ocr")
            await submit(client, doc_id, ocr, {"markdown": "[无法辨认]"})
            review = await claim(client, doc_id, "review", "fixture-review")
            await submit(client, doc_id, review, {"verdict": "pending", "issues": ["Synthetic unresolved scan"]})
            rejected = await client.call_tool("publish_document", {"document_id": doc_id})
            assert rejected.is_error
            assert value(await client.call_tool("search_library", {"query": "无法辨认"})) == []
            assert await claim(client, doc_id, "organization", "fixture-organizer") is None
            retry = value(
                await client.call_tool("retry_task", {"document_id": doc_id, "task_id": review["task_id"]})
            )
            assert retry["retry_of"] == review["task_id"]
            previous = value(
                await client.call_tool("task_details", {"document_id": doc_id, "task_id": review["task_id"]})
            )
            assert previous["state"] == "superseded"
            assert previous["result"]["issues"] == ["Synthetic unresolved scan"]

    asyncio.run(scenario())
