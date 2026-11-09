"""Synthetic contract checks, never an accuracy or content-acceptance report."""

import json
import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, insert, select, update

from hrs.api.main import create_app
from hrs.library.books import get_run
from hrs.library.errors import ServiceError
from hrs.library.events import read_events
from hrs.library.native_workspace import WrongWorkspace
from hrs.library.projection.facts import PROJECTION_POLICY
from hrs.library.projection.publication import NativePublication
from hrs.library.records import text_digest
from hrs.processing.research_workflow import POLICY as CURRENT_CARD_POLICY
from hrs.processing.workspace import identity
from hrs.research.api import Research, ResearchDecision
from hrs.research.bridge import ResearchBridge
from hrs.research.workspace import QuestionCreate, QuestionEvidence, QuestionUpdate
from hrs.storage import models as db


@pytest.fixture
def research_book(platform, monkeypatch):
    from copy import deepcopy

    from hrs.processing import research_workflow

    # These synthetic fixtures exercise retained v4 HTTP/projection contracts,
    # never native v5 coverage or content-accuracy acceptance.
    retained_policy = deepcopy(research_workflow.POLICY)
    retained_policy["version"] = 4
    retained_policy["card_only"] = False
    retained_policy["enrichment_search_required"] = True
    retained_policy["models"] = dict.fromkeys(research_workflow.STAGES, research_workflow.SOL)
    retained_policy["stages"] = {
        stage: {"model": research_workflow.SOL, "reasoning_effort": "high"}
        for stage in research_workflow.STAGES
    }
    retained_policy.pop("complete_research", None)
    monkeypatch.setattr(research_workflow, "POLICY", retained_policy)
    settings, engine = platform
    values = {}

    class Objects:
        writes = []

        def put_bytes(self, content, media_type="application/json", *, run_id=None):
            self.writes.append(bytes(content))
            sha = text_digest(content.decode())
            values[sha] = content
            return {
                "key": "test/" + sha,
                "sha256": sha,
                "byte_length": len(content),
                "media_type": media_type,
            }

        def read_bytes(self, ref):
            return values[ref["sha256"]]

    objects = Objects()
    bridge = ResearchBridge(settings, engine, objects=objects)
    book_id, run_id, chapter_id = (str(uuid4()) for _ in range(3))
    text = "# Synthetic article\n\n人物甲提出主张。[^1]\n\n[^1]: 属于原文的注释。\n"
    publication = text_digest("synthetic contract publication")
    content = objects.put_bytes(
        json.dumps(
            {
                "parts": [
                    {
                        "span_id": "span",
                        "start": 900,
                        "end": 900 + len(text),
                        "text": text,
                        "source": {
                            "pages": [4],
                            "page": 4,
                            "original_start": 35,
                            "original_end": 35 + len(text),
                            "image_sha256": "a" * 64,
                            "markdown_sha256": "b" * 64,
                        },
                    }
                ],
                "bibliography": {"status": "unclaimed"},
                "structure": [],
                "acceptance": {
                    "content_acceptance": "machine_or_human",
                    "publication": {"markdown_sha256": publication, "approval_type": "machine"},
                },
            },
            ensure_ascii=False,
        ).encode()
    )
    with engine.begin() as connection:
        connection.execute(
            insert(db.books).values(id=book_id, title="Synthetic contract book", state="ready")
        )
        connection.execute(
            insert(db.runs).values(
                id=run_id,
                book_id=book_id,
                state="completed",
                stage="complete",
                source={"sha256": "c" * 64},
                result={
                    "codex_workspace_id": identity(bridge.store)["workspace_id"],
                    "codex_document_id": "synthetic-document",
                    "published": True,
                    "content_acceptance": "machine_or_human",
                    "codex_publication_sha256": publication,
                    "codex_reading_projection": {"chapters_sha256": "d" * 64},
                    "codex_indexed_publication_sha256": publication,
                    "codex_indexed_chapters_sha256": "d" * 64,
                },
            )
        )
        connection.execute(
            insert(db.chapters).values(
                id=chapter_id,
                book_id=book_id,
                run_id=run_id,
                position=0,
                title="Synthetic article",
                kind="article",
                content=content,
                pages=[4],
                codepoints=len(text),
            )
        )
    return settings, engine, bridge, book_id, run_id, chapter_id, text


def retained_research_run(bridge, book_id, *, purpose, scope, questions=(), targets=()):
    """Seed a previously frozen legacy run without reopening retired public entry points."""
    from hrs.research.sources import publication_snapshot

    snapshot = publication_snapshot(
        bridge.settings, bridge.engine, bridge.store, bridge.objects, bridge.source_run(book_id)
    )
    snapshot.update(
        research_scope=scope,
        research_questions=list(questions),
        research_questions_omitted=0,
        link_target_cards=list(targets),
    )
    return bridge.workflow.enqueue(snapshot, purpose=purpose)


def test_indexed_reconciliation_is_durable_idempotent_and_freezes_full_article(research_book):
    _, engine, bridge, book_id, source_run, chapter_id, text = research_book
    bridge.tick()
    bridge.reconcile_at = 0
    bridge.tick()
    runs = bridge.workflow.list_runs(book_id)
    assert len(runs) == 1
    snapshot = bridge.workflow.snapshot(runs[0]["run_id"])
    article = snapshot["articles"][0]
    assert article["id"] == chapter_id and article["text"] == text
    assert article["parts"][0]["start"] == 0
    assert article["parts"][0]["source"]["publication_start"] == 900
    assert article["parts"][0]["source"]["original_start"] == 35
    assert article["footnotes"] and article["text_sha256"] == text_digest(text)
    with engine.connect() as connection:
        assert (
            connection.scalar(
                select(func.count())
                .select_from(db.outbox)
                .where(db.outbox.c.kind == "start_research")
            )
            == 1
        )
        assert (
            connection.scalar(
                select(db.outbox.c.delivered).where(db.outbox.c.kind == "start_research")
            )
            is True
        )
        assert (
            connection.scalar(
                select(func.count())
                .select_from(db.outbox)
                .where(db.outbox.c.kind == "index_research")
            )
            == 1
        )
        assert (
            connection.scalar(select(db.runs.c.state).where(db.runs.c.id == source_run))
            == "completed"
        )
        assert connection.scalar(select(db.research_runs.c.id)) == runs[0]["run_id"]


def test_research_multi_book_scope_is_one_authorized_union(research_book):
    from hrs.research.sources import ResearchEvidence

    settings, engine, _, book_id, source_run, chapter_id, _ = research_book
    ids = []
    with engine.begin() as connection:
        original = (
            connection.execute(select(db.runs).where(db.runs.c.id == source_run)).mappings().one()
        )
        chapter = (
            connection.execute(select(db.chapters).where(db.chapters.c.id == chapter_id))
            .mappings()
            .one()
        )
        for number in range(2):
            other_book, other_run, other_chapter = (str(uuid4()) for _ in range(3))
            connection.execute(
                insert(db.books).values(id=other_book, title=f"Scope {number}", state="ready")
            )
            connection.execute(
                insert(db.runs).values(**{**original, "id": other_run, "book_id": other_book})
            )
            connection.execute(
                insert(db.chapters).values(
                    **{**chapter, "id": other_chapter, "run_id": other_run, "book_id": other_book}
                )
            )
            ids.append((other_book, other_chapter))
    evidence = ResearchEvidence(settings, engine)
    assert evidence.article_scope(book_ids=[book_id, ids[0][0]]) == {chapter_id, ids[0][1]}
    assert evidence.article_scope(book_ids=[str(uuid4())]) == set()


