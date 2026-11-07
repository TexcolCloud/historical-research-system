"""Synthetic transport fixtures exercise real stdio MCP; these are not accuracy approvals."""

import asyncio
import json
import time

import pytest
from mcp import Client

from hrs.processing.navigation_verification import prepare_link_verification
from hrs.processing.workflow_rules import new_task, request_continuation
from tests.integration.codex.test_mcp import server_parameters, value
from tests.unit.processing.test_store import complete, document


def navigation_document(tmp_path, count=1, *, organized_legacy=False):
    store, doc_id = document(tmp_path, count + 1)
    titles = [f"Transport topic {number}" for number in range(1, count + 1)]
    toc = "目录\n" + "\n".join(f"{title} {number + 100}" for number, title in enumerate(titles, 1))
    texts = [
        toc,
        *(
            f"# {title}\n\nTransport body[^1]\n\n[^1]: Retained note.\n\n{number}"
            for number, title in enumerate(titles, 1)
        ),
    ]
    for number, text in enumerate(texts, 1):
        complete(store, doc_id, "ocr", f"transport-ocr-{number}", {"markdown": text})
        complete(
            store,
            doc_id,
            "review",
            f"transport-review-{number}",
            {
                "verdict": "approved",
                "issues": [],
                "page_observation": {"kind": "toc", "headings": []}
                if number == 1
                else {
                    "kind": "text",
                    "headings": [titles[number - 2]],
                    "printed_label": str(number - 1),
                },
            },
        )
    parser = store.claim_task(doc_id, "navigation", "transport-parser")
    store.original_page(doc_id, 1, task_id=parser["task_id"], lease_token=parser["lease_token"])
    store.submit_task(
        doc_id,
        parser["task_id"],
        parser["lease_token"],
        {
            "input_sha256": parser["input_sha256"],
            "entries": [
                {
                    "source_page": 1,
                    "title": title,
                    "target_label": str(number + 100),
                    "source_anchor": f"{title} {number + 100}",
                }
                for number, title in enumerate(titles, 1)
            ],
        },
    )
    if organized_legacy:
        # Arrange an organization produced before link-verification tasks existed.
        # No claimed task or retained result is removed from this synthetic legacy snapshot.
        with store.lock:
            doc = store.checkpoints.read(doc_id)
            doc["tasks"] = [t for t in doc["tasks"] if not t.get("verification_inputs")]
            doc["tasks"].append(new_task("organization"))
            store.checkpoints.write(doc)
        complete(
            store,
            doc_id,
            "organization",
            "transport-organizer",
            {"articles": [{"title": "Transport source", "pages": list(range(1, count + 2))}]},
        )
        with store.lock:
            doc = store.checkpoints.read(doc_id)
            doc["tasks"] = [t for t in doc["tasks"] if not t.get("verification_inputs")]
            doc["state"] = "ready"
            store.checkpoints.write(doc)
    return store, doc_id, titles


def assert_source_only(task, titles):
    encoded = json.dumps(task, ensure_ascii=False)
    assert all(title not in encoded for title in titles)
    assert "verification_inputs" not in task
    assert "navigation_reading_history" not in task
    assert "entries" not in task.get("inputs", {})


async def read_candidates(client, doc_id, task):
    readings = []
    for page in task["inputs"]["pages"]:
        image = await client.call_tool(
            "original_image",
            {
                "document_id": doc_id,
                "page": page["page"],
                "task_id": task["task_id"],
                "lease_token": task["lease_token"],
            },
        )
        assert not image.is_error
        assert any(block.type == "image" for block in image.content)
        receipt = json.loads(next(block.text for block in image.content if block.type == "text"))
        readings.append(
            {
                "page": page["page"],
                "image_receipt": receipt["image_receipt"],
                "image_sha256": receipt["image_sha256"],
                "observations": ["Synthetic image transport inspected."],
                "unresolved": [],
            }
        )
    candidates = value(
        await client.call_tool(
            "navigation_candidates",
            {
                "document_id": doc_id,
                "task_id": task["task_id"],
                "lease_token": task["lease_token"],
                "original_readings": readings,
            },
        )
    )
    return candidates["entries"], readings


def links(entries):
    return [
        {
            "entry_id": row["id"],
            "target_page": row["candidates"][0]["physical_page"],
            "target_anchor": row["candidates"][0]["anchor"],
        }
        for row in entries
    ]


