"""Exercise real local form submissions with explicitly synthetic human decisions."""

import re
import threading
from contextlib import contextmanager
from http.client import HTTPConnection
from urllib.parse import urlencode, urlparse

import pytest
from PIL import Image

from hrs_codex.human_review import create_review_server
from hrs_codex.server import create_server, prepare
from hrs_codex.store import Store


def complete(store, doc_id, stage, worker, result):
    task = store.claim_task(doc_id, stage, worker)
    store.submit_task(
        doc_id,
        task["task_id"],
        task["lease_token"],
        {"input_sha256": task["input_sha256"], **result},
    )
    return task


def pending_document(tmp_path, pages=1):
    image = tmp_path / "original.tiff"
    Image.new("RGB", (80, 100), "white").save(
        image, save_all=True, append_images=[Image.new("RGB", (80, 100), "blue")] * (pages - 1)
    )
    store = Store(tmp_path / "library")
    doc_id = prepare(store, str(image), "Synthetic human review fixture")["id"]
    for page in range(1, pages + 1):
        complete(
            store, doc_id, "ocr", f"ocr-{page}", {"markdown": "1938年。<script>fixture</script>"}
        )
        complete(
            store,
            doc_id,
            "review",
            f"review-{page}",
            {
                "verdict": "pending",
                "issues": ["Synthetic unclear source"],
                "approval_type": "human",
                "human_approval": True,
            },
        )
    return store, doc_id


@contextmanager
def running_server(store):
    with create_review_server(store, 0) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server
        finally:
            server.shutdown()
            thread.join(timeout=5)


def request(server, method="GET", path=None, form=None, origin=True):
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=10)
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if origin:
        headers["Origin"] = "http://" + server.authority
    connection.request(
        method,
        path or urlparse(server.review_url).path + "?" + urlparse(server.review_url).query,
        urlencode(form).encode("utf-8") if form else None,
        headers,
    )
    response = connection.getresponse()
    result = response.status, dict(response.getheaders()), response.read()
    connection.close()
    return result


def form_from_page(body):
    text = body.decode("utf-8")
    import html

    action = html.unescape(re.search(r'<form method="post" action="([^"]+)"', text)[1])
    fields = {
        name: html.unescape(value)
        for name, value in re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)">', text)
    }
    fields.update(
        reviewer="Synthetic test reviewer",
        note="Synthetic fixture only; not a real-source human verdict",
        markdown="1940年。",
        verdict="approved",
        confirmed="yes",
    )
    return action, fields


def test_local_form_preserves_drafts_rejects_stale_actions_and_resumes_publication(tmp_path):
    store, doc_id = pending_document(tmp_path)
    first = store.status(doc_id)["tasks"][-1]
    assert first["receipt"]["approval_type"] == "machine"
    assert first["receipt"]["human_approval"] is False
    retry = store.retry_task(doc_id, first["task_id"])
    assert retry["stage"] == "review"
    complete(
        store,
        doc_id,
        "review",
        "retry-machine",
        {"verdict": "pending", "issues": ["Still unclear after original detail retry"]},
    )
    assert store.human_queue()[0]["unresolved_attempts"] == 2
    with running_server(store) as server:
        status, headers, body = request(server)
        assert status == 200 and "frame-ancestors 'none'" in headers["Content-Security-Policy"]
        assert b"<script>fixture</script>" not in body
        assert b"&lt;script&gt;fixture&lt;/script&gt;" in body
        action, form = form_from_page(body)
        assert request(server, "POST", action, form, origin=False)[0] == 409
        assert (
            request(
                server,
                "POST",
                action.replace(urlparse(server.review_url).query, "token=wrong"),
                form,
            )[0]
            == 409
        )
        assert request(server, "POST", action, {**form, "confirmed": ""})[0] == 409
        assert store.status(doc_id)["pages"][0]["state"] == "pending"
        draft = {**form, "verdict": "pending", "markdown": "人工草稿，原字仍不确定。"}
        assert request(server, "POST", action, draft)[0] == 303
        assert store.human_packet(doc_id, 1)["markdown"] == draft["markdown"]
        assert request(server, "POST", action, form)[0] == 409
        task = store.status(doc_id)["tasks"][-1]
        assert task["receipt"]["approval_type"] == "human"
        assert task["receipt"]["human_approval"] is False
        with pytest.raises(ValueError, match="human draft"):
            store.retry_task(doc_id, task["task_id"])
        with pytest.raises(ValueError, match="valid stage"):
            store.claim_task(doc_id, "human_review", "machine-worker")
        with pytest.raises(ValueError, match="Whole document"):
            store.publish(doc_id)
        assert store.search("人工草稿") == []
        status, _, body = request(server)
        action, form = form_from_page(body)
        assert request(server, "POST", action, form)[0] == 303
    assert store.human_queue() == []
    doc = store.status(doc_id)
    assert doc["pages"][0]["approved"]["approval_type"] == "human"
    assert doc["pages"][0]["approved"]["human_approval"] is True
    assert doc["pages"][0]["ocr"]["markdown"].startswith("1938")
    assert first["result"] == store.get_task(doc_id, first["task_id"])["result"]
    assert len([t for t in doc["tasks"] if t["stage"] == "organization"]) == 1
    complete(
        store,
        doc_id,
        "organization",
        "machine-organizer",
        {"articles": [{"title": "Synthetic article", "pages": [1]}]},
    )
    publication = store.publish(doc_id)
    assert publication["approval_type"] == "mixed"
    assert publication["human_approval"] is False  # Organization still has only machine approval.
    assert publication["human_reviewed_pages"] == [1]
    assert store.search("1940")[0]["page"] == 1
    assert Store(store.root).publish(doc_id) == publication
    with pytest.raises(ValueError, match="unpublished pending"):
        store.human_packet(doc_id, 1)


