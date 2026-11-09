"""Synthetic post-index and HTTP contracts, without native inference or accuracy approval."""

from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update

from hrs.api.main import create_app
from hrs.library.books import get_run
from hrs.library.native_workspace import WrongWorkspace, require_workspace
from hrs.library.projection.facts import PROJECTION_POLICY
from hrs.library.projection.publication import NativePublication
from hrs.processing import research_workflow
from hrs.processing.workspace import identity as workspace_identity
from hrs.research.api import Research
from hrs.research.card_notes import CardNotes
from hrs.research.models import DocumentMetadata
from hrs.research.notebook import ResearchNotebook
from hrs.research.sources import publication_snapshot
from hrs.search.indexing import BookIndexer
from hrs.storage import models as db
from tests.integration.platform.test_research import research_book as research_book

CARD_ONLY_POLICY = deepcopy(research_workflow.POLICY)
CARD_ONLY_POLICY.update(version=6, card_only=True)
CARD_ONLY_POLICY.pop("dossier_cards", None)


@pytest.fixture
def card_book(research_book, monkeypatch):
    # The shared publication fixture deliberately freezes v4 for its own legacy tests.
    monkeypatch.setattr(research_workflow, "POLICY", deepcopy(CARD_ONLY_POLICY))
    assert research_workflow.POLICY["version"] == 6 and research_workflow.POLICY["card_only"]
    return research_book


def index_again(book, monkeypatch):
    settings, engine, bridge, _, source_run, _, _ = book
    from hrs.search import indexing

    calls = []

    monkeypatch.setattr(BookIndexer, "__init__", lambda self, *args: None)
    monkeypatch.setattr(BookIndexer, "index", lambda self, run_id: calls.append(run_id))
    monkeypatch.setattr(
        BookIndexer, "refresh_navigation", lambda self, run_id: calls.append(run_id)
    )
    # A first import during the patch must cache the real class, whose methods pytest restores.
    from hrs.research import discovery

    assert discovery.BookIndexer is indexing.BookIndexer is BookIndexer
    result = {
        **get_run(engine, source_run)["result"],
        "codex_projection_policy": PROJECTION_POLICY,
        "codex_projected_navigation_sha256": "e" * 64,
    }
    for key in ("codex_indexed_publication_sha256", "codex_indexed_chapters_sha256"):
        result.pop(key, None)
    with engine.begin() as connection:
        connection.execute(update(db.runs).where(db.runs.c.id == source_run).values(result=result))
    document = {
        "state": "published",
        "publication": {"markdown_sha256": result["codex_publication_sha256"]},
        "navigation": {"sha256": "e" * 64},
    }
    return NativePublication(settings, engine, bridge.objects), document, calls


def test_index_completion_automatically_queues_v6_once_without_research_question_injection(
    card_book,
    monkeypatch,
):
    _, engine, bridge, book_id, source_run, _, _ = card_book
    notebook = ResearchNotebook(bridge.store)
    question = notebook.create("保留的历史研究问题", [book_id], notes="历史记录继续可读。")
    retained_question = notebook.question(question["id"])
    syncs = []
    monkeypatch.setattr(ResearchNotebook, "sync_run", lambda *args: syncs.append("run"))
    monkeypatch.setattr(ResearchNotebook, "sync_card", lambda *args: syncs.append("card"))
    publication, document, calls = index_again(card_book, monkeypatch)
    bridge.tick()
    assert bridge.workflow.list_runs(book_id) == []
    publication.index_if_needed(get_run(engine, source_run), document)
    assert calls == [source_run]
    assert bridge.workflow.list_runs(book_id) == []
    bridge.reconcile_at = 0
    bridge.tick()
    run = bridge.workflow.list_runs(book_id)[0]
    assert run["policy"]["version"] == 6 and run["card_only"]
    assert {task["stage"] for task in run["tasks"]} == {"author"}
    snapshot = bridge.workflow.snapshot(run["run_id"])
    assert snapshot["research_questions"] == [] and snapshot["research_questions_omitted"] == 0
    publication, document, _ = index_again(card_book, monkeypatch)
    publication.index_if_needed(get_run(engine, source_run), document)
    bridge.reconcile_at = 0
    bridge.tick()
    assert [item["run_id"] for item in bridge.workflow.list_runs(book_id)] == [run["run_id"]]
    assert notebook.question(question["id"]) == retained_question and syncs == []
    with engine.connect() as connection:
        kinds = connection.execute(
            select(db.outbox.c.kind, func.count()).group_by(db.outbox.c.kind)
        ).all()
    assert dict(kinds).get("start_research") == 1
    assert dict(kinds).get("link_research", 0) == 0
    assert get_run(engine, source_run)["result"]["published"] is True