def test_stdio_link_verification_withholds_candidates_until_all_originals_are_frozen(tmp_path):
    store, doc_id, titles = navigation_document(tmp_path)
    ready = next(t for t in store.status(doc_id)["tasks"] if t.get("verification_inputs"))

    async def scenario():
        async with Client(
            server_parameters(store.root), mode="legacy", read_timeout_seconds=20
        ) as client:
            unclaimed = value(
                await client.call_tool(
                    "task_details", {"document_id": doc_id, "task_id": ready["task_id"]}
                )
            )
            assert_source_only(unclaimed, titles)
            assert (
                value(
                    await client.call_tool(
                        "claim_task",
                        {
                            "document_id": doc_id,
                            "stage": "navigation",
                            "worker_id": "transport-parser",
                        },
                    )
                )
                is None
            )
            task = value(
                await client.call_tool(
                    "claim_task",
                    {
                        "document_id": doc_id,
                        "stage": "navigation",
                        "worker_id": "transport-independent-sol",
                    },
                )
            )
            assert_source_only(task, titles)
            retained = value(
                await client.call_tool(
                    "task_details", {"document_id": doc_id, "task_id": task["task_id"]}
                )
            )
            assert_source_only(retained, titles)
            args = {
                "document_id": doc_id,
                "task_id": task["task_id"],
                "lease_token": task["lease_token"],
            }
            for name in ("approved_pages_text", "approved_page_catalog"):
                denied = await client.call_tool(name, args)
                assert denied.is_error
                assert "original" in denied.content[0].text
            entries, _ = await read_candidates(client, doc_id, task)
            text = value(await client.call_tool("approved_pages_text", args))
            assert len(text["pages"]) == 2
            receipt = value(
                await client.call_tool(
                    "submit_task",
                    {
                        **args,
                        "result": {"input_sha256": task["input_sha256"], "links": links(entries)},
                    },
                )
            )
            assert receipt["task_state"] == "completed"
            row = store.status(doc_id)["navigation"]["entries"][0]
            assert row["status"] == "matched_verified"
            assert row["verification"]["receipt"]["human_approval"] is False
            assert "decision" not in row

    asyncio.run(scenario())


def test_expired_navigation_lease_cannot_reuse_another_workers_original_reading(tmp_path):
    store, doc_id, titles = navigation_document(tmp_path)

    async def scenario():
        async with Client(
            server_parameters(store.root), mode="legacy", read_timeout_seconds=20
        ) as client:
            first = value(
                await client.call_tool(
                    "claim_task",
                    {
                        "document_id": doc_id,
                        "stage": "navigation",
                        "worker_id": "transport-first-sol",
                    },
                )
            )
            _, old_readings = await read_candidates(client, doc_id, first)
            with store.lock:
                doc = store.checkpoints.read(doc_id)
                task = next(t for t in doc["tasks"] if t["task_id"] == first["task_id"])
                task["claim"]["expires_at"] = time.time() - 1
                store.checkpoints.write(doc)
            second = value(
                await client.call_tool(
                    "claim_task",
                    {
                        "document_id": doc_id,
                        "stage": "navigation",
                        "worker_id": "transport-second-sol",
                    },
                )
            )
            assert second["lease_token"] != first["lease_token"]
            assert_source_only(second, titles)
            assert "navigation_original_reading" not in second
            denied = await client.call_tool(
                "navigation_candidates",
                {
                    "document_id": doc_id,
                    "task_id": second["task_id"],
                    "lease_token": second["lease_token"],
                    "original_readings": old_readings,
                },
            )
            assert denied.is_error
            entries, new_readings = await read_candidates(client, doc_id, second)
            assert entries and old_readings[0]["image_receipt"] != new_readings[0]["image_receipt"]
            saved = store.get_task(doc_id, first["task_id"])
            assert (
                saved["navigation_reading_history"][0]["claim"]["worker_id"]
                == "transport-first-sol"
            )

    asyncio.run(scenario())