def test_human_approval_of_one_page_does_not_release_other_unresolved_pages(tmp_path):
    store, doc_id = pending_document(tmp_path, pages=2)
    packet = store.human_packet(doc_id, 1)
    store.record_human_review(
        doc_id,
        1,
        input_sha256=packet["input_sha256"],
        reviewer="Synthetic fixture reviewer",
        markdown="1940年。",
        note="Synthetic confirmation",
        verdict="approved",
    )
    assert store.status(doc_id)["state"] == "pending"
    assert [row["page"] for row in store.human_queue()] == [2]
    assert store.claim_task(doc_id, "organization", "organizer") is None
    with pytest.raises(ValueError, match="Whole document"):
        store.publish(doc_id)


def test_human_packet_uses_the_exact_failed_input_instead_of_an_older_candidate(tmp_path):
    store, doc_id = pending_document(tmp_path)
    store.retry_task(doc_id, store.status(doc_id)["tasks"][-1]["task_id"])
    original = store.status(doc_id)["pages"][0]["ocr"]["markdown"]
    complete(
        store,
        doc_id,
        "review",
        "proposal",
        {
            "verdict": "corrections",
            "markdown": original.replace("1938", "1939"),
            "corrections": [
                {"before": "1938", "after": "1939", "reason": "Synthetic wrong proposal"}
            ],
        },
    )
    failed = complete(
        store,
        doc_id,
        "verify",
        "verifier",
        {"verdict": "pending", "issues": ["Synthetic failed correction"]},
    )
    assert store.human_packet(doc_id, 1)["markdown"].startswith("1939")
    store.retry_task(doc_id, failed["task_id"])
    complete(
        store,
        doc_id,
        "review",
        "retry-review",
        {"verdict": "pending", "issues": ["Original still unclear"]},
    )
    assert store.human_packet(doc_id, 1)["markdown"] == original


def test_human_review_rejects_changed_original_and_is_not_an_mcp_write_tool(tmp_path):
    store, doc_id = pending_document(tmp_path)
    packet = store.human_packet(doc_id, 1)
    doc = store.status(doc_id)
    store._path(doc, doc["source"]["path"]).write_bytes(b"changed original")
    with pytest.raises(ValueError, match="source hash"):
        store.record_human_review(
            doc_id,
            1,
            input_sha256=packet["input_sha256"],
            reviewer="Synthetic reviewer",
            markdown="1940年。",
            note="Synthetic fixture",
            verdict="approved",
        )
    import asyncio

    tools = asyncio.run(create_server(store.root).list_tools())
    names = {tool.name for tool in tools}
    assert "human_review_queue" in names
    assert not any("record_human" in name or "approve_human" in name for name in names)