def test_existing_v5_source_version_is_retained_without_a_parallel_v6_run(card_book):
    settings, engine, bridge, book_id, source_run, _, _ = card_book
    snapshot = publication_snapshot(
        settings, engine, bridge.store, bridge.objects, get_run(engine, source_run)
    )
    policy = deepcopy(CARD_ONLY_POLICY)
    policy.update(version=5, card_only=False, enrichment_search_required=True)
    old = bridge.workflow.enqueue(snapshot, _policy=policy)
    bridge.projection.sync(old)
    bridge.tick()
    bridge.reconcile_at = 0
    bridge.tick()
    retained = bridge.start(book_id)
    runs = bridge.workflow.list_runs(book_id)
    assert [run["run_id"] for run in runs] == [old["run_id"]]
    assert retained["run_id"] == old["run_id"] and retained["policy"]["version"] == 5
    assert retained["policy"]["card_only"] is False


def test_retired_link_events_require_legacy_source_proof_before_they_are_consumed(card_book):
    _, engine, bridge, _, source_run, _, _ = card_book
    source = get_run(engine, source_run)
    document = {"id": "d" * 24, "source": {"sha256": "a" * 64}, "tasks": [], "state": "draft"}
    bridge.store.checkpoints.write(document)
    unscoped = {**source["result"], "codex_document_id": document["id"]}
    unscoped.pop("codex_workspace_id")
    from sqlalchemy import insert

    with engine.begin() as connection:
        connection.execute(
            update(db.runs).where(db.runs.c.id == source_run).values(result=unscoped)
        )
        event_id = connection.scalar(
            insert(db.outbox)
            .values(
                run_id=source_run,
                kind="link_research",
                dedup_key="legacy-source-proof",
                payload={"book_id": source["book_id"]},
            )
            .returning(db.outbox.c.id)
        )
    with pytest.raises(WrongWorkspace):
        require_workspace(engine, get_run(engine, source_run), bridge.store)
    bridge.refresh_links()
    with engine.connect() as connection:
        assert (
            connection.scalar(select(db.outbox.c.delivered).where(db.outbox.c.id == event_id))
            is False
        )
        assert (
            connection.scalar(
                select(func.count())
                .select_from(db.events)
                .where(db.events.c.kind == "cards.legacy_link_retired")
            )
            == 0
        )
    document["source"]["sha256"] = source["source"]["sha256"]
    bridge.store.checkpoints.write(document)
    bridge.refresh_links()
    bridge.refresh_links()
    with engine.connect() as connection:
        assert (
            connection.scalar(select(db.outbox.c.delivered).where(db.outbox.c.id == event_id))
            is True
        )
        assert (
            connection.scalar(
                select(func.count())
                .select_from(db.events)
                .where(db.events.c.kind == "cards.legacy_link_retired")
            )
            == 1
        )
    assert (
        get_run(engine, source_run)["result"]["codex_workspace_id"]
        == workspace_identity(bridge.store)["workspace_id"]
    )


def service_app(book):
    settings, engine, bridge, *_ = book
    service = Research(settings, engine)
    service.bridge, service.workflow = bridge, bridge.workflow
    app = create_app(settings, engine)
    app.state.research = service
    return service, app


def test_public_manual_start_and_new_question_writes_are_retired_but_history_is_readable(card_book):
    _, _, bridge, book_id, _, chapter_id, _ = card_book
    notebook = ResearchNotebook(bridge.store)
    old = notebook.create("已保存的问题", [book_id], notes="旧笔记。")
    retained_question = notebook.question(old["id"])
    _, app = service_app(card_book)
    base = f"/api/v2/research/questions/{old['id']}"
    writes = [
        ("post", "/api/v2/research/questions", {"title": "新问题", "book_ids": [book_id]}),
        ("patch", base, {"expected_version": old["version"], "notes": "新写入"}),
        (
            "post",
            base + "/evidence",
            {"expected_version": old["version"], "kind": "source", "target_id": chapter_id},
        ),
        ("patch", base + "/evidence/legacy-selection", {"expected_version": 1, "note": "新写入"}),
        ("post", base + "/research", {"expected_version": old["version"]}),
    ]
    with TestClient(app) as client:
        assert client.post(f"/api/v2/research/books/{book_id}/runs", json={}).status_code == 409
        for method, path, body in writes:
            assert getattr(client, method)(path, json=body).status_code == 410
        history = client.get(base)
        assert history.status_code == 200 and history.json()["notes"] == "旧笔记。"
    assert notebook.question(old["id"]) == retained_question
    assert bridge.workflow.list_runs(book_id) == []


