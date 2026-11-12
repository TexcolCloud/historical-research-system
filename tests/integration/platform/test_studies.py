"""Synthetic study HTTP integration, never approval of original historical content."""

import json

from fastapi.testclient import TestClient

from hrs.api.main import create_app
from hrs.research.service import Research
from hrs.studies.service import StudyService
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


def test_analysis_disposition_and_edited_adoption_are_personal_versioned_actions(research_book):
    settings, engine, bridge, book_id, _, _, _ = research_book
    _, card = finish_contract_run(bridge, bridge.start(book_id), "Synthetic personal decisions")
    research = Research(settings, engine)
    research.bridge, research.workflow = bridge, bridge.workflow
    studies = StudyService(research)
    topic = studies.create_topic({"title": "Synthetic analysis HTTP contracts"})
    topic = studies.add_material(
        topic["id"],
        {
            "expected_version": topic["version"],
            "card_id": card["id"],
            "card_version": card["version"],
        },
    )
    pin = topic["materials"][0]["id"]
    # A synthetic terminal fixture tests HTTP decisions, not model quality or source approval.
    frozen = studies.store.create(
        "analysis_input", {"sources": [{"id": pin, "card_version": card["version"]}]}
    )
    job = studies.store.create(
        "analysis",
        {
            "mode": "summary",
            "material_ids": [pin],
            "prompt": "",
            "state": "ready",
            "input_id": frozen["id"],
            "suggestions": [
                {
                    "id": "s",
                    "title": "Synthetic suggestion",
                    "text": "Candidate interpretation.",
                    "limitations": "Does not establish the outcome.",
                    "evidence_refs": [{"material_id": pin, "evidence_id": "e1"}],
                    "verified": True,
                    "issues": [],
                }
            ],
        },
        topic_id=topic["id"],
    )
    original_suggestions = job["suggestions"]
    app = create_app(settings, engine)
    app.state.research = research
    path = f"/api/v2/studies/analyses/{job['id']}"
    with TestClient(app) as client:
        assert client.get(path).json()["suggestions"][0]["ignored"] is False
        body = {"expected_version": job["version"], "ignored": True}
        ignored = client.post(path + "/suggestions/s/disposition", json=body)
        assert ignored.status_code == 200, ignored.text
        assert ignored.json()["suggestions"][0]["ignored"] is True
        assert (
            client.post(path + "/suggestions/s/disposition", json=body).json()["version"]
            == ignored.json()["version"]
        )
        blocked = client.post(
            path + "/adopt",
            json={"expected_version": ignored.json()["version"], "suggestion_id": "s"},
        )
        assert blocked.status_code == 409
        restored = client.post(
            path + "/suggestions/s/disposition",
            json={"expected_version": ignored.json()["version"], "ignored": False},
        )
        assert restored.status_code == 200, restored.text
        adopted = client.post(
            path + "/adopt",
            json={
                "expected_version": restored.json()["version"],
                "suggestion_id": "s",
                "text": "My personal interpretation.",
                "limitations": "Still needs corroboration.",
            },
        )
        assert adopted.status_code == 200, adopted.text
        assert adopted.json()["text"] == "My personal interpretation."
        assert adopted.json()["origin"] == "ai_adopted" and adopted.json()["source_refs"] == [pin]
        assert studies.store.read("analysis", job["id"])["suggestions"] == original_suggestions
        assert research.card(card["id"])["version"] == card["version"]
        assert research.card(card["id"])["approval_type"] == card["approval_type"]
