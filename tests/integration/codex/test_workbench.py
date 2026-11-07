"""Local workbench operations against synthetic evidence, without launching models."""

import json
import select
from http.client import HTTPConnection
from urllib.parse import urlencode, urlparse

import pytest
from PIL import Image

from hrs.processing.store import Store
from tests.integration.codex.test_human_review import (
    complete,
    form_from_page,
    pending_document,
    request,
    running_server,
)


def view(server, **query):
    token = urlparse(server.review_url).query.split("&")[0]
    return "/?" + token + "&" + urlencode(query)


def action(server, name):
    return "/" + name + "?" + urlparse(server.review_url).query


@pytest.mark.parametrize("rejection", ["origin", "token", "host"])
def test_rejected_post_consumes_bounded_body_before_closing_connection(tmp_path, rejection):
    store = Store(tmp_path / "workspace")
    body = urlencode({"source": "must-not-import", "title": "Rejected source"}).encode()
    with running_server(store) as server:
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=10)
        path = "/import?token=wrong" if rejection == "token" else action(server, "import")
        connection.putrequest("POST", path, skip_host=True)
        connection.putheader("Host", "foreign.invalid" if rejection == "host" else server.authority)
        connection.putheader(
            "Origin",
            "http://foreign.invalid" if rejection == "origin" else "http://" + server.authority,
        )
        connection.putheader("Content-Type", "application/x-www-form-urlencoded")
        connection.putheader("Content-Length", str(len(body)))
        connection.endheaders()
        # Closing with unread POST data can discard the 409 response on Windows.
        responded_before_body = bool(select.select([connection.sock], [], [], 0.1)[0])
        connection.send(body)
        response = connection.getresponse()
        assert response.status == 409
        response.read()
        connection.close()
        assert not responded_before_body
    assert store.list_documents() == []


@pytest.mark.parametrize("length", [None, "0", "-1", "2000001", "invalid"])
def test_rejected_post_does_not_wait_for_unbounded_or_unframed_body(tmp_path, length):
    store = Store(tmp_path / "workspace")
    with running_server(store) as server:
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=2)
        connection.putrequest("POST", action(server, "import"))
        if length is not None:
            connection.putheader("Content-Length", length)
        connection.endheaders()
        response = connection.getresponse()
        assert response.status == 409
        response.read()
        connection.close()
    assert store.list_documents() == []


def test_post_body_read_has_one_deadline_for_the_entire_form(tmp_path, monkeypatch):
    from hrs.codex import workbench

    ticks = iter([0.0, 0.0])
    monkeypatch.setattr(workbench, "monotonic", lambda: next(ticks, 6.0))
    store = Store(tmp_path / "workspace")
    with running_server(store) as server:
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=2)
        connection.putrequest("POST", action(server, "import"))
        connection.putheader("Origin", "http://" + server.authority)
        connection.putheader("Content-Length", "100000")
        connection.endheaders()
        connection.send(b"source=must-not-import")
        response = connection.getresponse()
        assert response.status == 409
        assert b"timed out" in response.read()
        connection.close()
    assert store.list_documents() == []


def test_default_tasks_import_queues_without_claiming_execution_and_rejects_cross_origin(tmp_path):
    image = tmp_path / "local.png"
    Image.new("RGB", (80, 100), "white").save(image)
    store = Store(tmp_path / "workspace")
    with running_server(store) as server:
        status, _, body = request(server, path=view(server, view="tasks"))
        assert status == 200 and "处理任务".encode() in body
        assert "导入原件".encode() in body and str(store.root).encode() in body
        form = {"source": str(image), "title": "本地导入 <script>"}
        assert request(server, "POST", action(server, "import"), form, origin=False)[0] == 409
        assert store.list_documents() == []
        status, headers, _ = request(server, "POST", action(server, "import"), form)
        assert status == 303 and "view=document" in headers["Location"]
        doc = store.status(store.list_documents()[0]["id"])
        assert doc["run_request"]["state"] == "pending"
        assert doc["run_request"]["attempts"] == 0
        status, _, body = request(server, path=headers["Location"])
        assert status == 200 and "已排队".encode() in body
        assert b"<script>" not in body and b"&lt;script&gt;" in body
        assert "原生文本".encode() not in body