def test_retained_question_http_preserves_evidence_and_rejects_new_writes(research_book):
    settings, engine, bridge, book_id, _, _, _ = research_book
    _, card = finish_contract_run(bridge, bridge.start(book_id), "Selected source card")
    service = Research(settings, engine)
    service.bridge, service.workflow = bridge, bridge.workflow
    question = service.workspace.create_question(
        QuestionCreate(title="What does this source support?", book_ids=[book_id])
    )
    assert question["selections"] == question["findings"] == question["runs"] == []
    chosen = service.workspace.select_evidence(
        question["id"],
        QuestionEvidence(
            expected_version=question["version"],
            kind="card",
            target_id=card["id"],
            decision="adopted",
        ),
    )
    app = create_app(settings, engine)
    app.state.research = service
    with TestClient(app) as client:
        retired = client.post(
            "/api/v2/research/questions",
            json={"title": "What does this source support?", "book_ids": [book_id]},
        )
        assert retired.status_code == 410
        selected = client.get(f"/api/v2/research/questions/{question['id']}").json()["selections"][
            0
        ]
        assert selected["evidence"][0]["quote"] == "人物甲"
        assert selected["evidence"][0]["text_sha256"]
        summary = client.get("/api/v2/research/questions").json()["items"][0]
        assert (
            summary["selection_count"] == 1
            and summary["finding_count"] == 0
            and summary["run_count"] == 0
        )
        assert "selections" not in summary and "findings" not in summary and "runs" not in summary
        assert (
            client.patch(
                f"/api/v2/research/questions/{question['id']}",
                json={
                    "expected_version": question["version"],
                    "notes": "stale write",
                },
            ).status_code
            == 410
        )
        for method, path, body in (
            ("post", "/evidence", {"kind": "card", "target_id": card["id"]}),
            ("patch", f"/evidence/{selected['id']}", {"decision": "excluded"}),
            ("post", "/research", {}),
        ):
            response = client.request(
                method,
                f"/api/v2/research/questions/{question['id']}{path}",
                json={"expected_version": chosen["version"], **body},
            )
            assert response.status_code == 410
        assert client.get(f"/api/v2/research/questions/{question['id']}").json() == chosen
    with pytest.raises(ServiceError) as conflict:
        service.workspace.update_question(
            question["id"],
            QuestionUpdate(expected_version=question["version"], notes="stale write"),
        )
    assert conflict.value.status_code == 409


def test_complete_card_selection_keeps_all_evidence_and_marks_only_changed_derived_record(
    research_book,
):
    settings, engine, bridge, book_id, _, chapter_id, text = research_book
    start = text.index("人物甲")
    evidence = [
        {
            "id": f"e{index}",
            "article_id": chapter_id,
            "start": start,
            "end": start + 3,
            "quote": "人物甲",
        }
        for index in range(133)
    ]
    _, card = finish_contract_run(
        bridge, bridge.start(book_id), "Complete selection", draft_patch={"evidence": evidence}
    )
    service = Research(settings, engine)
    service.bridge, service.workflow = bridge, bridge.workflow
    question = service.workspace.create_question(
        QuestionCreate(title="Complete reviewed source", book_ids=[book_id])
    )
    saved = service.workspace.select_evidence(
        question["id"],
        QuestionEvidence(
            expected_version=question["version"],
            kind="card",
            target_id=card["id"],
            decision="adopted",
        ),
    )
    assert len(saved["selections"][0]["evidence"]) == 133
    assert saved["selections"][0]["record_ref"]["version"] == card["version"]
    explicit = service.workspace.select_evidence(
        question["id"],
        QuestionEvidence(
            expected_version=saved["version"],
            kind="card",
            target_id=card["id"],
            evidence_ids=[f"e{index}" for index in range(100)],
        ),
    )
    assert sorted(len(item["evidence"]) for item in explicit["selections"]) == [100, 133]
    app = create_app(settings, engine)
    app.state.research = service
    with TestClient(app) as client:
        assert (
            client.post(
                f"/api/v2/research/questions/{question['id']}/evidence",
                json={
                    "expected_version": explicit["version"],
                    "kind": "card",
                    "target_id": card["id"],
                    "evidence_ids": [f"e{index}" for index in range(101)],
                },
            ).status_code
            == 422
        )
        approved = client.post(
            f"/api/v2/research/cards/{card['id']}/decision",
            json={"action": "accept", "expected_version": card["version"]},
        )
        assert approved.status_code == 200
        retained = client.get(f"/api/v2/research/questions/{question['id']}").json()
        assert all(item["record_current"] is False for item in retained["selections"])
        assert all(
            span["source_current"] for item in retained["selections"] for span in item["evidence"]
        )
        assert (
            next(item for item in retained["selections"] if len(item["evidence"]) == 133)[
                "decision"
            ]
            == "adopted"
        )


@pytest.mark.parametrize("change", [{"published": False}, {"content_acceptance": "workflow_test"}])
def test_unpublished_or_workflow_only_sources_cannot_authorize_current_research_evidence(
    research_book, change
):
    settings, engine, bridge, book_id, source_run, chapter_id, _ = research_book
    _, card = finish_contract_run(bridge, bridge.start(book_id), "Source acceptance boundary")
    service = Research(settings, engine)
    service.bridge, service.workflow = bridge, bridge.workflow
    assert service.card(card["id"])["source_current"]
    with engine.begin() as connection:
        original = connection.scalar(select(db.runs.c.result).where(db.runs.c.id == source_run))
        connection.execute(
            update(db.runs).where(db.runs.c.id == source_run).values(result={**original, **change})
        )
    assert not service.card(card["id"])["source_current"]
    assert service.workspace._source_metadata(chapter_id) is None
    with pytest.raises(ServiceError):
        service.workspace.current_card(card["id"])


def test_graph_drops_stale_owner_edges_after_entities_merge_into_current_books(research_book):
    from hrs.research.workspace import IdentityDecision

    settings, engine, bridge, book_id, source_run, chapter_id, text = research_book
    start = text.index("人物甲提出主张")
    phrase = "人物甲提出主张"
    event = {
        "id": "event",
        "name": "提出主张",
        "description": phrase,
        "participant_ids": ["person"],
        "evidence_ids": ["e2"],
    }
    patch = named_patch(chapter_id, text)
    patch["evidence"] = [
        {"id": "e1", "article_id": chapter_id, "start": start, "end": start + 3, "quote": "人物甲"},
        {
            "id": "e2",
            "article_id": chapter_id,
            "start": start,
            "end": start + len(phrase),
            "quote": phrase,
        },
    ]
    patch["events"] = [event]
    _, old_card = finish_contract_run(
        bridge, bridge.start(book_id), "Old owner A", draft_patch=patch
    )
    book_b, article_b = later_source(engine, source_run, chapter_id)
    _, card_b = finish_contract_run(
        bridge, bridge.start(book_b), "Current person B", draft_patch=named_patch(article_b, text)
    )
    book_c, article_c = later_source(engine, source_run, chapter_id)
    _, card_c = finish_contract_run(
        bridge,
        bridge.start(book_c),
        "Current event C",
        draft_patch={
            "events": [{**event, "participant_ids": [], "evidence_ids": ["e1"]}],
            "evidence": [
                {
                    "id": "e1",
                    "article_id": article_c,
                    "start": start,
                    "end": start + len(phrase),
                    "quote": phrase,
                }
            ],
        },
    )
    service = Research(settings, engine)
    service.bridge, service.workflow = bridge, bridge.workflow

    def merge(source_card, source_kind, target_card):
        left = next(
            entity
            for entity in bridge.workflow.list_entities(run_id=source_card["run_id"])
            if entity["kind"] == source_kind
        )
        right = next(
            entity
            for entity in bridge.workflow.list_entities(run_id=target_card["run_id"])
            if entity["kind"] == source_kind
        )
        source = service.entity(left["canonical_id"])
        target = service.entity(right["canonical_id"])
        return service.workspace.identity_decision(
            source["id"],
            IdentityDecision(
                action="merge",
                expected_version=source["version"],
                target_id=target["id"],
                target_version=target["version"],
                evidence_refs=[
                    {
                        "card_id": source_card["id"],
                        "card_version": source_card["version"],
                        "evidence_id": "e1" if source_kind == "person" else "e2",
                    },
                    {
                        "card_id": target_card["id"],
                        "card_version": target_card["version"],
                        "evidence_id": "e1",
                    },
                ],
            ),
        )

    person = merge(old_card, "person", card_b)
    merge(old_card, "event", card_c)
    assert any(
        edge["card_id"] == old_card["id"] for edge in service.workspace.graph(person["id"])["edges"]
    )
    with engine.begin() as connection:
        original = connection.scalar(select(db.runs.c.result).where(db.runs.c.id == source_run))
        connection.execute(
            update(db.runs)
            .where(db.runs.c.id == source_run)
            .values(result={**original, "codex_publication_sha256": "f" * 64})
        )
    graph = service.workspace.graph(person["id"])
    assert all(edge["card_id"] != old_card["id"] for edge in graph["edges"])
    for node in graph["nodes"]:
        assert all(
            mention["card_id"] != old_card["id"] for mention in node.get("source_mentions", [])
        )
        assert all(
            ref["card_id"] != old_card["id"]
            for alias in node.get("aliases", [])
            for ref in alias["evidence_refs"]
        )