@pytest.fixture
def completed_card(card_book):
    _, _, bridge, book_id, _, _, _ = card_book
    bridge.tick()
    run = bridge.workflow.list_runs(book_id)[0]
    workflow = bridge.workflow
    article = workflow.snapshot(run["run_id"])["articles"][0]
    text = article["text"]
    mentions = []
    findings = []
    for identity, kind, name in (
        ("person", "person", "人物甲"),
        ("document", "document", "Synthetic article"),
    ):
        start = text.index(name)
        mentions.append(
            {
                "id": identity,
                "kind": kind,
                "name": name,
                "identity_key": article["id"] + ":" + identity,
                "identity_basis": "明确的合成原文标题或正文。",
                "evidence_ids": ["original"],
                "locations": [
                    {
                        "article_id": article["id"],
                        "start": start,
                        "end": start + len(name),
                        "quote": name,
                    }
                ],
            }
        )
        findings.append(
            {
                "id": identity,
                "kind": kind,
                "article_id": article["id"],
                "start": start,
                "end": start + len(name),
                "quote": name,
                "description": "保留原文人物或文献。",
            }
        )
    draft = {
        "id": "master",
        "article_id": article["id"],
        "title": article["title"],
        "summary": "合成契约测试：人物甲提出主张。",
        "metadata": DocumentMetadata().model_dump(),
        "evidence": [
            {
                "id": "original",
                "article_id": article["id"],
                "start": 0,
                "end": len(text),
                "quote": text,
                "footnote_ids": [note["id"] for note in article["footnotes"]],
            }
        ],
        "mentions": mentions,
        "items": [
            {
                "id": "claim",
                "kind": "evidence",
                "text": "人物甲提出主张。",
                "evidence_ids": ["original"],
            }
        ],
        "relations": [
            {
                "id": "appears-in",
                "subject_id": "person",
                "predicate": "appears_in",
                "object_id": "document",
                "reason": "人物甲的主张记于这份标题明确的文献。",
                "evidence_ids": ["original"],
            }
        ],
        "coverage": [
            {
                "id": "part-" + str(index),
                "article_id": article["id"],
                "start": part["start"],
                "end": part["end"],
                "summary": "阅读标题、正文与原文注。",
                "mention_ids": ["person", "document"],
                "relation_ids": ["appears-in"],
            }
            for index, part in enumerate(article["parts"])
        ],
    }
    claim_start = text.index("人物甲提出主张。")
    findings.append(
        {
            "id": "claim",
            "kind": "fact",
            "article_id": article["id"],
            "start": claim_start,
            "end": claim_start + len("人物甲提出主张。"),
            "quote": "人物甲提出主张。",
            "description": "正文的重要陈述。",
        }
    )
    for stage in ("author", "verify"):
        worker = "synthetic-" + stage
        packet = workflow.claim(run["run_id"], worker, stage=stage)
        for source in packet["source_index"]:
            workflow.read_source(packet["task_token"], article_id=source["id"])
        if stage == "author":
            result = {"cards": [draft]}
        else:
            candidate = workflow.candidate(
                packet["task_token"],
                worker,
                {
                    "article_id": article["id"],
                    "text_sha256": article["text_sha256"],
                    "summary": "独立阅读合成原文。",
                    "inventory": {
                        "coverage": [
                            {"start": p["start"], "end": p["end"], "summary": "逐段原文。"}
                            for p in article["parts"]
                        ],
                        "findings": findings,
                    },
                },
            )
            result = {
                "decision": "approve",
                **candidate["required_checks"],
                "coverage_assessments": [
                    {
                        "finding_id": item["id"],
                        "status": "covered",
                        "candidate_ids": ["master/" + item["id"]],
                        "reason": "精确的同篇原文证据。",
                    }
                    for item in findings
                ],
            }
        run = workflow.submit(
            packet["task_token"], worker, {"input_hash": packet["input_hash"], **result}
        )
    bridge.projection.sync(run)
    return card_book, workflow.card(run["card_ids"][0])


