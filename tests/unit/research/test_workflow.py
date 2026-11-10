"""Synthetic contract tests; these exercise scheduling, never source accuracy approval."""

import json
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest
from pydantic import ValidationError

from hrs.processing.contracts import SOL, TERRA
from hrs.processing.errors import EvidenceMismatch, LeaseConflict, WorkflowStopped
from hrs.processing.execution_policy import configure_execution
from hrs.processing.hashing import digest
from hrs.processing.research_workflow import ResearchWorkflow
from hrs.processing.store import Store
from hrs.research.graph import ResearchGraph
from hrs.research.models import CardDraft, ResearchDate


def legacy_policy(version=5):
    """Explicit retained policy; a new product default must not rewrite old-contract tests."""
    from hrs.research.policy import POLICY, STAGES

    return {
        **deepcopy(POLICY),
        "version": version,
        "card_only": False,
        "complete_research": version >= 5,
        "enrichment_search_required": True,
        "models": dict.fromkeys(STAGES, SOL),
        "stages": {stage: {"model": SOL, "reasoning_effort": "high"} for stage in STAGES},
    }


def snapshot(book_id="book-1", text="第一頁記張三。第二頁記張三參與會議。"):
    split = len(text) // 2
    return {
        "book_id": book_id,
        "book_title": "Synthetic publication",
        "source_run_id": "publication-1",
        "document_id": "a" * 24,
        "workspace_id": "test",
        "publication_sha256": digest(text),
        "validation_scope": "content",
        "content_verified": True,
        "articles": [
            {
                "id": "article-1",
                "title": "Synthetic article",
                "text": text,
                "text_sha256": digest(text),
                "pages": [1, 2],
                "footnotes": [],
                "parts": [
                    {
                        "id": "p1",
                        "start": 0,
                        "end": split,
                        "text": text[:split],
                        "source": {"pages": [1], "image_sha256": "1" * 64},
                    },
                    {
                        "id": "p2",
                        "start": split,
                        "end": len(text),
                        "text": text[split:],
                        "source": {"pages": [2], "image_sha256": "2" * 64},
                    },
                ],
            }
        ],
    }


def draft(article_id="article-1", text="第一頁記張三。第二頁記張三參與會議。", card_id="card-1"):
    return {
        "id": card_id,
        "article_id": article_id,
        "title": "記述",
        "summary": "原文記述參會。",
        "evidence": [
            {"id": "e1", "article_id": article_id, "start": 0, "end": len(text), "quote": text}
        ],
        "items": [
            {
                "id": "argument-1",
                "kind": "argument",
                "text": "文中記有會議。",
                "evidence_ids": ["e1"],
            }
        ],
        "mentions": [
            {
                "id": "person-1",
                "kind": "person",
                "name": "張三",
                "identity_basis": "本段明載姓名",
                "evidence_ids": ["e1"],
            }
        ],
    }


@pytest.fixture
def workflow(tmp_path, monkeypatch):
    # This suite retains the v4 wire/lease contracts; complete v5 extraction has
    # its own inventory, metadata, repair and catalogue acceptance fixtures.
    from hrs.research import policy

    monkeypatch.setattr(policy, "POLICY", legacy_policy(4))
    return ResearchWorkflow(Store(tmp_path / "workspace"))


def read_all(workflow, packet):
    for article in packet["source_index"]:
        offset = 0
        while offset < article["text_length"]:
            source = workflow.read_source(packet["task_token"], offset, 7, article_id=article["id"])
            offset = source["end"]


def submit_draft(workflow, run_id, stage, worker, cards=None):
    packet = workflow.claim(run_id, worker, stage=stage)
    assert packet is not None
    read_all(workflow, packet)
    if stage == "enrich":
        workflow.record_search(
            packet["task_token"],
            {
                "query": "synthetic fixture query",
                "backend": "hybrid",
                "available": True,
                "hits": [],
                "metrics": {"fixture": True},
            },
        )
    cards = cards or packet.get("cards") or [draft()]
    result = {"input_hash": packet["input_hash"], "cards": cards}
    workflow.submit(packet["task_token"], worker, result)
    return packet, result


def verify(workflow, run_id, decision="approve", worker="independent-verifier"):
    packet = workflow.claim(run_id, worker, stage="verify")
    assert "cards" not in packet
    read_all(workflow, packet)
    source = packet["source_index"][0]
    candidate = workflow.candidate(
        packet["task_token"],
        worker,
        {
            "article_id": source["id"],
            "text_sha256": source["text_sha256"],
            "summary": "已獨立通讀合成來源。",
        },
    )
    return workflow.submit(
        packet["task_token"],
        worker,
        {
            "input_hash": packet["input_hash"],
            "decision": decision,
            **candidate["required_checks"],
            "issues": ["需由使用者判斷解讀。"] if decision == "pending" else [],
        },
    )


def completed(workflow, book_id="book-1"):

    run = workflow.enqueue(snapshot(book_id), _policy=legacy_policy(4))
    submit_draft(workflow, run["run_id"], "author", "author")
    submit_draft(workflow, run["run_id"], "enrich", "enricher")
    verify(workflow, run["run_id"])
    return workflow.status(run["run_id"])


def test_read_only_queries_do_not_create_research_schema(workflow):
    assert workflow.list_runs() == workflow.list_cards() == workflow.list_entities() == []
    assert not workflow.has_live_work()
    with pytest.raises(FileNotFoundError):
        workflow.status("missing")
    with pytest.raises(FileNotFoundError):
        workflow.card("missing")
    with workflow.ledger.connect() as connection:
        assert not workflow._exists(connection)


def test_enqueue_is_atomic_idempotent_and_rejects_unverified_sources(workflow):
    with ThreadPoolExecutor(max_workers=4) as pool:
        runs = list(pool.map(lambda _: workflow.enqueue(snapshot()), range(4)))
    assert len({run["run_id"] for run in runs}) == 1
    assert len(runs[0]["tasks"]) == 1
    invalid = snapshot()
    invalid["content_verified"] = False
    with pytest.raises(EvidenceMismatch, match="content-verified"):
        workflow.enqueue(invalid)
    invalid = snapshot()
    invalid["articles"][0]["text_sha256"] = "0" * 64
    with pytest.raises(EvidenceMismatch, match="frozen hash"):
        workflow.enqueue(invalid)


