"""Synthetic read-surface contracts, never original-source accuracy approval."""

from fastapi.testclient import TestClient
from sqlalchemy import select, update

from hrs.api.main import create_app
from hrs.library.books import get_run
from hrs.research.service import Research
from hrs.storage import models as db
from tests.integration.platform.test_research import finish_contract_run
from tests.integration.platform.test_research import research_book as research_book


def test_network_and_timeline_are_lightweight_current_source_views(research_book, monkeypatch):
    settings, engine, bridge, book_id, source_run, _, _ = research_book
    date = {"text": "1936年", "normalized": "1936", "certainty": "inferred", "evidence_ids": ["e1"]}
    _, card = finish_contract_run(
        bridge,
        bridge.start(book_id),
        "Synthetic timeline document",
        draft_patch={
            "formation_date": {**date, "text": "1940年", "normalized": "1940"},
            "mentions": [
                {
                    "id": "person",
                    "kind": "person",
                    "name": "人物甲",
                    "identity_basis": "Synthetic fixture",
                    "evidence_ids": ["e1"],
                }
            ],
            "events": [
                {
                    "id": "event",
                    "name": "Synthetic event",
                    "description": "Synthetic event only",
                    "date": date,
                    "event_status": "planned",
                    "status_note": "Planned in fixture",
                    "participant_ids": ["person"],
                    "evidence_ids": ["e1"],
                }
            ],
        },
    )
    service = Research(settings, engine)
    service.bridge, service.workflow = bridge, bridge.workflow
    root = next(
        item["id"]
        for item in bridge.workflow.catalogue.entities(book_id=book_id)["items"]
        if item["kind"] == "person"
    )
    app = create_app(settings, engine)
    app.state.research = service
    with TestClient(app) as client:
        with monkeypatch.context() as patch:
            patch.setattr(
                service,
                "entity",
                lambda *args: (_ for _ in ()).throw(AssertionError("Summary loaded full entity")),
            )
            patch.setattr(
                service,
                "card",
                lambda *args, **kwargs: (_ for _ in ()).throw(
                    AssertionError("Summary loaded full card")
                ),
            )
            network = client.get("/api/v2/research/network", params={"entity_id": root, "limit": 1})
            assert network.status_code == 200, network.text
            graph = network.json()
            assert len(graph["nodes"]) == 2 and len(graph["edges"]) == 1
            assert all("evidence" not in item for item in graph["nodes"] + graph["edges"])
            assert graph["edges"][0]["date_basis"] == "event"
            assert graph["edges"][0]["event_status"] == "planned"
            entities = client.get(
                "/api/v2/research/network/entities", params={"kind": "person", "q": "人物"}
            )
            assert entities.status_code == 200, entities.text
            assert entities.json()["total"] == 1 and entities.json()["items"][0]["id"] == root
            assert "evidence" not in entities.json()["items"][0]
            response = client.get(
                "/api/v2/research/timeline",
                params={"summary": True, "date_from": "1936-05", "date_to": "1936-05"},
            )
            assert response.status_code == 200, response.text
            timeline = response.json()
            assert timeline["total"] == timeline["dated_total"] == 1
            event = timeline["items"][0]
            assert event["date_precision"] == "year" and event["date_start"] == "1936"
            assert event["event_status"] == "planned" and event["evidence_ids"] == ["e1"]
            assert event["entity_id"] and event["card_version"] == card["version"]
            assert "evidence" not in event and "relationships" not in event
            documents = client.get(
                "/api/v2/research/timeline",
                params={"summary": True, "date_basis": "formation", "entity_id": root},
            ).json()
            assert documents["items"][0]["item_kind"] == "document"
            assert documents["items"][0]["date_start"] == "1940"
        detail = client.get(f"/api/v2/research/network/relations/{graph['edges'][0]['id']}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["evidence"][0]["quote"] == "人物甲"
        assert (
            detail.json()["date_basis"] == "event" and detail.json()["date"]["normalized"] == "1936"
        )
        assert detail.json()["event_status"] == "planned"
        legacy = client.get("/api/v2/research/timeline").json()
        assert legacy["items"][0]["evidence"][0]["quote"] == "人物甲"
        original = get_run(engine, source_run)["result"]
        mutations = [
            (
                db.runs,
                db.runs.c.id == source_run,
                {"result": {**original, "codex_publication_sha256": "f" * 64}},
                {"result": original},
            ),
            (
                db.runs,
                db.runs.c.id == source_run,
                {"result": {**original, "codex_workspace_id": "foreign-workspace"}},
                {"result": original},
            ),
            (db.books, db.books.c.id == book_id, {"state": "deleted"}, {"state": "ready"}),
            (
                db.research_cards,
                db.research_cards.c.id == card["id"],
                {"state": "excluded"},
                {"state": card["state"]},
            ),
        ]
        for table, condition, changed, restored in mutations:
            with engine.begin() as connection:
                connection.execute(update(table).where(condition).values(**changed))
            assert (
                client.get("/api/v2/research/timeline", params={"summary": True}).json()["total"]
                == 0
            )
            assert (
                client.get("/api/v2/research/network", params={"entity_id": root}).status_code
                == 404
            )
            assert client.get("/api/v2/research/network/entities").json()["total"] == 0
            with engine.begin() as connection:
                connection.execute(update(table).where(condition).values(**restored))
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    select(db.research_cards.c.state).where(db.research_cards.c.id == card["id"])
                )
                == card["state"]
            )