def test_retired_question_start_preserves_legacy_authorized_scope_without_retrieval(
    research_book, monkeypatch
):
    settings, engine, bridge, book_id, source_run, chapter_id, _ = research_book
    foreign_book, foreign_run = str(uuid4()), str(uuid4())
    with engine.begin() as connection:
        source = (
            connection.execute(select(db.runs).where(db.runs.c.id == source_run)).mappings().one()
        )
        connection.execute(
            insert(db.books).values(id=foreign_book, title="Other workspace", state="ready")
        )
        connection.execute(
            insert(db.runs).values(
                **{
                    **source,
                    "id": foreign_run,
                    "book_id": foreign_book,
                    "result": {**source["result"], "codex_workspace_id": "foreign-workspace"},
                }
            )
        )
    service = Research(settings, engine)
    service.bridge, service.workflow = bridge, bridge.workflow
    calls = []

    def retrieve(query, **options):
        calls.append(options)
        raise AssertionError("Retired question starts must not invoke retrieval")

    monkeypatch.setattr(service.evidence, "search", retrieve)
    assert service.workspace.available_books() == [book_id]
    created = service.workspace.create_question(QuestionCreate(title="All current sources"))
    scope = {
        "kind": "question",
        "book_ids": [book_id],
        "question_id": created["id"],
        "definition_version": created["definition_version"],
        "selection": "workspace_all",
    }
    frozen_question = service.workspace.notebook.research_context(question_id=created["id"])[
        "questions"
    ][0]
    legacy = retained_research_run(
        bridge, book_id, purpose="question", scope=scope, questions=[frozen_question]
    )
    bridge.projection.sync(legacy)
    service.workspace.notebook.attach_runs(created["id"], created["version"], [legacy["run_id"]])
    bridge.tick()
    app = create_app(settings, engine)
    app.state.research = service
    with TestClient(app) as client:
        response = client.post(
            f"/api/v2/research/questions/{created['id']}/research",
            json={"expected_version": created["version"]},
        )
        assert response.status_code == 410, response.text
        retained = client.get(f"/api/v2/research/questions/{created['id']}").json()
        assert retained["book_ids"] == []
        assert retained["runs"][0]["book_id"] == book_id
        summary = client.get("/api/v2/research/questions").json()["items"][0]
        assert summary["run_count"] == 1 and "runs" not in summary
    assert calls == []
    runs = bridge.workflow.list_runs(book_id)
    question_run = next(run for run in runs if run.get("purpose") == "question")
    frozen = bridge.workflow.snapshot(question_run["run_id"])
    assert frozen["research_scope"]["book_ids"] == [book_id]
    assert frozen["research_scope"]["selection"] == "workspace_all"
    assert frozen["research_questions"][0]["id"] == created["id"]
    assert len([run for run in runs if run.get("purpose") == "question"]) == 1


def test_source_hits_advertise_only_current_verified_card_targets(research_book, monkeypatch):
    from types import SimpleNamespace

    from hrs.research import sources

    settings, engine, bridge, book_id, _, chapter_id, text = research_book
    _, card = finish_contract_run(bridge, bridge.start(book_id), "A target without named people")

    class Retrieval:
        def __init__(self, *args):
            self.client = SimpleNamespace(indices=SimpleNamespace(exists=lambda **kw: True))

        def search(self, *args, **kwargs):
            return [{"id": "synthetic-hit", "chapter_id": chapter_id}]

    monkeypatch.setattr(sources, "Search", Retrieval)
    adapter = sources.ResearchEvidence(settings, engine)
    hits = adapter.search("target")["hits"]
    assert hits[0]["research_cards"][0]["id"] == card["id"]
    assert hits[0]["research_cards"][0]["version"] == card["version"]
    with engine.begin() as connection:
        connection.execute(
            update(db.books).where(db.books.c.id == book_id).values(state="deleting")
        )
    adapter._attach_card_targets(hits)
    assert hits[0]["research_cards"] == []


def named_patch(chapter_id, text):
    start = text.index("人物甲")
    return {
        "mentions": [
            {
                "id": "person",
                "kind": "person",
                "name": "人物甲",
                "identity_basis": "Explicit synthetic source name",
                "evidence_ids": ["e1"],
                "locations": [
                    {"article_id": chapter_id, "start": start, "end": start + 3, "quote": "人物甲"}
                ],
            }
        ]
    }


def later_source(engine, source_run, chapter_id):
    book_id, run_id, article_id = (str(uuid4()) for _ in range(3))
    with engine.begin() as connection:
        source = (
            connection.execute(select(db.runs).where(db.runs.c.id == source_run)).mappings().one()
        )
        article = (
            connection.execute(select(db.chapters).where(db.chapters.c.id == chapter_id))
            .mappings()
            .one()
        )
        connection.execute(
            insert(db.books).values(id=book_id, title="Later question source", state="ready")
        )
        connection.execute(insert(db.runs).values(**{**source, "id": run_id, "book_id": book_id}))
        connection.execute(
            insert(db.chapters).values(
                **{**article, "id": article_id, "run_id": run_id, "book_id": book_id}
            )
        )
    return book_id, article_id


@pytest.mark.parametrize("human", [False, True])
def test_retained_link_refresh_keeps_frozen_questions_and_notebook_findings(research_book, human):
    from hrs.research.notebook import ResearchNotebook
    from hrs.research.projection import located_card

    settings, engine, bridge, book_id, source_run, chapter_id, text = research_book
    notebook = ResearchNotebook(bridge.store)
    question = notebook.create("What does the named source establish?", [])
    finding = {
        "id": "finding",
        "question_id": question["id"],
        "definition_version": 1,
        "text": "Synthetic source-bound statement",
        "status": "partial",
        "evidence_ids": ["e1"],
        "unresolved": ["Further proof remains unavailable"],
    }
    initial, original = finish_contract_run(
        bridge,
        bridge.start(book_id),
        "Original question card",
        draft_patch={**named_patch(chapter_id, text), "question_findings": [finding]},
    )
    service = Research(settings, engine)
    service.bridge, service.workflow = bridge, bridge.workflow
    if human:
        original = service.decide(
            original["id"], ResearchDecision(action="accept", expected_version=original["version"])
        )
    notebook.sync_card(
        located_card(
            bridge.workflow.card(original["id"]), bridge.workflow.snapshot(initial["run_id"])
        )
    )
    other_book, other_article = later_source(engine, source_run, chapter_id)
    _, counterpart = finish_contract_run(
        bridge,
        bridge.start(other_book),
        "Later question card",
        draft_patch=named_patch(other_article, text),
    )
    with pytest.raises(ServiceError) as retired:
        bridge.enqueue_links(other_book)
    assert retired.value.status_code == 410
    assert not any(run.get("purpose") == "link_refresh" for run in bridge.workflow.list_runs())
    refresh = retained_research_run(
        bridge,
        book_id,
        purpose="link_refresh",
        scope={
            "kind": "link_refresh",
            "source_card_id": original["id"],
            "source_card_version": original["version"],
            "book_ids": sorted([book_id, other_book]),
        },
        questions=notebook.research_context(book_ids=[book_id, other_book])["questions"],
        targets=[
            {
                "card_id": counterpart["id"],
                "version": counterpart["version"],
                "title": counterpart["title"],
                "publication_sha256": counterpart["publication_sha256"],
            }
        ],
    )
    snapshot = bridge.workflow.snapshot(refresh["run_id"])
    assert snapshot["research_questions"][0]["id"] == question["id"]
    assert snapshot["research_questions"][0]["book_ids"] == []
    assert snapshot["research_questions"][0]["definition_version"] == question["definition_version"]
    _, replacement = finish_contract_run(
        bridge,
        refresh,
        "Question-linked refresh",
        counterpart=counterpart,
        draft_patch={"question_findings": [finding]},
    )
    notebook.sync_card(
        located_card(replacement, snapshot, bridge.workflow.sources(refresh["run_id"]))
    )
    retained = service.workspace.question(question["id"])
    original_finding = next(
        item for item in retained["findings"] if item["card_id"] == original["id"]
    )
    new_finding = next(
        item for item in retained["findings"] if item["card_id"] == replacement["id"]
    )
    assert new_finding["card_current"] and new_finding["definition_current"]
    assert original_finding["card_current"] is human
    assert notebook.question(question["id"])["book_ids"] == []
    if human:
        assert bridge.workflow.card(original["id"])["human_decision"] == original["human_decision"]
        assert (
            replacement["linked_card_id"] == original["id"] and not replacement["replaces_card_id"]
        )
    else:
        assert replacement["replaces_card_id"] == original["id"]