def test_article_tasks_run_independently_and_leases_are_exclusive(workflow):
    source = snapshot()
    other = deepcopy(source["articles"][0])
    other["id"] = "article-2"
    source["articles"].append(other)
    run = workflow.enqueue(source)
    with ThreadPoolExecutor(max_workers=4) as pool:
        packets = list(
            pool.map(
                lambda worker: workflow.claim(run["run_id"], worker, stage="author"),
                ["w1", "w2", "w3", "w4"],
            )
        )
    claimed = [packet for packet in packets if packet]
    assert len(claimed) == 2
    assert {packet["article_id"] for packet in claimed} == {"article-1", "article-2"}
    assert len({packet["task_token"] for packet in claimed}) == 2


def test_pause_revokes_lease_and_resume_keeps_immutable_inputs(workflow):
    run = workflow.enqueue(snapshot())
    packet = workflow.claim(run["run_id"], "first")
    assert workflow.pause(run["run_id"])["state"] == "paused"
    assert workflow.claim(run["run_id"], "paused") is None
    with pytest.raises(LeaseConflict):
        workflow.read_source(packet["task_token"])
    restarted = ResearchWorkflow(workflow.store)
    restarted.resume(run["run_id"])
    resumed = restarted.claim(run["run_id"], "after-restart")
    assert resumed["input_hash"] == packet["input_hash"]
    assert resumed["task_token"] != packet["task_token"]


def test_expired_task_is_reclaimed_but_old_token_cannot_submit(workflow):
    run = workflow.enqueue(snapshot())
    packet = workflow.claim(run["run_id"], "expired")
    with workflow.ledger.connect(write=True) as connection:
        task, _ = workflow._lease(connection, packet["task_token"])
        task["expires_at"] = 0
        workflow._save_task(connection, task)
    assert workflow.pending_runs()[0]["tasks"][0]["state"] == "ready"
    replacement = workflow.claim(run["run_id"], "replacement")
    assert replacement["attempts"] == 2
    with pytest.raises(LeaseConflict):
        workflow.submit(
            packet["task_token"],
            "expired",
            {"input_hash": packet["input_hash"], "cards": [draft()]},
        )


def test_source_coverage_quote_integrity_input_cas_and_duplicate_submission(workflow):
    run = workflow.enqueue(snapshot())
    packet = workflow.claim(run["run_id"], "author")
    result = {"input_hash": packet["input_hash"], "cards": [draft()]}
    with pytest.raises(EvidenceMismatch, match="complete original"):
        workflow.submit(packet["task_token"], "author", result)
    read_all(workflow, packet)
    with pytest.raises(EvidenceMismatch, match="input hash"):
        workflow.submit(packet["task_token"], "author", {**result, "input_hash": "0" * 64})
    bad = deepcopy(result)
    bad["cards"][0]["evidence"][0]["quote"] = "不在原文"
    with pytest.raises(EvidenceMismatch, match="quote"):
        workflow.submit(packet["task_token"], "author", bad)
    first = workflow.submit(packet["task_token"], "author", result)
    assert workflow.submit(packet["task_token"], "author", result)["version"] == first["version"]
    with pytest.raises(LeaseConflict, match="cannot be replaced"):
        workflow.submit(packet["task_token"], "author", bad)


def test_verification_is_independent_source_first_and_complete(workflow):
    run = workflow.enqueue(snapshot())
    submit_draft(workflow, run["run_id"], "author", "author")
    submit_draft(workflow, run["run_id"], "enrich", "enricher")
    assert workflow.claim(run["run_id"], "author", stage="verify") is None
    assert workflow.claim(run["run_id"], "enricher", stage="verify") is None
    packet = workflow.claim(run["run_id"], "verifier", stage="verify")
    assert "cards" not in packet
    source = packet["source_index"][0]
    observations = {
        "article_id": source["id"],
        "text_sha256": source["text_sha256"],
        "summary": "來源獨立閱讀",
    }
    with pytest.raises(EvidenceMismatch, match="complete original"):
        workflow.candidate(packet["task_token"], "verifier", observations)
    read_all(workflow, packet)
    result = {
        "input_hash": packet["input_hash"],
        "decision": "approve",
        "checked_card_ids": ["card-1"],
    }
    with pytest.raises(EvidenceMismatch, match="observations"):
        workflow.submit(packet["task_token"], "verifier", result)
    candidate = workflow.candidate(packet["task_token"], "verifier", observations)
    result.update(candidate["required_checks"])
    with pytest.raises(EvidenceMismatch, match="immutable"):
        workflow.candidate(packet["task_token"], "verifier", {**observations, "summary": "changed"})
    with pytest.raises(EvidenceMismatch, match="every candidate"):
        workflow.submit(packet["task_token"], "verifier", {**result, "checked_card_ids": ["wrong"]})
    assert workflow.submit(packet["task_token"], "verifier", result)["state"] == "completed"


def test_physical_pages_and_footnote_ownership_survive_publication(workflow):
    source = snapshot()
    source["articles"][0]["footnotes"] = [
        {"id": "note-1", "owner": "編者", "note": {"start": 9, "end": 12}, "references": []}
    ]
    run = workflow.enqueue(source)
    card = draft()
    card["evidence"][0]["footnote_ids"] = ["wrong-note"]
    packet = workflow.claim(run["run_id"], "author")
    read_all(workflow, packet)
    with pytest.raises(EvidenceMismatch, match="Footnote"):
        workflow.submit(
            packet["task_token"], "author", {"input_hash": packet["input_hash"], "cards": [card]}
        )
    card["evidence"][0]["footnote_ids"] = ["note-1"]
    workflow.submit(
        packet["task_token"], "author", {"input_hash": packet["input_hash"], "cards": [card]}
    )
    submit_draft(workflow, run["run_id"], "enrich", "enricher")
    verify(workflow, run["run_id"])
    evidence = workflow.list_cards()[0]["evidence"][0]
    assert evidence["pages"] == [1, 2]
    assert evidence["footnotes"][0]["owner"] == "編者"
    assert len(evidence["sources"]) == 2
    assert all("text" not in part and part["text_sha256"] for part in evidence["sources"])


