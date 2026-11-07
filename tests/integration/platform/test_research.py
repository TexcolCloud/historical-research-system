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
from hrs.processing.workspace import identity
from hrs.research.api import Research, ResearchDecision
from hrs.research.bridge import ResearchBridge
from hrs.storage import models as db


@pytest.fixture
def research_book(platform):
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
        assert connection.scalar(select(func.count()).select_from(db.outbox)) == 1
        assert connection.scalar(select(db.outbox.c.delivered)) is True
        assert (
            connection.scalar(select(db.runs.c.state).where(db.runs.c.id == source_run))
            == "completed"
        )
        assert connection.scalar(select(db.research_runs.c.id)) == runs[0]["run_id"]


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


def finish_contract_run(bridge, status, title, *, pending=False, external=None):
    """Synthetic stage contracts; this helper is never a native accuracy acceptance."""
    article = bridge.workflow.snapshot(status["run_id"])["articles"][0]
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
    for stage in ("author", "enrich", "verify"):
        worker = f"synthetic-{status['run_id']}-{stage}"
        packet = bridge.workflow.claim(status["run_id"], worker)
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
    return status, bridge.workflow.list_cards(run_id=status["run_id"])[0]


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
