"""The no-SQL fallback can resolve genuine synthetic directory disputes end to end."""

import html
import re
from urllib.parse import urlencode, urlparse

import pytest

from hrs.processing.snapshots import atomic_json
from tests.integration.codex.test_human_review import (
    complete,
    pending_document,
    request,
    running_server,
)


def navigation_document(tmp_path):
    store, document_id = pending_document(tmp_path, pages=2)
    for number, text, kind, label, headings in [
        (1, "目录\n起源 1", "toc", None, []),
        (2, "# 起源\n正文已通过原图核验。\n9", "text", "9", ["起源"]),
    ]:
        packet = store.human_packet(document_id, number)
        store.record_human_review(
            document_id,
            number,
            input_sha256=packet["input_sha256"],
            reviewer="Synthetic fixture",
            markdown=text,
            note="Synthetic only",
            verdict="approved",
        )
        # Observations are test fixture metadata; the actual decision still goes through HTTP.
        with store.lock:
            doc = store.checkpoints.read(document_id)
            from hrs.processing.navigation import observation

            doc["pages"][number - 1]["observation"] = observation(
                doc["pages"][number - 1],
                {"kind": kind, "printed_label": label, "headings": headings},
            )
            store.checkpoints.write(doc)
    store.refresh_navigation(document_id)
    task = store.claim_task(document_id, "navigation", "synthetic-navigation")
    store.original_page(document_id, 1, task_id=task["task_id"], lease_token=task["lease_token"])
    store.submit_task(
        document_id,
        task["task_id"],
        task["lease_token"],
        {
            "input_sha256": task["input_sha256"],
            "entries": [
                {"source_page": 1, "title": "起源", "target_label": "1", "source_anchor": "起源 1"}
            ],
        },
    )
    return store, document_id


def fields(body):
    return {
        name: html.unescape(value)
        for name, value in re.findall(
            r'<input type="hidden" name="([^\"]+)" value="([^\"]*)">', body.decode()
        )
    }


@pytest.mark.parametrize(
    "verdict, expected", [("link", "matched_manual"), ("unlink", "unlinked_manual")]
)
def test_directory_form_displays_both_originals_and_resolves_without_sql(
    tmp_path, verdict, expected
):
    store, document_id = navigation_document(tmp_path)
    nav = store.status(document_id)["navigation"]
    entry = nav["entries"][0]
    assert entry["status"] == "conflict"
    with running_server(store) as server:
        query = (
            urlparse(server.review_url).query.split("&")[0]
            + "&"
            + urlencode({"view": "navigation", "document": document_id, "entry": entry["id"]})
        )
        status, _, body = request(server, path="/?" + query)
        assert status == 200
        assert "目录原件 · PDF 第 1 页".encode() in body
        assert "正文原件 · PDF 第 2 页".encode() in body
        assert "更改正文位置".encode() in body
        assert "人工待审 · 1".encode() in body
        for url in re.findall(r'<img src="([^\"]+)"', body.decode()):
            assert request(server, path=html.unescape(url))[0] == 200
        form = fields(body)
        assert form["expected_sha256"] == nav["sha256"]
        status, _, _ = request(
            server,
            "POST",
            "/navigation-decision?" + urlparse(server.review_url).query,
            {**form, "verdict": verdict},
        )
        assert status == 303
        result = store.status(document_id)["navigation"]["entries"][0]
        assert result["status"] == expected
        assert result["decision"]["content_approval"] is False
        assert store.status(document_id)["pages"][0]["approved"]["approval_type"] == "human"
        assert "目录核对完成".encode() in request(server, path="/?" + query)[2]
        complete(
            store,
            document_id,
            "organization",
            "synthetic-organizer",
            {"articles": [{"title": "起源", "pages": [1, 2]}]},
        )
        assert store.publish(document_id)["approval_type"] == "mixed"


def test_directory_rejects_stale_target_and_records_authorized_test_scope(tmp_path):
    store, document_id = navigation_document(tmp_path)
    atomic_json(
        store.root / ".workflow-test-authorization.json",
        {"enabled": True, "scope": "workflow_only"},
    )
    with running_server(store) as server:
        query = (
            urlparse(server.review_url).query.split("&")[0]
            + "&"
            + urlencode({"view": "navigation", "document": document_id})
        )
        _, _, body = request(server, path="/?" + query)
        form = fields(body)
        assert form["decision_scope"] == "workflow_test"
        action = "/navigation-decision?" + urlparse(server.review_url).query
        assert (
            request(
                server, "POST", action, {**form, "expected_sha256": "0" * 64, "verdict": "unlink"}
            )[0]
            == 409
        )
        assert request(server, "POST", action, {**form, "verdict": "unlink"})[0] == 303
        decision = store.status(document_id)["navigation"]["entries"][0]["decision"]
        assert decision["approval_type"] == "workflow_test" and decision["human_approval"] is False


def test_directory_manual_location_is_previewed_then_bound_to_source_and_unknown_scope_is_blocked(
    tmp_path,
):
    store, document_id = navigation_document(tmp_path)
    with running_server(store) as server:
        query = (
            urlparse(server.review_url).query.split("&")[0]
            + "&"
            + urlencode(
                {
                    "view": "navigation",
                    "document": document_id,
                    "target_page": 2,
                    "target_anchor": "正文已通过原图核验。",
                }
            )
        )
        status, _, body = request(server, path="/?" + query)
        assert status == 200 and "<mark>正文已通过原图核验。</mark>".encode() in body
        form = fields(body)
        action = "/navigation-decision?" + urlparse(server.review_url).query
        authorization = store.root / ".workflow-test-authorization.json"
        authorization.write_text("broken permission", encoding="utf-8")
        assert request(server, "POST", action, {**form, "verdict": "link"})[0] == 409
        _, _, blocked = request(server, path="/?" + query)
        assert b'data-approval-ready="false"' in blocked
        authorization.unlink()
        assert request(server, "POST", action, {**form, "verdict": "link"})[0] == 303
        target = store.status(document_id)["navigation"]["entries"][0]["target"]
        assert target["anchor"] == "正文已通过原图核验。" and target["physical_page"] == 2


def test_directory_decision_does_not_resume_a_user_paused_document(tmp_path):
    store, document_id = navigation_document(tmp_path)
    store.pause_document(document_id)
    with running_server(store) as server:
        query = (
            urlparse(server.review_url).query.split("&")[0]
            + "&"
            + urlencode({"view": "navigation", "document": document_id})
        )
        _, _, body = request(server, path="/?" + query)
        assert (
            request(
                server,
                "POST",
                "/navigation-decision?" + urlparse(server.review_url).query,
                {**fields(body), "verdict": "unlink"},
            )[0]
            == 303
        )
        document = store.status(document_id)
        assert document["state"] == "paused"
        assert document["run_request"]["state"] == "paused"