def test_native_pending_human_resolution_cas_and_old_revisions_are_retained(workflow):
    run = workflow.enqueue(snapshot())
    cards = [draft(), draft(card_id="card-2")]
    submit_draft(workflow, run["run_id"], "author", "author", cards)
    submit_draft(workflow, run["run_id"], "enrich", "enricher")
    assert verify(workflow, run["run_id"], "pending")["state"] == "pending"
    first, second = workflow.list_cards()
    edited = workflow.decision(first["id"], "edit", 1, "使用者修訂摘要", limitations=["保留邊界"])
    assert edited["state"] == "human_accepted" and edited["approval_type"] == "user"
    assert edited["checks"]["decision"] == "pending"
    assert workflow.card(first["id"], version=1)["summary"] != edited["summary"]
    with pytest.raises(LeaseConflict, match="revision changed"):
        workflow.decision(first["id"], "accept", 1)
    assert workflow.decision(second["id"], "reject", 1)["state"] == "excluded"
    assert workflow.status(run["run_id"])["state"] == "completed"
    assert workflow.status(run["run_id"])["counts"]["articles_completed"] == 1
    assert workflow.status(run["run_id"])["counts"]["articles_pending"] == 0
    assert workflow.status(run["run_id"])["counts"]["native_pending"] == 1


def test_new_publication_and_research_revision_keep_previous_artifacts(workflow):
    previous = completed(workflow)
    original = workflow.list_cards()[0]
    revised = workflow.enqueue(snapshot(), revision_of=previous["run_id"], reason="使用者勘誤")
    assert revised["run_id"] != previous["run_id"]
    assert (
        workflow.enqueue(snapshot(), revision_of=previous["run_id"], reason="使用者勘誤")["run_id"]
        == revised["run_id"]
    )
    assert workflow.card(original["id"])["state"] == "verified"
    assert workflow.status(previous["run_id"])["state"] == "completed"
    assert len(workflow.list_runs()) == 2


def test_external_sources_are_frozen_read_and_verified_with_cross_book_identity(workflow):
    first = completed(workflow)
    target = workflow.list_entities(run_id=first["run_id"])[0]
    run = workflow.enqueue(snapshot("book-2"))
    submit_draft(workflow, run["run_id"], "author", "second-author")
    packet = workflow.claim(run["run_id"], "second-enricher", stage="enrich")
    workflow.record_search(
        packet["task_token"],
        {
            "query": "synthetic cross-book fixture",
            "backend": "hybrid",
            "available": True,
            "hits": [],
            "metrics": {},
        },
    )
    external = snapshot("book-1")
    external["source_run_id"] = "original-publication"
    indexes = workflow.add_source(packet["task_token"], external)
    assert workflow.add_source(packet["task_token"], external) == indexes
    read_all(workflow, packet)
    card = packet["cards"][0]
    card["evidence"].append(
        {**card["evidence"][0], "id": "external-e1", "article_id": indexes[0]["id"]}
    )
    card["identity_links"] = [
        {
            "local_mention_id": "person-1",
            "entity_id": target["id"],
            "relation": "same_as",
            "evidence_ids": ["e1", "external-e1"],
            "reason": "兩書相同會議、日期與人物職務構成明確身份依據。",
        }
    ]
    result = {"input_hash": packet["input_hash"], "cards": [card]}
    with pytest.raises(EvidenceMismatch, match="complete original"):
        workflow.submit(packet["task_token"], "second-enricher", result)
    workflow.read_source(packet["task_token"], article_id=indexes[0]["id"])
    workflow.submit(packet["task_token"], "second-enricher", result)
    assert len(workflow.sources(run["run_id"])) == 1
    verify(workflow, run["run_id"], worker="second-verifier")
    published = workflow.list_cards(run_id=run["run_id"])[0]
    assert published["evidence"][1]["book_id"] == "book-1"
    assert published["evidence"][1]["source_run_id"] == "original-publication"
    assert len(workflow.entity(target["id"])["mentions"]) == 2


def test_same_names_do_not_merge_entities_without_explicit_verified_identity_link(workflow):
    completed(workflow, "book-1")
    completed(workflow, "book-2")
    entities = workflow.list_entities()
    assert len(entities) == 2 and entities[0]["name"] == entities[1]["name"]
    assert len(workflow.entity(entities[0]["id"])["mentions"]) == 1


def test_worker_cannot_verify_after_authoring_an_older_run(workflow):
    completed(workflow, "book-1")
    run = workflow.enqueue(snapshot("book-2"))
    submit_draft(workflow, run["run_id"], "author", "fresh-author")
    submit_draft(workflow, run["run_id"], "enrich", "fresh-enricher")
    assert workflow.claim(run["run_id"], "author", stage="verify") is None
    assert workflow.claim(run["run_id"], "enricher", stage="verify") is None
    assert workflow.claim(run["run_id"], "fresh-independent", stage="verify") is not None


def test_dates_and_entity_references_require_explicit_local_evidence():
    with pytest.raises(ValidationError, match="source wording and evidence"):
        ResearchDate(text="1920年", normalized="1920", certainty="stated")
    card = draft()
    card["formation_date"] = {
        "text": "1920年",
        "certainty": "stated",
        "evidence_ids": ["not-local"],
    }
    with pytest.raises(ValidationError, match="this card's evidence"):
        CardDraft.model_validate(card)


