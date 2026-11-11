"""Synthetic study HTTP integration, never approval of original historical content."""

import json

from fastapi.testclient import TestClient

from hrs.api.main import create_app
from hrs.research.service import Research
from tests.integration.platform.test_research import finish_contract_run
from tests.integration.platform.test_research import research_book as research_book


def test_study_topic_source_canvas_and_portable_copy(research_book):
    settings, engine, bridge, book_id, _, _, _ = research_book
    _, card = finish_contract_run(bridge, bridge.start(book_id), "Synthetic study source")
    research = Research(settings, engine)
    research.bridge, research.workflow = bridge, bridge.workflow
    app = create_app(settings, engine)
    app.state.research = research
    with TestClient(app) as client:
        created = client.post(
            "/api/v2/studies/topics", json={"title": "集成专题", "book_ids": [str(book_id)]}
        )
        assert created.status_code == 200, created.text
        topic = created.json()
        selected = client.post(
            f"/api/v2/studies/topics/{topic['id']}/materials",
            json={
                "expected_version": topic["version"],
                "card_id": card["id"],
                "card_version": card["version"],
            },
        )
        assert selected.status_code == 200, selected.text
        topic = selected.json()
        assert topic["material_count"] == 1 and not topic["materials"][0]["evidence"]
        pin = topic["materials"][0]["id"]
        evidence = client.get(f"/api/v2/studies/topics/{topic['id']}/materials/{pin}").json()
        assert evidence["evidence"][0]["quote"] == "人物甲"
        assert evidence["evidence"][0]["reading_target"]["physical_page"] > 0
        invalid = client.post(
            f"/api/v2/studies/topics/{topic['id']}/records",
            json={"kind": "thought", "title": "伪造身份", "origin": "ai_adopted"},
        )
        assert invalid.status_code == 422
        record = client.post(
            f"/api/v2/studies/topics/{topic['id']}/records",
            json={
                "kind": "clue",
                "title": "个人时间线索",
                "source_refs": [pin],
                "date": {"text": "1936年", "normalized": "1936", "certainty": "inferred"},
            },
        ).json()
        response = client.post(
            f"/api/v2/studies/topics/{topic['id']}/canvases",
            json={
                "title": "集成画布",
                "document": {
                    "nodes": [
                        {
                            "id": "note",
                            "record_kind": "record",
                            "record_id": record["id"],
                            "x": 1,
                            "y": 2,
                        }
                    ]
                },
            },
        )
        assert response.status_code == 200, response.text
        canvas = response.json()
        changed = client.patch(
            f"/api/v2/studies/canvases/{canvas['id']}",
            json={"expected_version": 1, "title": "修改画布"},
        )
        assert changed.status_code == 200 and changed.json()["version"] == 2
        conflict = client.patch(
            f"/api/v2/studies/canvases/{canvas['id']}",
            json={"expected_version": 1, "title": "冲突内容"},
        )
        assert conflict.status_code == 409
        timeline = client.get(
            f"/api/v2/studies/topics/{topic['id']}/timeline",
            params={"material_id": pin, "include_undated": False},
        )
        assert timeline.status_code == 200, timeline.text
        assert any(
            item["item_kind"] == "personal" and item["name"] == record["title"]
            for item in timeline.json()["items"]
        )
        backup = client.get(f"/api/v2/studies/canvases/{canvas['id']}/export").json()
        assert backup["workspace_id"]
        restored = client.post(
            f"/api/v2/studies/topics/{topic['id']}/canvases/import",
            json={"expected_version": topic["version"], "backup": backup, "title": "恢复副本"},
        )
        assert restored.status_code == 200, restored.text
        assert restored.json()["id"] != canvas["id"]
        assert (
            client.get(
                f"/api/v2/studies/canvases/{canvas['id']}/export", params={"format": "markdown"}
            ).status_code
            == 200
        )
        assert len(client.get(f"/api/v2/studies/canvases/{canvas['id']}/revisions").json()) == 2
        assert not client.get("/api/v2/studies/topics").json()["items"][0]["materials"]
        # No study operation changed the frozen source card or granted a source decision.
        retained = research.card(card["id"])
        assert (
            retained["version"] == card["version"]
            and retained["approval_type"] == card["approval_type"]
        )
        assert json.dumps(retained["evidence"], sort_keys=True) == json.dumps(
            research.card(card["id"])["evidence"], sort_keys=True
        )