def test_workbench_exposes_technical_failure_and_explicit_retry_without_approving_content(tmp_path):
    store, doc_id = pending_document(tmp_path)
    store.request_run(doc_id)
    run = store.claim_run("synthetic-runner")
    store.finish_run(doc_id, run["token"], error="Synthetic transport failure <unsafe>")
    with running_server(store) as server:
        status, _, body = request(server, path=view(server, view="document", document=doc_id))
        assert status == 200 and b"retryable_error" in body
        assert b"Synthetic transport failure &lt;unsafe&gt;" in body
        assert "对照原图核对".encode() in body
        assert request(server, "POST", action(server, "run"), {"document_id": doc_id})[0] == 303
        doc = store.status(doc_id)
        assert doc["run_request"]["state"] == "pending"
        assert doc["run_request"]["technical_failures"] == 0
        assert doc["pages"][0]["state"] == "pending"
        assert doc["publication"] is None
        # Old bookmarks still lead directly to the original comparison and genuine human form.
        status, _, body = request(server, path=view(server, document=doc_id, page=1))
        assert status == 200 and "提交本页并确认通过".encode() in body
        assert "查看原始 OCR".encode() in body
        connection = HTTPConnection("127.0.0.1", server.server_port)
        connection.request("GET", view(server, view="tasks"), headers={"Host": "evil.example"})
        assert connection.getresponse().status == 409
        connection.close()


def test_embedded_browser_opaque_origin_requires_same_origin_fetch_metadata_and_token(tmp_path):
    image = tmp_path / "synthetic.png"
    Image.new("RGB", (40, 50), "white").save(image)
    store = Store(tmp_path / "workspace")
    with running_server(store) as server:
        body = urlencode(
            {"source": str(image), "title": "Synthetic embedded browser fixture"}
        ).encode()
        for site, mode, expected in [
            ("cross-site", "navigate", 409),
            ("same-origin", "cors", 409),
            ("same-origin", "navigate", 303),
        ]:
            connection = HTTPConnection("127.0.0.1", server.server_port)
            connection.request(
                "POST",
                action(server, "import"),
                body,
                {
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Origin": "null",
                    "Sec-Fetch-Site": site,
                    "Sec-Fetch-Mode": mode,
                },
            )
            response = connection.getresponse()
            assert response.status == expected
            response.read()
            connection.close()
        assert len(store.list_documents()) == 1


def test_progress_feed_is_private_and_changes_with_durable_state(tmp_path):
    store, doc_id = pending_document(tmp_path)
    with running_server(store) as server:
        path = action(server, "changes")
        status, headers, body = request(server, path=path)
        first = json.loads(body)
        assert status == 200 and headers["Content-Type"].startswith("application/json")
        assert json.loads(request(server, path=path)[2]) == first
        store.request_run(doc_id)
        second = json.loads(request(server, path=path)[2])
        assert first["workspace_id"] == second["workspace_id"]
        assert first["revision"] != second["revision"]
        assert "markdown" not in second
        assert request(server, path="/changes?token=wrong")[0] == 409
        status, headers, script = request(server, path=action(server, "assets/workbench.js"))
        assert status == 200 and headers["Content-Type"].startswith("text/javascript")
        assert b"data-live-section" in script
        _, headers, page = request(server, path=view(server, view="review"))
        assert b'data-live-section="review-content"' in page
        assert b"data-workspace-id=" in page
        assert "script-src 'self'" in headers["Content-Security-Policy"]
        assert "connect-src 'self'" in headers["Content-Security-Policy"]


def test_saving_draft_stays_on_the_same_physical_page(tmp_path):
    store, doc_id = pending_document(tmp_path, pages=2)
    with running_server(store) as server:
        _, _, body = request(server, path=view(server, view="review", document=doc_id, page=2))
        action_url, form = form_from_page(body)
        status, headers, _ = request(server, "POST", action_url, {**form, "verdict": "pending"})
        assert status == 303
        from urllib.parse import parse_qs

        query = parse_qs(urlparse(headers["Location"]).query)
        assert query["document"] == [doc_id] and query["page"] == ["2"]
        assert store.human_packet(doc_id, 2)["markdown"] == form["markdown"]