def test_graph_neighbors_are_bounded_source_linked_and_expose_events(workflow):
    run = workflow.enqueue(snapshot())
    card = draft()
    card["mentions"].append(
        {
            "id": "person-2",
            "kind": "person",
            "name": "李四",
            "identity_basis": "合成參與者",
            "evidence_ids": ["e1"],
        }
    )
    card["events"] = [
        {
            "id": "meeting",
            "name": "會議",
            "description": "合成會議",
            "participant_ids": ["person-1", "person-2"],
            "evidence_ids": ["e1"],
        }
    ]
    card["relations"] = [
        {
            "id": "association",
            "subject_id": "person-1",
            "object_id": "person-2",
            "predicate": "co_participant",
            "reason": "同一記述",
            "evidence_ids": ["e1"],
        }
    ]
    submit_draft(workflow, run["run_id"], "author", "author", [card])
    submit_draft(workflow, run["run_id"], "enrich", "enricher")
    verify(workflow, run["run_id"])
    entity = next(item for item in workflow.list_entities() if item["kind"] == "person")
    graph = ResearchGraph(workflow).related(entity["id"], hops=2, limit=1)
    assert graph["truncated"] and len(graph["edges"]) == 1
    assert graph["edges"][0]["evidence"][0]["pages"] == [1, 2]
    assert graph["edges"][0]["usage"] == "research_lead"
    assert any(item["kind"] == "event" for item in workflow.list_entities())
    with pytest.raises(ValueError, match="1–2 hops"):
        ResearchGraph(workflow).related(entity["id"], hops=3)
    second = workflow.enqueue(snapshot("book-2"))
    author = workflow.claim(second["run_id"], "second-author")
    with pytest.raises(ValueError, match="enrichment and verification"):
        workflow.related(author["task_token"], entity["id"])


def test_admission_is_shared_and_pausing_releases_capacity(workflow):
    configure_execution(workflow.store.root, capacity=2)
    first = workflow.enqueue(snapshot("book-1"))
    second = workflow.enqueue(snapshot("book-2"))
    third = workflow.enqueue(snapshot("book-3"))
    packet = workflow.claim(first["run_id"], "occupies")
    assert workflow.claim(second["run_id"], "occupies-second") is not None
    assert workflow.claim(third["run_id"], "blocked") is None
    assert workflow.renew_run_leases(first["run_id"], active_workers={"occupies"}) == 1
    workflow.pause(first["run_id"])
    assert workflow.claim(third["run_id"], "now-available") is not None
    with pytest.raises(LeaseConflict):
        workflow.read_source(packet["task_token"])


def test_native_research_and_coordinator_share_one_account_capacity_pool(workflow):
    configure_execution(workflow.store.root, capacity=4)
    workflow.ledger.save_session(
        {
            "session_token": "native-session",
            "document_id": "b" * 24,
            "stage": "ocr",
            "actual_model": TERRA,
            "state": "busy",
            "expires_at": time.time() + 900,
        }
    )
    control = workflow.store.runtime_control
    assert control.reserve_coordinator(SOL, "research-coordinator:fixture")["allowed"]
    first, second, third = [workflow.enqueue(snapshot(f"book-{index}")) for index in range(3)]
    assert workflow.claim(first["run_id"], "first-research")
    assert workflow.claim(second["run_id"], "second-research")
    assert workflow.ledger.capacity_used() == 4
    assert workflow.ledger.model_capacity_used(SOL) == 3
    assert workflow.ledger.model_capacity_used(TERRA) == 1
    assert workflow.claim(third["run_id"], "would-exceed-account") is None
    assert not control.admit(
        TERRA,
        "native-next",
        occupied=workflow.ledger.model_capacity_used(TERRA),
        total_occupied=workflow.ledger.capacity_used(),
    )["allowed"]
    control.release("research-coordinator:fixture")
    assert workflow.claim(third["run_id"], "now-fits-account")


def test_normal_coordinator_does_not_consume_the_last_worker_slot(workflow):
    configure_execution(workflow.store.root, capacity=2)
    session = {
        "session_token": "busy-native",
        "document_id": "b" * 24,
        "stage": "ocr",
        "actual_model": TERRA,
        "state": "busy",
        "expires_at": time.time() + 900,
    }
    workflow.ledger.save_session(session)
    control = workflow.store.runtime_control
    assert not control.reserve_coordinator(SOL, "research-coordinator:last-slot")["allowed"]
    assert workflow.ledger.capacity_used() == 1
    session["state"] = "exited"
    workflow.ledger.save_session(session)
    assert control.reserve_coordinator(SOL, "research-coordinator:last-slot")["allowed"]


@pytest.mark.parametrize("scope", ["account", "model"])
def test_recovery_coordinator_does_not_steal_the_only_probe_or_worker_slot(
    workflow, monkeypatch, scope
):
    configure_execution(workflow.store.root, capacity=2)
    now = [time.time()]
    monkeypatch.setattr("hrs.processing.runtime_control.time.time", lambda: now[0])
    control = workflow.store.runtime_control
    owner = "research-coordinator:recovery-fixture"
    assert control.reserve_coordinator(SOL, owner)["allowed"]
    assert workflow.ledger.capacity_used() == 1
    failure = control.failure("capacity", "fixture-provider-outage", scope=scope, model=SOL)
    assert workflow.ledger.capacity_used() == 1  # It stays counted until its executor yields.
    assert not control.renew(SOL, owner, expires_at=now[0] + 900)
    control.release(owner)
    now[0] = failure["retry_at"] + 1
    reservation = control.reserve_coordinator(SOL, owner)
    assert reservation["allowed"] and reservation["control_only"]
    assert all(state["probe_owner"] is None for state in control.snapshot()["scopes"])
    run = workflow.enqueue(snapshot())
    packet = workflow.claim(run["run_id"], "probe-author", stage="author")
    assert packet and workflow.ledger.capacity_used() == 1 + int(scope == "model")
    assert not control.reserve_coordinator(SOL, "research-coordinator:another")["allowed"]
    before = control.snapshot()
    control.success(SOL, owner, "coordinator-start-is-not-evidence")
    assert control.snapshot() == before
    read_all(workflow, packet)
    workflow.submit(
        packet["task_token"],
        "probe-author",
        {"input_hash": packet["input_hash"], "cards": [draft()]},
    )
    assert control.gate(SOL)["state"] == "recovering"
    assert workflow.ledger.capacity_used() == int(scope == "model")
    submit_draft(workflow, run["run_id"], "enrich", "recovery-enricher")
    now[0] += 31
    verify(workflow, run["run_id"], worker="recovery-verifier")
    assert control.gate(SOL)["state"] == "normal"
    assert (
        workflow.ledger.capacity_used() == 1
    )  # normal capacity counts the still-live dispatcher again
    control.release(owner)
    assert workflow.ledger.capacity_used() == 0