def test_new_cards_do_not_inject_notebook_context_or_rewrite_frozen_question_scope(research_book):
    from hrs.research.notebook import ResearchNotebook

    _, _, bridge, book_id, _, _, _ = research_book
    notebook = ResearchNotebook(bridge.store)
    first = notebook.create("Original global question", [])
    frozen_questions = notebook.research_context(book_ids=[book_id])["questions"]
    legacy = retained_research_run(
        bridge,
        book_id,
        purpose="question",
        scope={"kind": "question", "book_ids": [book_id], "question_id": first["id"]},
        questions=frozen_questions,
    )
    notebook.update(first["id"], first["version"], title="Current global question definition")
    for index in range(12):
        notebook.create(f"Other active question {index}", [])
    current = bridge.start(book_id)
    snapshot = bridge.workflow.snapshot(current["run_id"])
    assert not snapshot.get("research_questions")
    assert not snapshot.get("research_scope")
    bridge.tick()
    assert bridge.workflow.snapshot(legacy["run_id"])["research_questions"] == frozen_questions
    assert frozen_questions[0]["definition_version"] == 1
    assert notebook.question(first["id"])["definition_version"] == 2


def test_notebook_syncs_http_decisions_and_controls_even_when_projection_is_current(research_book):
    from hrs.research.notebook import ResearchNotebook

    settings, engine, bridge, book_id, _, chapter_id, text = research_book
    service = Research(settings, engine)
    service.bridge, service.workflow = bridge, bridge.workflow
    notebook = ResearchNotebook(bridge.store)
    question = notebook.create("A pending finding needs a human decision", [book_id])
    status, card = finish_contract_run(
        bridge,
        bridge.start(book_id),
        "Pending question",
        pending=True,
        draft_patch={
            "question_findings": [
                {
                    "id": "finding",
                    "question_id": question["id"],
                    "definition_version": 1,
                    "text": "Synthetic statement awaiting review",
                    "status": "partial",
                    "evidence_ids": ["e1"],
                    "unresolved": [],
                }
            ]
        },
    )
    bridge.tick()
    assert notebook.question(question["id"])["findings"] == []
    app = create_app(settings, engine)
    app.state.research = service
    with TestClient(app) as client:
        approved = client.post(
            f"/api/v2/research/cards/{card['id']}/decision",
            json={"action": "accept", "expected_version": card["version"]},
        )
        assert approved.status_code == 200
        assert (
            bridge.projection.versions()[status["run_id"]]
            == bridge.workflow.status(status["run_id"])["version"]
        )
        bridge.tick()
        assert len(notebook.question(question["id"])["findings"]) == 1
        current_question = notebook.question(question["id"])
        scope = {
            "kind": "question",
            "book_ids": [book_id],
            "question_id": question["id"],
            "definition_version": 1,
        }
        pending_run = retained_research_run(
            bridge,
            book_id,
            purpose="question",
            scope=scope,
            questions=notebook.research_context(question_id=question["id"])["questions"],
        )
        notebook.attach_runs(question["id"], current_question["version"], [pending_run["run_id"]])
        for action in ("pause", "resume"):
            response = client.post(f"/api/v2/research/runs/{pending_run['run_id']}/{action}")
            assert response.status_code == 200
            bridge.tick()
            assert notebook.question(question["id"])["runs"][0]["state"] == response.json()["state"]
        packet = bridge.workflow.claim(
            pending_run["run_id"], "synthetic-count-progress", stage="author"
        )
        bridge.projection.sync(bridge.workflow.status(pending_run["run_id"]))
        bridge.tick()
        before = notebook.question(question["id"])["version"]
        for source in packet["source_index"]:
            bridge.workflow.read_source(packet["task_token"], article_id=source["id"])
        updated = bridge.workflow.submit(
            packet["task_token"],
            "synthetic-count-progress",
            {
                "input_hash": packet["input_hash"],
                "cards": [
                    {
                        "id": "draft",
                        "article_id": chapter_id,
                        "title": "Synthetic progress",
                        "summary": "Counts only; no new user decision",
                        "evidence": [
                            {
                                "id": "e1",
                                "article_id": chapter_id,
                                "start": text.index("人物甲"),
                                "end": text.index("人物甲") + 3,
                                "quote": "人物甲",
                            }
                        ],
                    }
                ],
            },
        )
        assert updated["state"] == "running"
        bridge.projection.sync(updated)
        bridge.tick()
        assert notebook.question(question["id"])["version"] == before
        restarted = ResearchBridge(settings, engine, store=bridge.store, objects=bridge.objects)
        restarted.tick()
        assert notebook.question(question["id"])["version"] == before


def test_alias_decision_refreshes_durable_projection_and_authorized_hybrid_index(
    research_book, monkeypatch
):
    from hrs.research.discovery import ResearchDiscovery
    from hrs.research.workspace import IdentityDecision
    from hrs.search.indexing import BookIndexer

    settings, engine, bridge, book_id, source_run, chapter_id, text = research_book
    status, card = finish_contract_run(
        bridge, bridge.start(book_id), "Named source", draft_patch=named_patch(chapter_id, text)
    )
    service = Research(settings, engine)
    service.bridge, service.workflow = bridge, bridge.workflow
    entity_id = bridge.workflow.list_entities(run_id=status["run_id"])[0]["canonical_id"]
    entity = service.entity(entity_id)
    with engine.connect() as connection:
        before = connection.scalar(
            select(db.research_runs.c.summary).where(db.research_runs.c.id == status["run_id"])
        )
    result = service.workspace.identity_decision(
        entity_id,
        IdentityDecision(
            action="alias",
            expected_version=entity["version"],
            alias="甲先生唯一别名",
            evidence_refs=[
                {"card_id": card["id"], "card_version": card["version"], "evidence_id": "e1"}
            ],
        ),
    )
    assert any(alias["name"] == "甲先生唯一别名" for alias in result["aliases"])
    assert service.evidence.authorized_entity_ids([entity_id]) == {entity_id}
    graph = service.workspace.graph(entity_id)
    assert entity_id in {node["id"] for node in graph["nodes"]}, graph
    with engine.connect() as connection:
        summary = connection.scalar(
            select(db.research_runs.c.summary).where(db.research_runs.c.id == status["run_id"])
        )
        projected = connection.scalar(
            select(db.research_entities.c.summary).where(
                db.research_entities.c.run_id == status["run_id"]
            )
        )
        assert "甲先生唯一别名" in json.dumps(projected, ensure_ascii=False)
        assert summary["discovery_input_sha256"] != before["discovery_input_sha256"]
        assert (
            connection.scalar(
                select(func.count())
                .select_from(db.outbox)
                .where(db.outbox.c.kind == "index_research", db.outbox.c.run_id == source_run)
            )
            == 3
        )
    settings = settings.model_copy(
        update={
            "opensearch_url": "http://127.0.0.1:19269",
            "opensearch_index": "test-alias-" + uuid4().hex,
        }
    )
    discovery = ResearchDiscovery(settings, engine, bridge.objects, bridge.store)
    monkeypatch.setattr(
        BookIndexer,
        "embed_cached",
        lambda self, run_id, records, metrics: {
            "chunks": [dict(record, vector=[1.0] + [0.0] * 1023) for record in records],
            "model": {"synthetic_contract_only": True},
        },
    )
    try:
        discovery.index(status["run_id"], summary["discovery_input_sha256"])
        found = discovery.search("甲先生唯一别名", semantic=False, book_ids=[book_id])
        assert any(
            row["record_id"] == entity_id and row["record_kind"] == "entity"
            for row in found["records"]
        )
    finally:
        if discovery.client.indices.exists(index=discovery.name):
            discovery.client.indices.delete(index=discovery.name)


def test_reading_annotations_bind_exact_quote_version_and_canonical_entity(
    research_book, monkeypatch
):
    from hrs.library.catalog import Library

    settings, engine, bridge, book_id, source_run, chapter_id, text = research_book
    _, card = finish_contract_run(
        bridge, bridge.start(book_id), "Reader source", draft_patch=named_patch(chapter_id, text)
    )
    service = Research(settings, engine)
    service.bridge, service.workflow = bridge, bridge.workflow
    monkeypatch.setattr(
        Library,
        "chapter",
        lambda self, cid: {
            "id": chapter_id,
            "book_id": book_id,
            "run_id": source_run,
            "text": text,
            "text_sha256": text_digest(text),
        },
    )
    result = service.workspace.reading(chapter_id, expected_sha256=text_digest(text))
    assert result["source_current"] and result["annotations"]
    annotation = result["annotations"][0]
    assert text[annotation["start"] : annotation["end"]] == annotation["quote"] == "人物甲"
    assert annotation["entity_id"].startswith("entity-")
    assert annotation["card_id"] == card["id"]
    with pytest.raises(ServiceError, match="阅读原文已换版"):
        service.workspace.reading(chapter_id, expected_sha256="0" * 64)