@pytest.mark.parametrize("published", [False, True])
def test_runtime_packets_complete_two_navigation_batches_without_republishing_content(
    tmp_path, published
):
    store, doc_id, titles = navigation_document(tmp_path, count=4, organized_legacy=True)
    before = store.publish(doc_id) if published else None
    with store.context.verified(doc_id) as doc:
        # New metadata work on an already organized/published pre-upgrade document.
        for task in doc["tasks"]:
            if task.get("verification_inputs") and "claim" not in task:
                task["state"] = "superseded"
        assert prepare_link_verification(doc)
        store.checkpoints.write(doc)
    store.request_run(doc_id)

    async def scenario():
        async with Client(
            server_parameters(store.root), mode="legacy", read_timeout_seconds=20
        ) as client:
            dispatched = value(await client.call_tool("dispatch_work", {"document_id": doc_id}))
            assignment = next(a for a in dispatched["assignments"] if a["stage"] == "navigation")
            worker = "transport-runtime-sol-0"
            outcomes = []
            for batch in range(2):
                if batch and outcomes[-1]["session_state"] == "exited":
                    dispatched = value(
                        await client.call_tool(
                            "dispatch_work",
                            {
                                "document_id": doc_id,
                                "active_session_tokens": [assignment["session_token"]],
                            },
                        )
                    )
                    assignment = next(
                        a for a in dispatched["assignments"] if a["stage"] == "navigation"
                    )
                    worker = f"transport-runtime-sol-{batch}"
                packet = value(
                    await client.call_tool(
                        "open_task_packet",
                        {
                            "session_token": assignment["session_token"],
                            "worker_id": worker,
                            "wait_seconds": 0,
                        },
                    )
                )
                task = packet["task"]
                assert_source_only(task, titles)
                entries, _ = await read_candidates(client, doc_id, task)
                outcome = value(
                    await client.call_tool(
                        "submit_packet",
                        {
                            "session_token": assignment["session_token"],
                            "worker_id": worker,
                            "result": {
                                "input_sha256": task["input_sha256"],
                                "links": links(entries),
                            },
                        },
                    )
                )
                outcomes.append(outcome)
            assert outcomes[0]["state"] == ("published" if published else "processing")
            assert outcomes[-1]["state"] == "published"

    asyncio.run(scenario())
    after = store.status(doc_id)
    assert all(row["status"] == "matched_verified" for row in after["navigation"]["entries"])
    assert (
        sum(
            t.get("inputs", {}).get("navigation_mode") == "verify_links"
            and t["state"] == "completed"
            for t in after["tasks"]
        )
        == 2
    )
    if published:
        assert after["publication"] == before
        assert store.publish(doc_id) == before


def test_metadata_assistance_does_not_resume_an_explicitly_paused_document(tmp_path):
    store, doc_id, _ = navigation_document(tmp_path)
    store.pause_document(doc_id)
    with store.context.verified(doc_id) as doc:
        count = len(doc["tasks"])
        assert not prepare_link_verification(doc, requested=True)
        assert len(doc["tasks"]) == count
    store.request_run(doc_id)
    assert store.status(doc_id)["run_request"]["pause_requested"]
    assert not store.dispatch.reserve(doc_id)["assignments"]


def published_metadata(tmp_path):
    store, doc_id, _ = navigation_document(tmp_path, organized_legacy=True)
    publication = store.publish(doc_id)
    with store.context.verified(doc_id) as doc:
        assert request_continuation(doc)["state"] == "succeeded"
        assert prepare_link_verification(doc)
        store.checkpoints.write(doc)
    return store, doc_id, publication


def test_published_metadata_checkpoint_recovery_requeues_without_republishing(tmp_path):
    store, doc_id, publication = published_metadata(tmp_path)
    with store.lock:
        doc = store.checkpoints.read(doc_id)
        doc["run_request"].update(state="retryable_error", technical_failures=3, retry_limit=3)
        store.checkpoints.write(doc)
    store.runtime_control.failure("rate_limit", "synthetic-metadata-pressure", model="gpt-6.1-sol")
    with store.checkpoints.ledger.connect() as connection:
        pressure = connection.execute(
            "SELECT scope,payload FROM runtime_control ORDER BY scope"
        ).fetchall()
    before = store.status(doc_id)
    recovered = store.recover_run(doc_id, "synthetic-metadata-recovery", automatic=True)
    after = store.status(doc_id)
    assert recovered["unfinished_tasks"] == 1
    assert recovered["state"] == "pending"
    assert after["state"] == "published"
    assert after["publication"] == publication
    assert after["pages"] == before["pages"]
    assert after["run_request"]["automatic_checkpoint_used"]
    with store.checkpoints.ledger.connect() as connection:
        assert (
            connection.execute(
                "SELECT scope,payload FROM runtime_control ORDER BY scope"
            ).fetchall()
            == pressure
        )


def test_published_metadata_finish_cannot_report_success_while_work_is_ready(tmp_path):
    store, doc_id, publication = published_metadata(tmp_path)
    store.request_run(doc_id)
    claimed = store.claim_run("synthetic-metadata-coordinator", document_id=doc_id)
    assert claimed is not None
    result = store.finish_run(doc_id, claimed["token"])
    assert result["state"] == "pending"
    assert result["technical_failures"] == 0
    assert store.status(doc_id)["publication"] == publication


def test_published_metadata_refresh_preserves_an_active_coordinator_lease(tmp_path):
    store, doc_id, _ = published_metadata(tmp_path)
    store.request_run(doc_id)
    claimed = store.claim_run("synthetic-metadata-coordinator", document_id=doc_id)
    repeated = store.request_run(doc_id)
    assert repeated["state"] == "running"
    assert repeated["token"] == claimed["token"]