def test_technical_failures_and_coordinator_restarts_have_durable_budgets(workflow):
    run = workflow.enqueue(snapshot())
    for attempt in range(3):
        packet = workflow.claim(run["run_id"], f"worker-{attempt}")
        workflow.fail(
            packet["task_token"], f"worker-{attempt}", "transport", details={"fixture": True}
        )
        workflow.store.runtime_control.reset()
    assert workflow.status(run["run_id"])["state"] == "failed"
    workflow.resume(run["run_id"])
    retried = workflow.claim(run["run_id"], "explicit-retry")
    assert retried is not None
    status = workflow.fail(retried["task_token"], "explicit-retry", "transport")
    assert status["state"] == "running" and status["tasks"][0]["technical_failures"] == 1
    workflow.store.runtime_control.reset()
    workflow.pause(run["run_id"])
    workflow.resume(run["run_id"])
    for _ in range(3):
        status = workflow.runtime_failure(run["run_id"], {"code": "coordinator_exit"})
    assert status["state"] == "technical_blocked"
    assert not workflow.pending_runs()
    restarted = ResearchWorkflow(workflow.store)
    assert restarted.status(run["run_id"])["runtime_failures"] == 3
    assert restarted.resume(run["run_id"])["runtime_failures"] == 0


def test_original_reference_cannot_escape_frozen_pages_and_deleted_book_stays_cancelled(workflow):
    run = workflow.enqueue(snapshot())
    packet = workflow.claim(run["run_id"], "worker")
    assert workflow.source_reference(packet["task_token"], page=2)["image_sha256"] == "2" * 64
    with pytest.raises(EvidenceMismatch, match="outside"):
        workflow.source_reference(packet["task_token"], page=3)
    workflow.cancel_book("book-1")
    assert workflow.status(run["run_id"])["state"] == "cancelled"
    assert not workflow.pending_runs()
    assert workflow.claim(run["run_id"], "cannot-restart") is None
    with pytest.raises(WorkflowStopped, match="cannot be resumed"):
        workflow.resume(run["run_id"])
    with workflow.ledger.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM research_events").fetchone()[0] >= 3


def test_large_sources_and_drafts_are_immutable_references_not_status_payloads(
    workflow, monkeypatch
):
    text = "原始長文資料。" * 30000
    run = workflow.enqueue(snapshot(text=text))
    source_refs = None
    for _ in range(3):
        packet = workflow.claim(run["run_id"], "large-source-author")
        workflow.renew_run_leases(run["run_id"])
        workflow.pause(run["run_id"])
        workflow.resume(run["run_id"])
    packet = workflow.claim(run["run_id"], "large-source-author")
    for offset in range(0, len(text), 32000):
        source = workflow.read_source(packet["task_token"], offset, 32000)
        assert len(source["text"]) <= 32000 and all("text" not in part for part in source["parts"])
    card = draft(text=text[:20])
    workflow.submit(
        packet["task_token"],
        "large-source-author",
        {"input_hash": packet["input_hash"], "cards": [card]},
    )
    with workflow.ledger.connect() as connection:
        payload = connection.execute(
            "SELECT payload FROM research_runs WHERE id=?", (run["run_id"],)
        ).fetchone()[0]
        tasks = [
            row[0]
            for row in connection.execute(
                "SELECT payload FROM research_tasks WHERE run_id=?", (run["run_id"],)
            )
        ]
        source_refs = connection.execute("SELECT COUNT(*) FROM research_sources").fetchone()[0]
    assert '"snapshot":' not in payload and '"snapshot_ref":' in payload
    assert len(payload) < 5000 and all(
        len(task) < 2500 and '"cards":' not in task for task in tasks
    )
    assert source_refs == 3  # publication, article and authored draft, each frozen once
    original_loader = workflow._load_source
    monkeypatch.setattr(
        workflow,
        "_load_source",
        lambda *args: pytest.fail("status must not load source text or drafts"),
    )
    for _ in range(3):
        assert workflow.status(run["run_id"])["counts"]["articles_total"] == 1
        assert workflow.pending_runs()[0]["run_id"] == run["run_id"]
    monkeypatch.setattr(workflow, "_load_source", original_loader)
    assert workflow.snapshot(run["run_id"])["articles"][0]["text"] == text


def test_enrichment_cannot_skip_real_hybrid_search_and_retains_transport_audit(workflow):
    run = workflow.enqueue(snapshot())
    submit_draft(workflow, run["run_id"], "author", "author")
    packet = workflow.claim(run["run_id"], "enricher", stage="enrich")
    read_all(workflow, packet)
    result = {"input_hash": packet["input_hash"], "cards": packet["cards"]}
    with pytest.raises(EvidenceMismatch, match="actual hybrid retrieval"):
        workflow.submit(packet["task_token"], "enricher", result)
    with pytest.raises(ValueError, match="actual hybrid"):
        workflow.record_search(
            packet["task_token"], {"query": "張三", "backend": "lexical", "available": True}
        )
    workflow.record_search(
        packet["task_token"],
        {
            "query": "張三",
            "backend": "hybrid",
            "available": False,
            "hits": [],
            "metrics": {},
            "error": {"code": "unavailable"},
        },
    )
    with pytest.raises(EvidenceMismatch, match="actual hybrid retrieval"):
        workflow.submit(packet["task_token"], "enricher", result)
    workflow.record_search(
        packet["task_token"],
        {
            "query": "張三會議",
            "backend": "hybrid",
            "available": True,
            "hits": [
                {
                    "id": "hit",
                    "chapter_id": "chapter",
                    "text": "引用片段",
                    "hidden_reasoning": "never retain",
                }
            ],
            "metrics": {"lexical_ms": 12.5, "chain_of_thought": "never retain"},
            "hidden_reasoning": "never retain",
        },
    )
    workflow.submit(packet["task_token"], "enricher", result)
    with workflow.ledger.connect() as connection:
        audits = [
            json.loads(row[0])
            for row in connection.execute(
                "SELECT payload FROM research_events WHERE event='research_search'"
            )
        ]
    assert audits[-1]["query"] == "張三會議" and audits[-1]["backend"] == "hybrid"
    assert audits[-1]["metrics"] == {"lexical_ms": 12.5}
    assert audits[-1]["hits_manifest"][0]["snippet_sha256"] == digest("引用片段")
    assert "never retain" not in str(audits)