def test_new_book_completion_retires_auto_links_and_preserves_legacy_two_sided_evidence(
    research_book,
):
    settings, engine, bridge, book_id, source_run, chapter_id, text = research_book
    _, original = finish_contract_run(
        bridge, bridge.start(book_id), "Original source", draft_patch=named_patch(chapter_id, text)
    )
    service = Research(settings, engine)
    service.bridge, service.workflow = bridge, bridge.workflow
    original = service.decide(
        original["id"], ResearchDecision(action="accept", expected_version=original["version"])
    )
    other_book, other_run, other_chapter = (str(uuid4()) for _ in range(3))
    with engine.begin() as connection:
        source = (
            connection.execute(select(db.runs).where(db.runs.c.id == source_run)).mappings().one()
        )
        chapter = (
            connection.execute(select(db.chapters).where(db.chapters.c.id == chapter_id))
            .mappings()
            .one()
        )
        connection.execute(
            insert(db.books).values(id=other_book, title="Later arrival", state="ready")
        )
        connection.execute(
            insert(db.runs).values(**{**source, "id": other_run, "book_id": other_book})
        )
        connection.execute(
            insert(db.chapters).values(
                **{**chapter, "id": other_chapter, "run_id": other_run, "book_id": other_book}
            )
        )
    _, second = finish_contract_run(
        bridge,
        bridge.start(other_book),
        "Later source",
        draft_patch=named_patch(other_chapter, text),
    )
    bridge.refresh_links()
    links = [run for run in bridge.workflow.list_runs() if run.get("purpose") == "link_refresh"]
    assert links == []
    with engine.begin() as connection:
        assert (
            connection.scalar(
                select(func.count())
                .select_from(db.outbox)
                .where(db.outbox.c.kind == "link_research")
            )
            == 0
        )
        connection.execute(
            insert(db.outbox).values(
                dedup_key="retained-link-" + uuid4().hex,
                run_id=source_run,
                kind="link_research",
                payload={"book_id": book_id},
            )
        )
    refresh = retained_research_run(
        bridge,
        book_id,
        purpose="link_refresh",
        scope={
            "kind": "link_refresh",
            "source_card_id": original["id"],
            "source_card_version": original["version"],
            "book_ids": sorted([book_id, other_book]),
        },
        targets=[
            {
                "card_id": second["id"],
                "version": second["version"],
                "title": second["title"],
                "publication_sha256": second["publication_sha256"],
            }
        ],
    )
    old = bridge.workflow.card(original["id"])
    assert old["state"] == "human_accepted" and old["human_decision"] == original["human_decision"]
    frozen = bridge.workflow.snapshot(refresh["run_id"])
    assert frozen["link_target_cards"][0]["card_id"] == second["id"]
    assert len(frozen["articles"]) == 1
    bridge.refresh_links()
    bridge.refresh_links()
    assert len([r for r in bridge.workflow.list_runs() if r.get("purpose") == "link_refresh"]) == 1
    with engine.connect() as connection:
        assert (
            connection.scalar(
                select(db.outbox.c.delivered).where(db.outbox.c.kind == "link_research")
            )
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
    _, package = finish_contract_run(
        bridge, refresh, "Independent relation package", counterpart=second
    )
    retained = service.card(original["id"])
    assert (
        retained["human_decision"] == original["human_decision"]
        and retained["version"] == original["version"]
    )
    edge = retained["derived_relations"]["items"][0]
    assert edge["source_id"] == original["id"] and edge["target_id"] == second["id"]
    assert edge["card_id"] == package["id"] != original["id"]
    assert {span["book_id"] for span in edge["evidence"]} == {book_id, other_book}
    assert {span["reading_target"]["chapter_id"] for span in edge["evidence"]} == {
        chapter_id,
        other_chapter,
    }
    assert service.card(second["id"])["derived_relations"]["items"][0]["id"] == edge["id"]
    question = service.workspace.create_question(
        QuestionCreate(title="Select both retained sides", book_ids=[book_id, other_book])
    )
    selected = service.workspace.select_evidence(
        question["id"],
        QuestionEvidence(
            expected_version=question["version"],
            kind="relation",
            target_id=edge["id"],
            decision="adopted",
        ),
    )
    evidence = selected["selections"][0]["evidence"]
    assert {span["book_id"] for span in evidence} == {book_id, other_book}
    assert all(span["source_current"] for span in evidence)
    assert "后续补充的双向关系" in service.export(original["id"])


def test_structured_card_revision_replaces_only_its_article_after_independent_review(
    research_book, monkeypatch
):
    from hrs.research.discovery import ResearchDiscovery
    from hrs.research.models import CardDraft
    from hrs.research.workspace import CardRevision
    from hrs.search.indexing import BookIndexer

    settings, engine, bridge, book_id, _, chapter_id, _ = research_book
    second_id = str(uuid4())
    with engine.begin() as connection:
        article = (
            connection.execute(select(db.chapters).where(db.chapters.c.id == chapter_id))
            .mappings()
            .one()
        )
        connection.execute(
            insert(db.chapters).values(
                **{**article, "id": second_id, "position": 1, "title": "Second article"}
            )
        )
    initial = bridge.start(book_id)
    initial, first = finish_contract_run(bridge, initial, "LegacyOnlyMarker")
    initial, second = finish_contract_run(bridge, initial, "Second article")
    assert initial["state"] == "completed"
    service = Research(settings, engine)
    service.bridge, service.workflow = bridge, bridge.workflow
    draft = CardDraft.model_validate(bridge.workflow.card_draft(first["id"]))
    draft.summary = "User structured revision retained for review"
    draft.title = "RevisedOnlyMarker"
    revised_run = service.workspace.card_revision(
        first["id"], CardRevision(expected_version=first["version"], card=draft)
    )
    assert {row["id"] for row in service.records(db.research_cards, book_id)["items"]} == {
        first["id"],
        second["id"],
    }
    packet = bridge.workflow.claim(
        revised_run["run_id"], "synthetic-independent-revision", stage="verify"
    )
    for source in packet["source_index"]:
        bridge.workflow.read_source(packet["task_token"], article_id=source["id"])
    candidate = bridge.workflow.candidate(
        packet["task_token"],
        "synthetic-independent-revision",
        {
            "article_id": first["article_id"],
            "text_sha256": text_digest(
                bridge.workflow.snapshot(initial["run_id"])["articles"][0]["text"]
            ),
            "summary": "Synthetic original-first observation",
        },
    )
    final = bridge.workflow.submit(
        packet["task_token"],
        "synthetic-independent-revision",
        {
            "input_hash": packet["input_hash"],
            "decision": "approve",
            **candidate["required_checks"],
            "issues": [],
        },
    )
    bridge.projection.sync(final)
    replacement = bridge.workflow.list_cards(run_id=final["run_id"])[0]
    assert replacement["replaces_card_id"] == first["id"]
    assert {row["id"] for row in service.records(db.research_cards, book_id)["items"]} == {
        replacement["id"],
        second["id"],
    }
    assert service.card(first["id"])["superseded"]
    assert not service.card(second["id"])["superseded"]
    assert service.card(replacement["id"])["summary"] == draft.summary
    # The original run still owns the second article, so its generation remains
    # searchable; its superseded first card must not consume the top-N window.
    indexed_settings = settings.model_copy(
        update={
            "opensearch_url": "http://127.0.0.1:19269",
            "opensearch_index": "test-lineage-" + uuid4().hex,
        }
    )
    discovery = ResearchDiscovery(indexed_settings, engine, bridge.objects, bridge.store)
    monkeypatch.setattr(
        BookIndexer,
        "embed_cached",
        lambda self, run_id, records, metrics: {
            "chunks": [dict(record, vector=[1.0] + [0.0] * 1023) for record in records],
            "model": {"synthetic_contract_only": True},
        },
    )
    try:
        for run in (initial, final):
            with engine.connect() as connection:
                projected = connection.scalar(
                    select(db.research_runs.c.summary).where(db.research_runs.c.id == run["run_id"])
                )
            discovery.index(run["run_id"], projected["discovery_input_sha256"])
        assert discovery.scope([book_id])["ready_runs"] == 2
        assert (
            discovery.search("LegacyOnlyMarker", book_ids=[book_id], semantic=False)["records"]
            == []
        )
        assert (
            discovery.search("RevisedOnlyMarker", book_ids=[book_id], semantic=False)["records"][0][
                "record_id"
            ]
            == replacement["id"]
        )
    finally:
        if discovery.client.indices.exists(index=discovery.name):
            discovery.client.indices.delete(index=discovery.name)


def test_retained_policy_is_visible_as_upgrade_without_automatic_rewriting(
    research_book, monkeypatch
):
    from copy import deepcopy

    from hrs.processing import research_workflow

    settings, engine, bridge, book_id, _, _, _ = research_book
    status, _ = finish_contract_run(bridge, bridge.start(book_id), "Retained v4 source")
    current = deepcopy(CURRENT_CARD_POLICY)
    monkeypatch.setattr(research_workflow, "POLICY", current)
    bridge.tick()
    assert (
        len(
            [
                run
                for run in bridge.workflow.list_runs(book_id)
                if run.get("purpose", "book") == "book"
            ]
        )
        == 1
    )
    service = Research(settings, engine)
    service.bridge, service.workflow = bridge, bridge.workflow
    result = service.overview(book_id)
    assert result["needs_upgrade"] and result["needs_update"]
    assert (
        result["research_policy_version"] == 4
        and result["required_research_policy_version"] == CURRENT_CARD_POLICY["version"]
    )
    assert result["runs"][0]["run_id"] == status["run_id"]


def test_status_changes_reference_one_immutable_source_object(research_book):
    _, engine, bridge, book_id, _, _, _ = research_book
    status = bridge.start(book_id)
    original_status = dict(status)
    initial = len(bridge.objects.writes)
    for action in ("pause", "resume", "pause"):
        status = getattr(bridge.workflow, action)(status["run_id"])
        bridge.projection.sync(status)
    additions = [json.loads(value) for value in bridge.objects.writes[initial:]]
    assert len(additions) == 3
    assert all(
        "articles" not in value and "articles" not in value["snapshot"] for value in additions
    )
    assert all(
        set(value["snapshot"]) == {"key", "sha256", "byte_length", "media_type"}
        for value in additions
    )
    snapshots = [value for value in bridge.objects.writes if "articles" in json.loads(value)]
    assert len(snapshots) == 1
    assert sum(len(value) for value in snapshots) == len(snapshots[0])
    bridge.projection.sync(original_status)
    with engine.connect() as connection:
        assert connection.scalar(select(db.research_runs.c.version)) == status["version"]


def test_research_projection_events_are_committed_once_per_version_and_ignore_duplicate_sync(
    research_book,
):
    _, engine, bridge, book_id, source_run, _, _ = research_book
    source_before = get_run(engine, source_run)
    status = bridge.start(book_id)
    bridge.projection.sync(status)
    initial = read_events(engine, 0)
    assert len(initial) == 1 and initial[0]["kind"] == "research.changed"
    assert initial[0]["book_id"] == book_id and initial[0]["run_id"] == source_run
    assert initial[0]["payload"] == {
        "research_run_id": status["run_id"],
        "state": status["state"],
        "version": status["version"],
        "counts": status["counts"],
        "artifacts_changed": False,
    }
    changed = bridge.workflow.pause(status["run_id"])
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(bridge.projection.sync, [changed, changed]))
    following = read_events(engine, initial[0]["sequence"])
    assert len(following) == 1 and following[0]["payload"]["version"] == changed["version"]
    assert following[0]["payload"]["artifacts_changed"] is False
    bridge.projection.sync(status)
    bridge.projection.sync(changed)
    assert read_events(engine, following[0]["sequence"]) == []
    assert get_run(engine, source_run) == source_before


