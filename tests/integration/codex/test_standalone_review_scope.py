"""Standalone review uses the same located doubts and approval scope as the platform."""

import json
import re

from hrs.processing.snapshots import atomic_json
from tests.integration.codex.test_human_review import (
    complete,
    form_from_page,
    pending_document,
    request,
    running_server,
)


def located_pending(tmp_path):
    store, document = pending_document(tmp_path)
    task = store.status(document)["tasks"][-1]
    store.retry_task(document, task["task_id"])
    complete(
        store,
        document,
        "review",
        "located-technical-review",
        {
            "verdict": "pending",
            "issues": [
                {
                    "explanation": "核对这个数字",
                    "anchor": {"exact": "1938年。", "prefix": "", "suffix": ""},
                    "focus": "8",
                    "source_quote": "193〔待辨〕年。",
                    "candidate_quote": "1938年。",
                    "boxes": [[0.1, 0.2, 0.5, 0.3]],
                    "image_precision": "region",
                }
            ],
        },
    )
    return store, document


def test_standalone_locates_doubts_and_requires_every_explicit_judgment(tmp_path):
    store, document = located_pending(tmp_path)
    atomic_json(store.root / ".workflow-test-authorization.json", {"enabled": True})
    with running_server(store) as server:
        status, _, body = request(server)
        assert status == 200
        text = body.decode("utf-8")
        assert "流程验收工作区" in text and "不代表内容准确率通过" in text
        assert re.search(r'<mark[^>]+data-review-id="[^"]+"[^>]*>8</mark>', text)
        assert 'aria-label="疑点原图区域"' in text
        assert "核对这个数字" in text and "机器参考读法" in text
        action, form = form_from_page(body)
        radio = re.search(r'name="(resolution\.[^"]+)"', text)[1]
        assert form["decision_scope"] == "workflow_test"
        incomplete = {
            key: value for key, value in form.items() if not key.startswith("resolution.")
        }
        assert request(server, "POST", action, incomplete)[0] == 409
        assert store.status(document)["pages"][0]["state"] == "pending"
        form.update({radio: "keep", "markdown": store.human_packet(document, 1)["markdown"]})
        assert request(server, "POST", action, form)[0] == 303
        page = store.status(document)["pages"][0]
        assert page["approved"]["approval_type"] == "workflow_test"
        assert page["approved"]["content_verified"] is False
        assert store.status(document)["tasks"][-1]["stage"] == "organization"
        reviewed = next(t for t in store.status(document)["tasks"] if t["stage"] == "human_review")
        assert reviewed["result"]["resolutions"] == [
            {"id": radio.split(".", 1)[1], "action": "keep"}
        ]


def test_scope_changes_reject_final_submission_but_unknown_scope_keeps_drafts(tmp_path):
    store, document = located_pending(tmp_path)
    marker = store.root / ".workflow-test-authorization.json"
    atomic_json(marker, {"enabled": True})
    with running_server(store) as server:
        _, _, body = request(server)
        action, form = form_from_page(body)
        atomic_json(marker, {"enabled": False})
        assert request(server, "POST", action, form)[0] == 409
        assert store.status(document)["pages"][0]["state"] == "pending"
        marker.write_text("invalid", "utf-8")
        _, _, body = request(server)
        assert "工作区范围未确认".encode() in body
        assert re.search(rb'<button[^>]+name="verdict"[^>]+value="approved"[^>]+disabled', body)
        action, draft = form_from_page(body)
        assert (
            request(
                server,
                "POST",
                action,
                {**draft, "verdict": "pending", "markdown": "已保存的临时草稿"},
            )[0]
            == 303
        )
        packet = store.human_packet(document, 1)
        assert packet["markdown"] == "已保存的临时草稿"
        assert packet["history"][-1]["receipt"]["approval_type"] == "human_draft"
        assert json.loads(json.dumps(packet, ensure_ascii=False))["history"][-1]["result"][
            "resolutions"
        ]