def test_source_supported_no_external_claims_disposition_is_independently_checked(workflow):
    text = "本頁僅有版式記號與無名短句。"
    run = workflow.enqueue(snapshot(text=text))
    card = {
        "id": "card-1",
        "article_id": "article-1",
        "title": "版式記述",
        "summary": text,
        "evidence": [
            {"id": "e1", "article_id": "article-1", "start": 0, "end": len(text), "quote": text}
        ],
    }
    submit_draft(workflow, run["run_id"], "author", "author", [card])
    packet = workflow.claim(run["run_id"], "enricher", stage="enrich")
    read_all(workflow, packet)
    workflow.submit(
        packet["task_token"],
        "enricher",
        {
            "input_hash": packet["input_hash"],
            "cards": packet["cards"],
            "research_disposition": {
                "mode": "not_needed",
                "category": "no_named_entities_or_external_claims",
                "reason": "該頁只記無名版式與短句，沒有可供交叉辨人的人名、事件或跨文獻身份主張。",
                "evidence_ids": ["card-1/e1"],
            },
        },
    )
    assert verify(workflow, run["run_id"])["state"] == "completed"
    assert workflow.list_cards()[0]["checks"]["checked_research_disposition"]


def test_new_policy_creates_a_new_run_and_preserves_older_claim_contract(workflow, monkeypatch):
    from hrs.research import policy

    current = deepcopy(policy.POLICY)
    legacy = {**current, "version": 1, "enrichment_search_required": False}
    monkeypatch.setattr(policy, "POLICY", legacy)
    older = workflow.enqueue(snapshot())
    submit_draft(workflow, older["run_id"], "author", "old-author")
    packet = workflow.claim(older["run_id"], "old-enricher", stage="enrich")
    read_all(workflow, packet)
    monkeypatch.setattr(policy, "POLICY", current)
    newer = workflow.enqueue(snapshot())
    assert newer["run_id"] != older["run_id"]
    workflow.submit(
        packet["task_token"],
        "old-enricher",
        {"input_hash": packet["input_hash"], "cards": packet["cards"]},
    )
    assert workflow.status(older["run_id"])["policy"]["version"] == 1


def test_large_native_candidate_is_read_in_bounded_windows_with_required_full_coverage(workflow):
    run = workflow.enqueue(snapshot())
    card = draft()
    card["summary"] = "合成長提要逐項保留。" * 700
    submit_draft(workflow, run["run_id"], "author", "author", [card])
    submit_draft(workflow, run["run_id"], "enrich", "enricher")
    packet = workflow.claim(run["run_id"], "verifier", stage="verify")
    with pytest.raises(EvidenceMismatch, match="complete original"):
        workflow.candidate_text(packet["task_token"], "verifier")
    read_all(workflow, packet)
    with pytest.raises(EvidenceMismatch, match="observations"):
        workflow.candidate_text(packet["task_token"], "verifier")
    source = packet["source_index"][0]
    manifest = workflow.candidate(
        packet["task_token"],
        "verifier",
        {
            "article_id": source["id"],
            "text_sha256": source["text_sha256"],
            "summary": "合成來源獨立閱讀",
        },
        paged=None,
    )
    assert "cards" not in manifest and "required_checks" not in manifest
    assert (
        len(json.dumps(manifest, ensure_ascii=False)) < 1400
        and manifest["candidate"]["total"] > 6000
    )
    first = workflow.candidate_text(packet["task_token"], "verifier")
    assert len(first["text"]) == 1600
    with workflow.ledger.connect() as connection:
        task, _ = workflow._lease(connection, packet["task_token"])
        checks = workflow._verification_checks(workflow._task_cards(task))
    result = {"input_hash": packet["input_hash"], "decision": "approve", **checks}
    with pytest.raises(EvidenceMismatch, match="candidate JSON window"):
        workflow.submit(packet["task_token"], "verifier", result)
    windows = [first["text"]]
    offset = first["end"]
    while offset < first["total"]:
        piece = workflow.candidate_text(packet["task_token"], "verifier", offset)
        assert len(piece["text"]) <= 1600 and piece["candidate_sha256"] == first["candidate_sha256"]
        windows.append(piece["text"])
        offset = piece["end"]
    body = json.loads("".join(windows))
    assert body["cards"][0]["summary"] == card["summary"]
    assert (
        workflow.submit(packet["task_token"], "verifier", {**result, **body["required_checks"]})[
            "state"
        ]
        == "completed"
    )


def test_packet_windows_cover_large_schema_and_every_external_source_before_source_access(workflow):
    run = workflow.enqueue(snapshot())
    card = draft()
    card["summary"] = "合成長提要。" * 1600
    submit_draft(workflow, run["run_id"], "author", "author", [card])
    enriched, _ = submit_draft(workflow, run["run_id"], "enrich", "enricher")
    # Add three frozen external sources to the ready verification task, as a prior enrichment would.
    with workflow.ledger.connect(write=True) as connection:
        task = next(
            task for task in workflow._tasks(connection, run["run_id"]) if task["stage"] == "verify"
        )
        external_ids = []
        for index in range(3):
            article = deepcopy(snapshot(f"external-{index}")["articles"][0])
            article.update(id=f"source-canonical-{index}", title="外部來源標題。" * 600)
            reference = workflow._freeze_source(connection, article, "external_article")
            task["external_sources"].append({"id": article["id"], "source_ref": reference})
            external_ids.append(article["id"])
        cards = workflow._task_cards(task)
        for index, article_id in enumerate(external_ids):
            cards[0]["evidence"].append(
                {**cards[0]["evidence"][0], "id": f"external-e{index}", "article_id": article_id}
            )
        task["cards_ref"] = workflow._freeze_source(connection, {"cards": cards}, "draft")
        workflow._save_task(connection, task)
    manifest = workflow.claim(run["run_id"], "verifier", stage="verify", paged=None)
    assert "source_index" not in manifest and "result_schema" not in manifest
    assert (
        len(json.dumps(manifest, ensure_ascii=False)) < 1400 and manifest["packet"]["total"] > 10000
    )
    total, windows, offset = manifest["packet"]["total"], [], 0
    while offset + 1600 < total:
        piece = workflow.packet_text(manifest["task_token"], "verifier", offset)
        assert len(piece["text"]) <= 1600
        windows.append(piece["text"])
        offset = piece["end"]
    with pytest.raises(EvidenceMismatch, match="research packet JSON window"):
        workflow.read_source(manifest["task_token"])
    with pytest.raises(EvidenceMismatch, match="research packet JSON window"):
        workflow.submit(
            manifest["task_token"],
            "verifier",
            {
                "input_hash": manifest["input_hash"],
                "decision": "approve",
                "checked_card_ids": [card["id"]],
            },
        )
    windows.append(workflow.packet_text(manifest["task_token"], "verifier", offset)["text"])
    packet = json.loads("".join(windows))
    assert set(external_ids) <= {source["id"] for source in packet["source_index"]}
    assert packet["result_schema"] and packet["instructions"]
    assert all("parts" not in source for source in packet["source_index"])
    read_all(workflow, packet)
    assert (
        workflow.read_source(manifest["task_token"], article_id=external_ids[-1])["article_id"]
        == external_ids[-1]
    )