def test_personal_notes_http_does_not_change_card_approval_model_or_search_projection(
    completed_card,
):
    book, card = completed_card
    _, engine, bridge, _, _, _, _ = book
    _, app = service_app(book)
    native_before = bridge.workflow.card(card["id"])
    with engine.connect() as connection:
        projection_before = connection.execute(
            select(db.research_cards.c.content, db.research_cards.c.search_text).where(
                db.research_cards.c.id == card["id"]
            )
        ).one()
    base = f"/api/v2/research/cards/{card['id']}"
    with TestClient(app) as client:
        assert client.get(base + "/notes").json()["version"] == 0
        saved = client.patch(
            base + "/notes", json={"expected_version": 0, "text": "个人待查想法。"}
        )
        assert saved.status_code == 200 and saved.json()["version"] == 1
        assert (
            client.patch(
                base + "/notes", json={"expected_version": 0, "text": "迟到写入。"}
            ).status_code
            == 409
        )
        detail = client.get(base)
        assert detail.status_code == 200 and detail.json()["human_notes"] == "个人待查想法。"
        assert detail.json()["notes_version"] == 1
        assert (
            detail.json()["state"] == card["state"] and detail.json()["version"] == card["version"]
        )
        draft = client.get(base + "/draft").json()["card"]
        assert "human_notes" not in draft and "个人待查想法。" not in str(draft)
    with engine.connect() as connection:
        projection_after = connection.execute(
            select(db.research_cards.c.content, db.research_cards.c.search_text).where(
                db.research_cards.c.id == card["id"]
            )
        ).one()
    assert projection_before == projection_after
    assert bridge.workflow.card(card["id"]) == native_before


def test_completed_cards_do_not_enter_the_notebook_or_queue_automatic_research_links(
    completed_card,
):
    book, _ = completed_card
    _, engine, bridge, _, _, _, _ = book
    bridge.tick()
    with bridge.workflow.ledger.connect() as connection:
        assert not connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='research_notebook_cards'"
        ).fetchone()
    with engine.connect() as connection:
        assert (
            connection.scalar(
                select(func.count())
                .select_from(db.outbox)
                .where(db.outbox.c.kind == "link_research")
            )
            == 0
        )


@pytest.mark.parametrize("unauthorized", ["deleted", "foreign_workspace"])
def test_notes_reads_and_writes_require_the_cards_source_authorization(
    completed_card, unauthorized
):
    book, card = completed_card
    _, engine, bridge, book_id, source_run, _, _ = book
    _, app = service_app(book)
    with engine.begin() as connection:
        if unauthorized == "deleted":
            connection.execute(
                update(db.books).where(db.books.c.id == book_id).values(state="deleted")
            )
        else:
            result = get_run(engine, source_run)["result"]
            connection.execute(
                update(db.runs)
                .where(db.runs.c.id == source_run)
                .values(
                    result={**result, "codex_workspace_id": "another-workspace"},
                )
            )
    base = f"/api/v2/research/cards/{card['id']}/notes"
    expected = 404 if unauthorized == "deleted" else 409
    with TestClient(app) as client:
        assert client.get(base).status_code == expected
        assert (
            client.patch(base, json={"expected_version": 0, "text": "无权保存。"}).status_code
            == expected
        )
    assert CardNotes(bridge.store).read(card)["version"] == 0


def test_document_entities_remain_entities_in_public_detail_catalogue_and_graph(completed_card):
    book, card = completed_card
    _, _, bridge, _, _, _, _ = book
    document = next(e for e in bridge.workflow.list_entities() if e["kind"] == "document")
    _, app = service_app(book)
    with TestClient(app) as client:
        detail = client.get(f"/api/v2/research/cards/{card['id']}")
        assert detail.status_code == 200
        assert any(m["kind"] == "document" for m in detail.json()["mentions"])
        entity = client.get(f"/api/v2/research/entities/{document['id']}")
        assert entity.status_code == 200 and entity.json()["kind"] == "document"
        catalogue = client.get("/api/v2/research/entities", params={"kind": "document"})
        assert catalogue.status_code == 200
        assert any(item["id"] == entity.json()["id"] for item in catalogue.json()["items"])
        graph = client.get("/api/v2/research/graph", params={"entity_id": document["id"]})
        assert graph.status_code == 200
        nodes = {node["id"]: node for node in graph.json()["nodes"]}
        assert entity.json()["id"] in nodes, graph.json()
        assert nodes[entity.json()["id"]]["kind"] == "document"
        assert nodes[entity.json()["id"]]["name"] == "Synthetic article"
        assert graph.json()["edges"]
