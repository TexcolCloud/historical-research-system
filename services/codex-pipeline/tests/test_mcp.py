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
from pypdf.generic import DecodedStreamObject


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
            detail_result = await client.call_tool(
                "original_detail", {"document_id": doc_id, "page": 1, "box": [0, 0, 1, 1]}
            )
            detail = json.loads(next(block.text for block in detail_result.content if block.type == "text"))
            bad_detail = {**detail, "image_sha256": "0" * 64}
            invalid_evidence = await client.call_tool(
                "submit_task",
                {
                    "document_id": doc_id, "task_id": review["task_id"],
                    "lease_token": review["lease_token"],
                    "result": {"input_sha256": review["input_sha256"], "verdict": "approved", "original_details": [bad_detail]},
                },
            )
            assert invalid_evidence.is_error
            await submit(
                client, doc_id, review,
                {
                    "verdict": "corrections", "markdown": "1939年",
                    "corrections": [{"before": "1938", "after": "1939", "reason": "Synthetic correction fixture"}],
                    "original_details": [detail],
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
            detail_path = root / "documents" / doc_id / detail["image"]
            detail_path.write_bytes(b"changed detail")
            assert (await client.call_tool("search_library", {"query": "1939"})).is_error

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
            human_queue = value(await client.call_tool("human_review_queue"))
            assert human_queue[0]["document_id"] == doc_id
            assert human_queue[0]["page"] == 1
            assert human_queue[0]["unresolved_attempts"] == 1
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
            assert value(await client.call_tool("human_review_queue")) == []

    asyncio.run(scenario())


def test_stdio_original_detail_rerenders_pdf_and_uses_native_raster_frames(tmp_path):
    pdf_source = tmp_path / "source.pdf"
    writer = PdfWriter()
    page = writer.add_blank_page(width=100, height=120)
    contents = DecodedStreamObject()
    contents.set_data(b"1 0 0 rg 0 0 50 120 re f\n0 0 1 rg 50 0 50 120 re f")
    page.replace_contents(contents)
    with pdf_source.open("wb") as stream:
        writer.write(stream)
    raster_source = tmp_path / "source.tiff"
    Image.new("RGB", (80, 60), "red").save(
        raster_source, save_all=True, append_images=[Image.new("RGB", (80, 60), "blue")]
    )
    root = tmp_path / "library"

    async def scenario():
        async with Client(server_parameters(root), mode="legacy", read_timeout_seconds=15) as client:
            for source, physical_page, expected_size, expected_crop in (
                (pdf_source, 1, [600, 720], [300, 180, 600, 540]),
                (raster_source, 2, [80, 60], [40, 15, 80, 45]),
            ):
                document = value(await client.call_tool("prepare_document", {"source": str(source)}))
                arguments = {"document_id": document["id"], "page": physical_page, "box": [0.5, 0.25, 1, 0.75]}
                result = await client.call_tool("original_detail", arguments)
                assert not result.is_error, result.content
                metadata = json.loads(next(block.text for block in result.content if block.type == "text"))
                data = base64.b64decode(next(block.data for block in result.content if block.type == "image"))
                assert metadata["source_size"] == expected_size
                assert metadata["crop_pixels"] == expected_crop
                assert metadata["source_sha256"] == document["source"]["sha256"]
                assert metadata["image_sha256"] == metadata["detail_sha256"] == hashlib.sha256(data).hexdigest()
                directory = root / "documents" / document["id"]
                assert (directory / metadata["image"]).read_bytes() == data
                metadata_bytes = (directory / metadata["metadata"]).read_bytes()
                assert hashlib.sha256(metadata_bytes).hexdigest() == metadata["metadata_sha256"]
                persisted = json.loads(metadata_bytes)
                assert "metadata_sha256" not in persisted
                assert persisted["image_sha256"] == metadata["image_sha256"]
                with Image.open(io.BytesIO(data)) as image:
                    assert list(image.size) == metadata["image_size"]
                    assert image.getpixel((image.width // 2, image.height // 2)) == (0, 0, 255)
                cached = await client.call_tool("original_detail", arguments)
                assert not cached.is_error
                assert json.loads(cached.content[0].text) == metadata
                for box in ([0, 0, 0, 1], [-0.1, 0, 1, 1], [0, 0, 1.1, 1], [0, 1, 1, 0], [0, 0, 1]):
                    invalid = await client.call_tool("original_detail", {**arguments, "box": box})
                    assert invalid.is_error
                invalid = await client.call_tool("original_detail", {**arguments, "page": 0})
                assert invalid.is_error
                (directory / document["source"]["path"]).write_bytes(b"changed retained source")
                changed = await client.call_tool("original_detail", arguments)
                assert changed.is_error

    asyncio.run(scenario())