def test_local_tool_failure_does_not_reduce_shared_model_capacity(workflow):
    run = workflow.enqueue(snapshot())
    packet = workflow.claim(run["run_id"], "local-contract-failure")
    status = workflow.fail(
        packet["task_token"],
        "local-contract-failure",
        "tool_error",
        details={"reason": "packet_truncated", "message": "Local contract packet was truncated"},
    )
    assert status["tasks"][0]["technical_failures"] == 1
    assert workflow.store.runtime_control.gate(SOL)["state"] == "normal"
    assert status["tasks"][0]["last_failure"]["details"]["reason"] == "packet_truncated"


def test_resume_clears_legacy_local_gate_without_revoking_a_live_probe(workflow, monkeypatch):
    control = workflow.store.runtime_control
    now = [time.time()]
    monkeypatch.setattr("hrs.processing.runtime_control.time.time", lambda: now[0])
    failure = control.failure("tool_error", "research:legacy-local", model=SOL)
    now[0] = failure["retry_at"] + 1
    first, second = workflow.enqueue(snapshot()), workflow.enqueue(snapshot("second-book"))
    probe = workflow.claim(first["run_id"], "live-probe")
    assert probe and control.gate(SOL)["state"] == "probing"
    workflow.resume(second["run_id"])
    assert control.gate(SOL)["state"] == "normal"
    assert workflow.renew_run_leases(first["run_id"], active_workers={"live-probe"}) == 1
    read_all(workflow, probe)
    workflow.submit(
        probe["task_token"], "live-probe", {"input_hash": probe["input_hash"], "cards": [draft()]}
    )


def test_resume_does_not_erase_confirmed_provider_incident(workflow, monkeypatch):
    control = workflow.store.runtime_control
    now = [time.time()]
    monkeypatch.setattr("hrs.processing.runtime_control.time.time", lambda: now[0])
    run = workflow.enqueue(snapshot())
    first = control.failure("rate_limit", "provider:real-429", model=SOL)
    now[0] = first["retry_at"] + 1
    control.failure("tool_error", "research:legacy-local-after-provider", model=SOL)
    workflow.resume(run["run_id"])
    assert control.gate(SOL)["state"] == "cooldown"


def test_v3_verifier_gets_frozen_search_audit_after_source_gate_and_checks_each_receipt(workflow):
    run = workflow.enqueue(snapshot())
    submit_draft(workflow, run["run_id"], "author", "author")
    packet = workflow.claim(run["run_id"], "enricher", stage="enrich")
    read_all(workflow, packet)
    anchor = "unbounded copied anchor " * 2000
    receipt = workflow.record_search(
        packet["task_token"],
        {
            "query": "張三會議別名",
            "backend": "hybrid",
            "available": True,
            "hits": [
                {
                    "id": "hit-1",
                    "book_id": "book-1",
                    "chapter_id": "article-1",
                    "run_id": "publication-1",
                    "start": 0,
                    "end": 5,
                    "text": "第一頁記張",
                    "reading_target": {
                        "anchor": anchor,
                        "start": 0,
                        "end": 5,
                        "text_sha256": digest(snapshot()["articles"][0]["text"]),
                    },
                }
            ],
            "metrics": {"subqueries": [{"query": "張三", "lexical_ms": 1.5}], "results": 1},
        },
        preview_summary={"total_hits": 1, "returned_hits": 0, "omitted_hits": 1, "truncated": True},
    )
    failed = workflow.record_search(
        packet["task_token"],
        {
            "query": "另一次查詢",
            "backend": "hybrid",
            "available": False,
            "hits": [],
            "metrics": {},
            "error": {"message": "Temporary backend failure"},
        },
    )
    workflow.submit(
        packet["task_token"],
        "enricher",
        {"input_hash": packet["input_hash"], "cards": packet["cards"]},
    )
    verifier = workflow.claim(run["run_id"], "verifier", stage="verify")
    source = verifier["source_index"][0]
    observations = {
        "article_id": source["id"],
        "text_sha256": source["text_sha256"],
        "summary": "先獨立讀來源",
    }
    with pytest.raises(EvidenceMismatch, match="complete original"):
        workflow.candidate(verifier["task_token"], "verifier", observations, paged=True)
    read_all(workflow, verifier)
    manifest = workflow.candidate(verifier["task_token"], "verifier", observations, paged=True)
    assert "research_audit" not in manifest
    chunks, offset = [], 0
    while offset < manifest["candidate"]["total"]:
        piece = workflow.candidate_text(verifier["task_token"], "verifier", offset)
        chunks.append(piece["text"])
        offset = piece["end"]
    candidate = json.loads("".join(chunks))
    receipts = candidate["research_audit"]["receipts"]
    assert [item["receipt_id"] for item in receipts] == [
        receipt["receipt_id"],
        failed["receipt_id"],
    ]
    assert receipts[0]["query"] == "張三會議別名" and receipts[1]["available"] is False
    assert receipts[0]["metrics"]["subqueries"][0]["lexical_ms"] == 1.5
    assert receipts[0]["preview"]["omitted_hits"] == 1
    assert receipts[0]["hits_manifest"][0]["reading_target"]["text_sha256"] == source["text_sha256"]
    assert anchor not in "".join(chunks)
    result = {
        "input_hash": verifier["input_hash"],
        "decision": "approve",
        **candidate["required_checks"],
    }
    with pytest.raises(EvidenceMismatch, match="every server search receipt"):
        workflow.submit(
            verifier["task_token"], "verifier", {**result, "checked_search_receipt_ids": []}
        )
    assert workflow.submit(verifier["task_token"], "verifier", result)["state"] == "completed"