def test_embedded_progressive_form_requires_trusted_metadata_and_keeps_bad_submissions_pending(
    tmp_path,
):
    store, doc_id = pending_document(tmp_path)
    with running_server(store) as server:
        body = urlencode({"document_id": doc_id}).encode()
        for site, marker, expected in [
            ("cross-site", "1", 409),
            ("same-origin", "", 409),
            ("same-origin", "1", 303),
        ]:
            connection = HTTPConnection("127.0.0.1", server.server_port)
            connection.request(
                "POST",
                action(server, "run"),
                body,
                {
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Origin": "null",
                    "Sec-Fetch-Site": site,
                    "Sec-Fetch-Mode": "same-origin",
                    "X-HRS-Form": marker,
                },
            )
            response = connection.getresponse()
            assert response.status == expected
            response.read()
            connection.close()
        assert store.status(doc_id)["pages"][0]["state"] == "pending"
        assert store.status(doc_id)["publication"] is None


def test_library_search_only_includes_published_reviewed_text_and_keeps_page_provenance(tmp_path):
    store, doc_id = pending_document(tmp_path)
    with running_server(store) as server:
        path = view(server, view="library", q="Synthetic finding")
        assert "检索结果 · 0".encode() in request(server, path=path)[2]
        packet = store.human_packet(doc_id, 1)
        store.record_human_review(
            doc_id,
            1,
            input_sha256=packet["input_sha256"],
            reviewer="Synthetic reviewer",
            markdown="Synthetic finding <source>",
            note="Synthetic fixture confirmation only",
            verdict="approved",
        )
        complete(
            store,
            doc_id,
            "organization",
            "synthetic-organizer",
            {
                "articles": [{"title": "Synthetic article", "pages": [1]}],
            },
        )
        store.publish(doc_id)
        status, _, body = request(server, path=path)
        assert status == 200 and "检索结果 · 1".encode() in body
        assert b"Synthetic finding &lt;source&gt;" in body
        assert "物理第 1 页".encode() in body
        assert doc_id.encode() in body


def test_revision_workspace_keeps_active_library_and_original_images_available(tmp_path):
    store, doc_id = pending_document(tmp_path)
    previous = store.status(doc_id)["tasks"][-1]
    store.retry_task(doc_id, previous["task_id"])
    complete(
        store,
        doc_id,
        "review",
        "synthetic-independent-review",
        {"verdict": "approved", "issues": []},
    )
    complete(
        store,
        doc_id,
        "organization",
        "synthetic-organizer",
        {"articles": [{"title": "Synthetic archive", "pages": [1]}]},
    )
    publication = store.publish(doc_id)
    with running_server(store) as server:
        image_url = (
            "/page-image?"
            + urlparse(server.review_url).query
            + "&"
            + urlencode({"document": doc_id, "page": 1})
        )
        assert request(server, path=image_url)[0] == 200
        assert (
            request(
                server,
                "POST",
                action(server, "revision"),
                {"document_id": doc_id, "reason": "Synthetic explicit recheck"},
            )[0]
            == 303
        )
        status, _, body = request(server, path=view(server, view="document", document=doc_id))
        assert status == 200 and "版草稿".encode() in body
        assert "已入库文档 · 1".encode() in request(server, path=view(server, view="library"))[2]
        assert store.search("1938")[0]["document_id"] == doc_id
        assert store.search("1938")[0]["revision_pending"] is True
        assert (
            b"Synthetic explicit recheck"
            in request(server, path=view(server, view="library", q="1938"))[2]
        )
        assert (
            "以下为历史发布文本".encode()
            in request(server, path=view(server, view="library", q="1938"))[2]
        )
        assert store.status(doc_id)["active_publication"]["publication"] == publication
        assert request(server, path=image_url)[0] == 200