def test_index_commit_emits_one_durable_research_event(research_book, monkeypatch):
    settings, engine, bridge, _, source_run, _, _ = research_book
    from hrs.search import indexing

    monkeypatch.setattr(
        indexing,
        "BookIndexer",
        lambda *args: type(
            "Indexer",
            (),
            {
                "index": lambda self, run_id: None,
                "refresh_navigation": lambda self, run_id: None,
            },
        )(),
    )
    run = get_run(engine, source_run)
    result = {
        **run["result"],
        "codex_projection_policy": PROJECTION_POLICY,
        "codex_projected_navigation_sha256": "e" * 64,
    }
    for key in ("codex_indexed_publication_sha256", "codex_indexed_chapters_sha256"):
        result.pop(key)
    with engine.begin() as connection:
        connection.execute(update(db.runs).where(db.runs.c.id == source_run).values(result=result))
    document = {
        "state": "published",
        "publication": {"markdown_sha256": result["codex_publication_sha256"]},
        "navigation": {"sha256": "e" * 64},
    }
    publication = NativePublication(settings, engine, bridge.objects)
    publication.index_if_needed(get_run(engine, source_run), document)
    publication.index_if_needed(get_run(engine, source_run), document)
    with engine.connect() as connection:
        events = (
            connection.execute(select(db.outbox).where(db.outbox.c.kind == "start_research"))
            .mappings()
            .all()
        )
    assert len(events) == 1 and events[0]["delivered"] is False
    assert events[0]["payload"] == {
        "publication_sha256": result["codex_publication_sha256"],
        "chapters_sha256": result["codex_reading_projection"]["chapters_sha256"],
    }
    indexed_events = [
        event for event in read_events(engine, 0) if event["kind"] == "library.indexed"
    ]
    assert len(indexed_events) == 1
    assert indexed_events[0]["payload"] == {
        "publication_sha256": result["codex_publication_sha256"],
        "chapters_sha256": result["codex_reading_projection"]["chapters_sha256"],
        "navigation_sha256": "e" * 64,
        "retrieval_generation": None,
    }


def test_foreign_workspace_never_enters_research_or_its_search_scope(research_book):
    settings, engine, bridge, book_id, source_run, _, _ = research_book
    from hrs.research.sources import ResearchEvidence

    result = {**get_run(engine, source_run)["result"], "codex_workspace_id": "foreign-workspace"}
    with engine.begin() as connection:
        connection.execute(update(db.runs).where(db.runs.c.id == source_run).values(result=result))
    bridge.tick()
    assert bridge.workflow.list_runs() == []
    assert ResearchEvidence(settings, engine).article_scope() == set()
    with pytest.raises(WrongWorkspace):
        bridge.start(book_id)


def test_research_source_failure_retains_outbox_and_published_source(research_book, monkeypatch):
    _, engine, bridge, _, source_run, _, _ = research_book

    def broken_source(reference):
        raise OSError("Synthetic unavailable research object")

    monkeypatch.setattr(bridge.objects, "read_bytes", broken_source)
    bridge.tick()
    assert bridge.workflow.list_runs() == []
    with engine.connect() as connection:
        assert connection.scalar(select(db.outbox.c.delivered)) is False
    run = get_run(engine, source_run)
    assert run["state"] == "completed" and run["result"]["published"] is True
    assert run["error"] is None


def test_source_freeze_rejects_chapter_generation_changed_since_run_was_read(research_book):
    settings, engine, bridge, _, source_run, _, _ = research_book
    from hrs.research.sources import publication_snapshot

    stale = get_run(engine, source_run)
    result = {
        **stale["result"],
        "codex_reading_projection": {"chapters_sha256": "f" * 64},
        "codex_indexed_chapters_sha256": "f" * 64,
    }
    with engine.begin() as connection:
        connection.execute(update(db.runs).where(db.runs.c.id == source_run).values(result=result))
    with pytest.raises(ServiceError, match="来源版本在冻结期间已变更"):
        publication_snapshot(settings, engine, bridge.store, bridge.objects, stale)


def test_empty_published_library_search_does_not_start_or_require_retrieval(platform, monkeypatch):
    settings, engine = platform
    from hrs.research import sources

    def unexpected_retrieval(*args, **kwargs):
        raise AssertionError("An empty publication scope must not invoke retrieval")

    monkeypatch.setattr(sources, "Search", unexpected_retrieval)
    app = create_app(settings, engine)
    with TestClient(app) as client:
        response = client.get("/api/v2/research/search", params={"q": "尚未入库资料"})
    assert response.status_code == 200
    result = response.json()
    assert result["evidence_status"] == "no_published_sources"
    assert result["metrics"]["retrieval_executed"] is False
    assert result["backend"] == "hybrid" and result["available"] is True
    assert result["hits"] == result["cards"] == result["entities"] == []