def test_v3_business_prompt_and_old_v2_receipt_contract_remain_frozen(workflow, monkeypatch):
    from hrs.research import policy
    from hrs.research.execution import role_configs

    current = deepcopy(policy.POLICY)
    legacy = {**current, "version": 2, "verify_search_audit": False}
    assert (
        "never put runtime tool names"
        in role_configs(current)["research_card_author"]["instructions"]
    )
    assert (
        "never put runtime tool names"
        not in role_configs(legacy)["research_card_author"]["instructions"]
    )
    monkeypatch.setattr(policy, "POLICY", legacy)
    previous = completed(workflow)
    card = workflow.list_cards(run_id=previous["run_id"])[0]
    assert card["checks"]["checked_search_receipt_ids"] == []
    monkeypatch.setattr(policy, "POLICY", current)
    revision = workflow.enqueue(
        snapshot(),
        revision_of=previous["run_id"],
        reason="Separate server audit from business card",
    )
    assert (
        revision["run_id"] != previous["run_id"]
        and revision["policy"]["version"] == current["version"]
    )
    assert (
        workflow.card(card["id"])["version"] == 1
        and workflow.card(card["id"])["state"] == "verified"
    )


def test_v4_decoded_mechanics_distinguish_line_feeds_and_literal_backslash_n(workflow):
    text = "首行\n次行\\n字面\r\n終行"
    run = workflow.enqueue(snapshot(text=text))
    card = draft(text=text)
    submit_draft(workflow, run["run_id"], "author", "author", [card])
    submit_draft(workflow, run["run_id"], "enrich", "enricher")
    packet = workflow.claim(run["run_id"], "verifier", stage="verify")
    source = packet["source_index"][0]
    observations = {
        "article_id": source["id"],
        "text_sha256": source["text_sha256"],
        "summary": "先獨立閱讀含換行的來源",
    }
    with pytest.raises(EvidenceMismatch, match="complete original"):
        workflow.candidate(packet["task_token"], "verifier", observations, paged=True)
    read_all(workflow, packet)
    manifest = workflow.candidate(packet["task_token"], "verifier", observations, paged=True)
    chunks, offset = [], 0
    while offset < manifest["candidate"]["total"]:
        piece = workflow.candidate_text(packet["task_token"], "verifier", offset)
        assert len(piece["text"]) <= 1600
        chunks.append(piece["text"])
        offset = piece["end"]
    candidate = json.loads("".join(chunks))
    receipt = candidate["mechanical_evidence"]["receipts"][0]
    assert receipt["quote_exact"] and receipt["source_hash_verified"]
    assert receipt["decoded_quote"]["sha256"] == receipt["source_span"]["sha256"] == digest(text)
    assert (
        receipt["decoded_quote"]["line_feed_u000a"]
        == receipt["source_span"]["line_feed_u000a"]
        == 2
    )
    assert receipt["decoded_quote"]["carriage_return_u000d"] == 1
    assert receipt["decoded_quote"]["literal_backslash_then_n"] == 1
    assert receipt["decoded_quote"]["unicode_codepoints"] == len(text)
    assert receipt["decoded_quote"]["utf8_bytes"] == len(text.encode("utf-8"))
    assert len(json.dumps(receipt)) < 1600 and "首行" not in json.dumps(receipt, ensure_ascii=False)
    result = {
        "input_hash": packet["input_hash"],
        "decision": "approve",
        **candidate["required_checks"],
    }
    with pytest.raises(EvidenceMismatch, match="every decoded mechanical"):
        workflow.submit(
            packet["task_token"], "verifier", {**result, "checked_mechanical_evidence_ids": []}
        )
    assert workflow.submit(packet["task_token"], "verifier", result)["state"] == "completed"


def test_literal_backslash_n_cannot_replace_an_actual_source_line_feed(workflow):
    text = "首行\n次行"
    run = workflow.enqueue(snapshot(text=text))
    packet = workflow.claim(run["run_id"], "author")
    read_all(workflow, packet)
    card = draft(text=text)
    card["evidence"][0]["quote"] = text.replace("\n", "\\n")
    with pytest.raises(EvidenceMismatch, match="quote does not match"):
        workflow.submit(
            packet["task_token"], "author", {"input_hash": packet["input_hash"], "cards": [card]}
        )


@pytest.mark.parametrize("version", [1, 2, 3])
def test_v4_does_not_add_mechanical_contract_to_older_policies(workflow, monkeypatch, version):
    from hrs.research import policy
    from hrs.research.execution import role_configs

    current = deepcopy(policy.POLICY)
    older = {**current, "version": version}
    assert (
        "Policy v4 decoded evidence" not in role_configs(older)["research_verifier"]["instructions"]
    )
    monkeypatch.setattr(policy, "POLICY", older)
    run = workflow.enqueue(snapshot())
    submit_draft(workflow, run["run_id"], "author", "author")
    submit_draft(workflow, run["run_id"], "enrich", "enricher")
    packet = workflow.claim(run["run_id"], "verifier", stage="verify")
    read_all(workflow, packet)
    source = packet["source_index"][0]
    candidate = workflow.candidate(
        packet["task_token"],
        "verifier",
        {
            "article_id": source["id"],
            "text_sha256": source["text_sha256"],
            "summary": "舊政策獨立來源閱讀",
        },
    )
    assert (
        "mechanical_evidence" not in candidate
        and "checked_mechanical_evidence_ids" not in candidate["required_checks"]
    )
    status = workflow.submit(
        packet["task_token"],
        "verifier",
        {"input_hash": packet["input_hash"], "decision": "approve", **candidate["required_checks"]},
    )
    assert status["state"] == "completed" and status["policy"]["version"] == version