def test_pending_human_api_cas_reader_export_and_deleted_book_cancellation(research_book):
    settings, engine, bridge, book_id, source_run, chapter_id, text = research_book
    status = bridge.start(book_id)
    draft = {
        "id": "local-card",
        "article_id": chapter_id,
        "title": "Synthetic card",
        "summary": "待审提要",
        "evidence": [
            {
                "id": "e1",
                "article_id": chapter_id,
                "start": text.index("人物甲"),
                "end": text.index("。") + 1,
                "quote": "人物甲提出主张。",
            }
        ],
        "mentions": [
            {
                "id": "m1",
                "kind": "person",
                "name": "人物甲",
                "identity_basis": "原文明示",
                "evidence_ids": ["e1"],
            }
        ],
    }
    for stage in ("author", "enrich", "verify"):
        worker = "synthetic-" + stage
        packet = bridge.workflow.claim(status["run_id"], worker)
        bridge.workflow.read_source(packet["task_token"])
        if stage == "verify":
            candidate = bridge.workflow.candidate(
                packet["task_token"],
                worker,
                {
                    "article_id": chapter_id,
                    "text_sha256": text_digest(text),
                    "summary": "独立读取原文",
                },
            )
            result = {
                "decision": "pending",
                **candidate["required_checks"],
                "issues": ["测试争议"],
            }
        else:
            if stage == "enrich":
                bridge.workflow.record_search(
                    packet["task_token"],
                    {
                        "query": "synthetic contract only",
                        "available": True,
                        "backend": "hybrid",
                        "hits": [],
                        "metrics": {"synthetic_contract": True},
                    },
                )
            result = {"cards": [draft]}
        status = bridge.workflow.submit(
            packet["task_token"], worker, {"input_hash": packet["input_hash"], **result}
        )
    bridge.projection.sync(status)
    service = Research(settings, engine)
    service.bridge, service.workflow = bridge, bridge.workflow
    from hrs.research.sources import ResearchEvidence

    assert ResearchEvidence(settings, engine).entities("人物") == []
    app = create_app(settings, engine)
    app.state.research = service
    with TestClient(app) as client:
        overview = client.get(f"/api/v2/research/books/{book_id}").json()
        assert overview["cards_count"] == overview["entities_count"] == 1
        summary = overview["cards"][0]
        assert "evidence" not in summary and summary["evidence_count"] == 1
        card_id = summary["id"]
        card = client.get(f"/api/v2/research/cards/{card_id}").json()
        assert card["evidence"][0]["reading_target"]["chapter_id"] == chapter_id
        exported = client.get(f"/api/v2/research/cards/{card_id}/export")
        assert exported.status_code == 200
        href = re.search(r"\[阅读原文\]\(([^)]+)\)", exported.text)[1]
        location = urlsplit(href)
        assert location.netloc == "testserver"
        path, _, query = location.fragment.partition("?")
        assert path == f"/books/{book_id}"
        assert parse_qs(query) == {
            "tab": ["reader"],
            "section": ["text"],
            "chapter": [chapter_id],
            "physicalPage": ["4"],
            "start": [str(text.index("人物甲"))],
            "end": [str(text.index("。") + 1)],
            "textSha": [text_digest(text)],
            "run": [source_run],
        }
        response = client.post(
            f"/api/v2/research/cards/{card_id}/decision",
            json={"action": "edit", "expected_version": 1, "text": "人工修订提要"},
        )
        assert response.status_code == 200
        assert response.json()["state"] == "human_accepted"
        assert response.json()["approval_type"] == "user"
        assert response.json()["version"] == 2
        changed_events = [
            event for event in read_events(engine, 0) if event["kind"] == "research.changed"
        ]
        assert [event["payload"]["artifacts_changed"] for event in changed_events] == [
            False,
            True,
            True,
        ]
        assert (
            changed_events[-1]["payload"]["version"]
            == bridge.workflow.status(status["run_id"])["version"]
        )
        cursor = changed_events[-1]["sequence"]
        bridge.projection.sync(bridge.workflow.status(status["run_id"]))
        assert read_events(engine, cursor) == []
        identities = ResearchEvidence(settings, engine).entities("人物")
        assert len(identities) == 1 and identities[0]["name"] == "人物甲"
        assert "evidence" not in identities[0]
        retained = get_run(engine, source_run)["result"]
        with engine.begin() as connection:
            connection.execute(
                update(db.runs)
                .where(db.runs.c.id == source_run)
                .values(result={**retained, "codex_revision_pending": True})
            )
        assert (
            client.get(f"/api/v2/research/cards/{card_id}").json()["source_status"]
            == "revision_pending"
        )
        assert client.get(f"/api/v2/research/books/{book_id}/cards").json()["items"][0][
            "needs_update"
        ]
        with engine.begin() as connection:
            connection.execute(
                update(db.runs)
                .where(db.runs.c.id == source_run)
                .values(
                    result={
                        **retained,
                        "codex_publication_sha256": "e" * 64,
                        "codex_reading_projection": {"chapters_sha256": "f" * 64},
                    }
                )
            )
        historical = client.get(f"/api/v2/research/cards/{card_id}").json()
        assert historical["source_current"] is False and historical["source_status"] == "stale"
        overview = client.get(f"/api/v2/research/books/{book_id}").json()
        assert overview["cards_count"] == 0 and overview["stale_cards_count"] == 1
        assert overview["needs_update"] is True
        assert ResearchEvidence(settings, engine).entities("人物") == []
        assert (
            client.post(
                f"/api/v2/research/cards/{card_id}/decision",
                json={"action": "accept", "expected_version": 2},
            ).status_code
            == 409
        )
        assert "历史卡片" in client.get(f"/api/v2/research/cards/{card_id}/export").text
        with engine.begin() as connection:
            connection.execute(
                update(db.runs)
                .where(db.runs.c.id == source_run)
                .values(result={**retained, "codex_indexed_chapters_sha256": "f" * 64})
            )
        assert ResearchEvidence(settings, engine).entities("人物") == []
        with engine.begin() as connection:
            connection.execute(
                update(db.runs).where(db.runs.c.id == source_run).values(result=retained)
            )
        assert (
            client.post(
                f"/api/v2/research/cards/{card_id}/decision",
                json={"action": "accept", "expected_version": 1},
            ).status_code
            == 409
        )
        assert client.get("/api/v2/research/cards/does-not-exist").status_code == 404
    with engine.begin() as connection:
        connection.execute(
            update(db.books).where(db.books.c.id == book_id).values(state="deleting")
        )
    bridge.tick()
    assert bridge.workflow.status(status["run_id"])["state"] == "cancelled"
    with engine.connect() as connection:
        assert (
            connection.scalar(select(db.runs.c.state).where(db.runs.c.id == source_run))
            == "completed"
        )
    with TestClient(app) as client:
        assert client.get(f"/api/v2/research/cards/{card_id}").status_code == 404
        assert client.get(f"/api/v2/research/cards/{card_id}/export").status_code == 404
    with pytest.raises(ServiceError):
        bridge.projection.sync(status)
    with engine.begin() as connection:
        connection.execute(delete(db.research_runs).where(db.research_runs.c.book_id == book_id))
    with pytest.raises(ServiceError):
        bridge.projection.sync(status)
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(db.research_runs)) == 0


def finish_contract_run(
    bridge, status, title, *, pending=False, external=None, draft_patch=None, counterpart=None
):
    """Synthetic stage contracts; this helper is never a native accuracy acceptance."""
    first_worker = f"synthetic-{status['run_id']}-author"
    first_packet = bridge.workflow.claim(status["run_id"], first_worker, stage="author")
    article = next(
        article
        for article in bridge.workflow.snapshot(status["run_id"])["articles"]
        if article["id"] == first_packet["article_id"]
    )
    start = article["text"].index("人物甲")
    draft = {
        "id": "local-card",
        "article_id": article["id"],
        "title": title,
        "summary": "Synthetic contract summary",
        "evidence": [
            {
                "id": "e1",
                "article_id": article["id"],
                "start": start,
                "end": start + len("人物甲"),
                "quote": "人物甲",
            }
        ],
    }
    draft.update(draft_patch or {})
    for stage in ("author", "enrich", "verify"):
        worker = f"synthetic-{status['run_id']}-{stage}"
        packet = (
            first_packet
            if stage == "author"
            else bridge.workflow.claim(status["run_id"], worker, stage=stage)
        )
        for source in packet["source_index"]:
            bridge.workflow.read_source(packet["task_token"], article_id=source["id"])
        if stage == "enrich":
            bridge.workflow.record_search(
                packet["task_token"],
                {
                    "query": "synthetic contract only",
                    "backend": "hybrid",
                    "available": True,
                    "hits": [],
                    "metrics": {"synthetic_contract": True},
                },
                book_ids=(status.get("research_scope") or {}).get("book_ids"),
            )
            if external:
                source = bridge.workflow.add_source(packet["task_token"], external)[0]
                bridge.workflow.read_source(packet["task_token"], article_id=source["id"])
                draft["evidence"].append(
                    {
                        "id": "e2",
                        "article_id": source["id"],
                        "start": start,
                        "end": start + len("人物甲"),
                        "quote": "人物甲",
                    }
                )
            if counterpart:
                target = bridge.workflow.target_card(
                    packet["task_token"], worker, counterpart["id"], counterpart["version"]
                )
                for source in target["source_index"]:
                    bridge.workflow.read_source(packet["task_token"], article_id=source["id"])
                for offset in range(0, target["total"], 1600):
                    bridge.workflow.target_card_text(
                        packet["task_token"], worker, target["card_ref"], offset, 1600
                    )
                draft["evidence"].append(
                    {
                        "id": "e2",
                        "article_id": target["source_ids"][0],
                        "start": start,
                        "end": start + 3,
                        "quote": "人物甲",
                    }
                )
                draft["cross_relations"] = [
                    {
                        "id": "cross",
                        "kind": "association",
                        "reason": "Synthetic two-sided contract only",
                        "target_card_id": counterpart["id"],
                        "target_card_version": counterpart["version"],
                        "target_publication_sha256": counterpart["publication_sha256"],
                        "source_evidence_ids": ["e1"],
                        "target_evidence_ids": ["e2"],
                    }
                ]
        if stage == "verify":
            candidate = bridge.workflow.candidate(
                packet["task_token"],
                worker,
                {
                    "article_id": article["id"],
                    "text_sha256": article["text_sha256"],
                    "summary": "Synthetic source-first observation",
                },
            )
            for target in candidate.get("target_cards", []):
                for offset in range(0, target["total"], 1600):
                    bridge.workflow.target_card_text(
                        packet["task_token"], worker, target["card_ref"], offset, 1600
                    )
            result = {
                "decision": "pending" if pending else "approve",
                **candidate["required_checks"],
                "issues": ["Synthetic contract dispute"] if pending else [],
            }
        else:
            result = {"cards": [draft]}
        status = bridge.workflow.submit(
            packet["task_token"], worker, {"input_hash": packet["input_hash"], **result}
        )
    bridge.projection.sync(status)
    return status, next(
        card
        for card in bridge.workflow.list_cards(run_id=status["run_id"])
        if card["article_id"] == article["id"]
    )


def test_derived_discovery_is_versioned_and_never_reuses_stale_source(research_book, monkeypatch):
    from hrs.research.discovery import ResearchDiscovery
    from hrs.search.indexing import BookIndexer

    settings, engine, bridge, book_id, source_run, _, _ = research_book
    status, card = finish_contract_run(bridge, bridge.start(book_id), "Distinct discovery title")
    settings = settings.model_copy(
        update={
            "opensearch_url": "http://127.0.0.1:19269",
            "opensearch_index": "test-discovery-" + uuid4().hex,
        }
    )
    discovery = ResearchDiscovery(settings, engine, bridge.objects, bridge.store)
    monkeypatch.setattr(
        BookIndexer,
        "embed_cached",
        lambda self, run_id, records, metrics: {
            "chunks": [dict(record, vector=[1.0] + [0.0] * 1023) for record in records],
            "model": {"synthetic_contract_only": True},
        },
    )
    with engine.connect() as connection:
        expected = connection.scalar(
            select(db.research_runs.c.summary).where(db.research_runs.c.id == status["run_id"])
        )["discovery_input_sha256"]
    try:
        built = discovery.index(status["run_id"], expected)
        assert built["chunks"] > 0
        with engine.begin() as connection:
            connection.execute(
                update(db.outbox).where(db.outbox.c.kind == "index_research").values(delivered=True)
            )
        assert bridge.rebuild_discovery(source_run)["research_run_ids"] == [status["run_id"]]
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    select(func.count())
                    .select_from(db.outbox)
                    .where(db.outbox.c.kind == "index_research", db.outbox.c.delivered.is_(False))
                )
                == 1
            )
        result = discovery.search("Distinct discovery", book_ids=[book_id], semantic=False)
        assert result["available"] and result["backend"] == "lexical"
        assert result["records"][0]["record_id"] == card["id"]
        assert result["records"][0]["source_evidence"] is False
        with engine.begin() as connection:
            source = connection.scalar(select(db.runs.c.result).where(db.runs.c.id == source_run))
            connection.execute(
                update(db.runs)
                .where(db.runs.c.id == source_run)
                .values(result={**source, "codex_publication_sha256": "e" * 64})
            )
        assert discovery.search("Distinct discovery", semantic=False)["records"] == []
    finally:
        if discovery.client.indices.exists(index=discovery.name):
            discovery.client.indices.delete(index=discovery.name)


def test_research_revision_keeps_complete_cards_until_replacement_is_ready(research_book):
    settings, engine, bridge, book_id, _, _, _ = research_book
    original, card = finish_contract_run(bridge, bridge.start(book_id), "Original complete card")
    service = Research(settings, engine)
    service.bridge, service.workflow = bridge, bridge.workflow
    replacement = bridge.start(
        book_id, revision_of=original["run_id"], reason="Synthetic revision contract"
    )
    assert service.records(db.research_cards, book_id)["items"][0]["id"] == card["id"]
    replacement, revised = finish_contract_run(
        bridge, replacement, "Replacement pending card", pending=True
    )
    assert service.records(db.research_cards, book_id)["items"][0]["id"] == card["id"]
    assert service.card(revised["id"])["superseded"] is False
    accepted = service.decide(
        revised["id"], ResearchDecision(action="accept", expected_version=revised["version"])
    )
    visible = service.records(db.research_cards, book_id)
    assert visible["total"] == 1 and visible["items"][0]["id"] == accepted["id"]
    assert service.card(card["id"])["superseded"] is True
    assert service.card(card["id"])["source_current"] is True
    with pytest.raises(ServiceError):
        service.decide(
            card["id"], ResearchDecision(action="accept", expected_version=card["version"])
        )


def test_external_book_revision_hides_dependent_cards_and_preserves_original_target(research_book):
    settings, engine, bridge, book_id, source_run, chapter_id, _ = research_book
    from hrs.research.sources import publication_snapshot

    external_book, external_run, external_article = (str(uuid4()) for _ in range(3))
    publication = "e" * 64
    with engine.connect() as connection:
        original_chapter = dict(
            connection.execute(select(db.chapters).where(db.chapters.c.id == chapter_id))
            .mappings()
            .one()
        )
    body = json.loads(bridge.objects.read_bytes(original_chapter["content"]))
    body["acceptance"]["publication"]["markdown_sha256"] = publication
    content = bridge.objects.put_bytes(json.dumps(body, ensure_ascii=False).encode())
    result = {
        **get_run(engine, source_run)["result"],
        "codex_document_id": "synthetic-external-document",
        "codex_publication_sha256": publication,
        "codex_indexed_publication_sha256": publication,
    }
    with engine.begin() as connection:
        connection.execute(
            insert(db.books).values(
                id=external_book, title="Synthetic external book", state="ready"
            )
        )
        connection.execute(
            insert(db.runs).values(
                id=external_run,
                book_id=external_book,
                state="completed",
                stage="complete",
                source={"sha256": "f" * 64},
                result=result,
            )
        )
        connection.execute(
            insert(db.chapters).values(
                {
                    **original_chapter,
                    "id": external_article,
                    "book_id": external_book,
                    "run_id": external_run,
                    "content": content,
                }
            )
        )
    external = publication_snapshot(
        settings, engine, bridge.store, bridge.objects, get_run(engine, external_run)
    )
    status, card = finish_contract_run(
        bridge, bridge.start(book_id), "Cross-book contract card", external=external
    )
    service = Research(settings, engine)
    service.bridge, service.workflow = bridge, bridge.workflow
    located = service.card(card["id"])
    target = located["evidence"][1]["reading_target"]
    assert located["evidence"][1]["book_id"] == external_book
    assert target["run_id"] == external_run and target["chapter_id"] == external_article
    assert located["source_current"] is True
    with engine.begin() as connection:
        connection.execute(
            update(db.runs)
            .where(db.runs.c.id == external_run)
            .values(result={**result, "codex_publication_sha256": "a" * 64})
        )
    assert service.records(db.research_cards, book_id)["total"] == 0
    historical = service.card(card["id"])
    assert historical["source_current"] is False and historical["needs_update"] is True
    assert historical["evidence"][1]["reading_target"] == target
    assert bridge.workflow.status(status["run_id"])["state"] == "completed"
