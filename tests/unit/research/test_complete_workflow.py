"""Policy v5 business acceptance on explicit synthetic historical statements."""

from copy import deepcopy

import pytest

from hrs.processing.errors import EvidenceMismatch, LeaseConflict, WorkflowStopped
from hrs.processing.hashing import digest
from hrs.processing.research_workflow import ResearchWorkflow
from hrs.processing.store import Store
from hrs.research.models import CardDraft, DocumentMetadata
from tests.unit.research.test_workflow import legacy_policy

TEXT = "1939年2月，张三在南京致函李四，申请经费。"


@pytest.fixture(autouse=True)
def frozen_v5_policy(monkeypatch):
    monkeypatch.setattr("hrs.research.policy.POLICY", legacy_policy(5))


def source(book="book-a"):
    split = TEXT.index("致函")
    return {
        "book_id": book,
        "source_run_id": "publication-" + book,
        "document_id": "a" * 24,
        "workspace_id": "synthetic",
        "title": "Synthetic dated letter",
        "publication_sha256": digest(TEXT),
        "content_verified": True,
        "validation_scope": "content",
        "articles": [
            {
                "id": "article-" + book,
                "title": "申请函",
                "text": TEXT,
                "text_sha256": digest(TEXT),
                "pages": [1, 2],
                "footnotes": [],
                "parts": [
                    {"id": str(i), "start": a, "end": b, "source": {"pages": [i + 1]}}
                    for i, (a, b) in enumerate([(0, split), (split, len(TEXT))])
                ],
            }
        ],
    }


def draft(book="book-a", *, event=True):
    article = "article-" + book
    metadata = DocumentMetadata().model_dump()
    metadata["title_origin"] = "system"
    for key, value in metadata.items():
        if isinstance(value, dict):
            value["note"] = "合成节选未载这项形成或出版资料。"
    metadata["authors"] = {
        "value": "张三",
        "certainty": "stated",
        "evidence_ids": ["e1"],
        "note": "明确发函者",
    }
    mentions = [
        {
            "id": identity,
            "kind": "person",
            "name": name,
            "identity_key": "person:" + name,
            "identity_basis": "函中明确的发出或收受者",
            "evidence_ids": ["e1"],
            "locations": [
                {
                    "article_id": article,
                    "start": TEXT.index(name),
                    "end": TEXT.index(name) + len(name),
                    "quote": name,
                }
            ],
        }
        for identity, name in [("sender", "张三"), ("recipient", "李四")]
    ]
    events = (
        [
            {
                "id": "letter",
                "name": "张三申请经费函",
                "identity_key": "letter:1939-02:张三:李四",
                "description": "张三在南京致函李四申请经费，未记已拨付。",
                "action": "致函申请经费",
                "event_status": "reported",
                "status_note": "原文明载发函申请，不等于经费已获拨付。",
                "date": {
                    "text": "1939年2月",
                    "normalized": "1939-02",
                    "certainty": "stated",
                    "evidence_ids": ["e1"],
                },
                "place": {
                    "value": "南京",
                    "certainty": "stated",
                    "evidence_ids": ["e1"],
                    "note": "原文地点",
                },
                "participant_ids": ["sender", "recipient"],
                "evidence_ids": ["e1"],
                "participant_roles": [
                    {
                        "mention_id": identity,
                        "role": role,
                        "description": role,
                        "evidence_ids": ["e1"],
                    }
                    for identity, role in [("sender", "sender"), ("recipient", "recipient")]
                ],
            }
        ]
        if event
        else []
    )
    result = {
        "id": "master",
        "article_id": article,
        "title": "张三申请经费函",
        "summary": "张三申请经费，材料未记拨付结果。",
        "metadata": metadata,
        "mentions": mentions,
        "events": events,
        "evidence": [
            {"id": "e1", "article_id": article, "start": 0, "end": len(TEXT), "quote": TEXT}
        ],
        "coverage": [
            {
                "id": "unit-" + part["id"],
                "article_id": article,
                "start": part["start"],
                "end": part["end"],
                "summary": "发函事项的连续原文片段。",
                "mention_ids": [m["id"] for m in mentions],
                "event_ids": ["letter"] if event else [],
                "relation_ids": [],
                "no_mentions_reason": "",
                "no_events_reason": "尚未抽出动作" if not event else "",
                "no_relations_reason": "单篇申请函未证实外部文书关系。",
            }
            for part in source(book)["articles"][0]["parts"]
        ],
        "research_plan": [
            {
                "id": "payment",
                "question": "本函能否证明款项已拨付？",
                "proposed_queries": ["张三 李四 经费"],
            }
        ],
    }
    return CardDraft.model_validate(result).model_dump(mode="json")


def read(workflow, packet):
    run = workflow.status(packet["run_id"])
    if run.get("native_execution_id"):
        workflow.bind_native_worker(
            run["run_id"],
            packet["worker_id"],
            "native-" + packet["worker_id"],
            run["native_execution_id"],
        )
    for article in packet["source_index"]:
        offset = 0
        while offset < article["text_length"]:
            offset = workflow.read_source(
                packet["task_token"], offset, 1000, article_id=article["id"]
            )["end"]


def submit_author(workflow, run, card):
    packet = workflow.claim(run["run_id"], "author", stage="author")
    read(workflow, packet)
    workflow.submit(
        packet["task_token"], "author", {"input_hash": packet["input_hash"], "cards": [card]}
    )


def enrich(workflow, run, card, *, stage="enrich", worker="enricher", target=None):
    packet = workflow.claim(run["run_id"], worker, stage=stage)
    read(workflow, packet)
    if target:
        authorized = []
        manifest = workflow.target_card(
            packet["task_token"],
            worker,
            target["id"],
            target["version"],
            authorize=lambda card: authorized.append(card["id"]),
        )
        assert authorized == [target["id"]]
        read(workflow, {**packet, "source_index": manifest["source_index"]})
        read_target(workflow, packet, manifest)
        card = deepcopy(card)
        card["evidence"].append(
            {
                "id": "target-e1",
                "article_id": manifest["source_ids"][0],
                "start": 0,
                "end": len(TEXT),
                "quote": TEXT,
            }
        )
        card["cross_relations"] = [
            {
                "id": "same-versioned-text",
                "target_card_id": target["id"],
                "target_card_version": target["version"],
                "target_publication_sha256": target["publication_sha256"],
                "kind": "same_source",
                "reason": "两篇合成文书逐字相同，不能视为独立见证。",
                "certainty": "stated",
                "source_evidence_ids": ["e1"],
                "target_evidence_ids": ["target-e1"],
            }
        ]
    if stage == "enrich":
        workflow.record_plan(packet["task_token"], worker, card["research_plan"])
        receipt = workflow.record_search(
            packet["task_token"],
            {
                "query": "张三 李四 经费",
                "backend": "hybrid",
                "available": True,
                "hits": [],
                "metrics": {"synthetic": True},
            },
        )
        card = deepcopy(card)
        card["research_plan"][0].update(
            status="answered",
            search_receipt_ids=[receipt["receipt_id"]],
            evidence_ids=["e1"],
            evaluation="仅支持申请，不能据此证明实际支付。",
            stop_reason="限定于现存合成文献，未据空搜索断言全库不存在。",
        )
    workflow.record_plan(packet["task_token"], worker, card["research_plan"])
    workflow.submit(
        packet["task_token"], worker, {"input_hash": packet["input_hash"], "cards": [card]}
    )
    return card


def observation(book="book-a"):
    article = source(book)["articles"][0]
    return {
        "article_id": article["id"],
        "text_sha256": article["text_sha256"],
        "summary": "独立读取合成函件全文。",
        "inventory": {
            "coverage": [
                {
                    "start": part["start"],
                    "end": part["end"],
                    "summary": "读取连续函件的形成及行为信息。",
                }
                for part in article["parts"]
            ],
            "findings": [
                {
                    "id": identity,
                    "kind": kind,
                    "article_id": article["id"],
                    "start": start,
                    "end": end,
                    "quote": TEXT[start:end],
                    "description": description,
                    "required": True,
                    "event_status": "reported" if kind == "event" else "unknown",
                }
                for identity, kind, start, end, description in [
                    ("sender", "person", TEXT.index("张三"), TEXT.index("张三") + 2, "明确发函者"),
                    (
                        "recipient",
                        "person",
                        TEXT.index("李四"),
                        TEXT.index("李四") + 2,
                        "明确收函者",
                    ),
                    ("letter", "event", 0, len(TEXT), "具年月地点及双方角色的致函申请行为"),
                ]
            ],
            "no_findings_reason": "",
        },
    }


def read_target(workflow, packet, manifest):
    offset = 0
    while offset < manifest["total"]:
        offset = workflow.target_card_text(
            packet["task_token"], packet["worker_id"], manifest["card_ref"], offset, 2000
        )["end"]


def verify(
    workflow,
    run,
    *,
    decision="approve",
    stage="verify",
    worker="verifier",
    book="book-a",
    target=None,
):
    packet = workflow.claim(run["run_id"], worker, stage=stage)
    read(workflow, packet)
    if target:
        manifest = workflow.target_card(
            packet["task_token"],
            worker,
            target["id"],
            target["version"],
            authorize=lambda _: pytest.fail("Verifier used live authorization"),
        )
        with pytest.raises(EvidenceMismatch, match="observations"):
            workflow.target_card_text(packet["task_token"], worker, manifest["card_ref"])
    candidate = workflow.candidate(packet["task_token"], worker, observation(book))
    assessments = [
        {
            "finding_id": identity,
            "status": "covered" if identity != "letter" or decision == "approve" else "missing",
            "candidate_ids": ["master/" + identity]
            if identity != "letter" or decision == "approve"
            else [],
            "reason": "原文与结构化候选吻合。"
            if identity != "letter" or decision == "approve"
            else "明确动作未被抽取。",
        }
        for identity in ["sender", "recipient", "letter"]
    ]
    result = {
        "input_hash": packet["input_hash"],
        "decision": decision,
        **candidate["required_checks"],
        "coverage_assessments": assessments,
        "issues": ["漏掉明确致函事件及参与角色。"] if decision == "pending" else [],
    }
    if target:
        with pytest.raises(EvidenceMismatch, match="counterpart card window"):
            workflow.submit(packet["task_token"], worker, result)
        read_target(workflow, packet, manifest)
    return workflow.submit(packet["task_token"], worker, result)


def test_v5_independent_inventory_catches_empty_events_and_repairs_before_human_pending(tmp_path):
    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    run = workflow.enqueue(source())
    incomplete = draft(event=False)
    submit_author(workflow, run, incomplete)
    enriched = enrich(workflow, run, incomplete)
    after = verify(workflow, run, decision="pending")
    assert after["state"] == "running" and after["counts"]["native_pending"] == 0
    assert next(task for task in after["tasks"] if task["stage"] == "repair")["state"] == "ready"
    repaired = draft()
    repaired["research_plan"] = enriched["research_plan"]
    enrich(workflow, run, repaired, stage="repair", worker="repairer")
    assert workflow.claim(run["run_id"], "verifier", stage="reverify") is None
    final = verify(workflow, run, stage="reverify", worker="fresh-verifier")
    assert final["state"] == "completed"
    card = workflow.card(final["card_ids"][0])
    assert len(card["events"]) == 1 and len(card["events"][0]["participant_roles"]) == 2
    sender = next(item for item in workflow.catalogue_entities()["items"] if item["name"] == "张三")
    assert sender["participations"][0]["role"] == "sender"
    assert workflow.timeline(entity_id=sender["id"])["items"][0]["name"] == "张三申请经费函"


def completed(workflow, book="book-a"):
    run = workflow.enqueue(source(book), _policy=legacy_policy(5))
    submit_author(workflow, run, draft(book))
    enrich(workflow, run, draft(book))
    final = verify(workflow, run, book=book)
    return workflow.card(final["card_ids"][0])


def test_claim_inventory_example_agrees_with_required_event_status_schema(tmp_path):
    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    run = workflow.enqueue(source())
    submit_author(workflow, run, draft())
    enrich(workflow, run, draft())
    packet = workflow.claim(run["run_id"], "schema-review", stage="verify")
    instruction = packet["instructions"]["complete_research"]
    assert "event_status:reported|planned|conditional|negated|unknown" in instruction
    assert "Use unknown for non-event findings" in instruction
    finding = packet["observations_schema"]["$defs"]["IndependentFinding"]
    assert "event_status" in finding["required"]
    assert set(finding["properties"]["event_status"]["enum"]) == {
        "reported",
        "planned",
        "conditional",
        "negated",
        "unknown",
    }


def test_quote_locator_requires_real_source_read_and_preserves_candidate_gate(tmp_path):
    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    run = workflow.enqueue(source())
    workflow.start_execution(run["run_id"], "execution-quotes")
    packet = workflow.claim(run["run_id"], "quote-worker", stage="author", paged=True)
    token = packet["task_token"]
    with pytest.raises(LeaseConflict, match="attestation"):
        workflow.locate_quotes(token, "quote-worker", ["张三"])
    workflow.bind_native_worker(run["run_id"], "quote-worker", "native-quotes", "execution-quotes")
    with pytest.raises(EvidenceMismatch, match="packet"):
        workflow.locate_quotes(token, "quote-worker", ["张三"])
    offset = 0
    while True:
        window = workflow.packet_text(token, "quote-worker", offset, 2000)
        offset = window["end"]
        if offset == window["total"]:
            break
    workflow.read_source(token, 0, 5)
    with pytest.raises(EvidenceMismatch, match="complete frozen source"):
        workflow.locate_quotes(token, "quote-worker", ["张三"])
    workflow.read_source(token, 5, 1000)
    with workflow.ledger.connect() as connection:
        before = workflow._reads(connection, token)
    located = workflow.locate_quotes(token, "quote-worker", ["张三", "不存在", "张三"])
    assert located["quote_counts"] == [
        {"quote_index": 0, "total_matches": 1},
        {"quote_index": 1, "total_matches": 0},
        {"quote_index": 2, "total_matches": 1},
    ]
    assert located["matches"][0]["start"] == TEXT.index("张三")
    with workflow.ledger.connect() as connection:
        assert workflow._reads(connection, token) == before
    with pytest.raises(EvidenceMismatch, match="outside"):
        workflow.locate_quotes(token, "quote-worker", ["张三"], article_id="unauthorized-article")
    with pytest.raises(LeaseConflict):
        workflow.locate_quotes(token, "other-worker", ["张三"])
    workflow.pause(run["run_id"])
    with pytest.raises((WorkflowStopped, LeaseConflict)):
        workflow.locate_quotes(token, "quote-worker", ["张三"])


def test_structured_revision_keeps_previous_publication_until_independent_review(tmp_path):
    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    original = completed(workflow)
    edited = deepcopy(original)
    edited["metadata"]["purpose"] = {
        "value": "申请经费",
        "certainty": "stated",
        "evidence_ids": ["e1"],
        "note": "原文明示申请事项",
    }
    revised = workflow.card_revision(original["id"], 1, edited, reason="补全材料写作目的")
    assert revised["purpose"] == "card_revision" and revised["state"] == "running"
    assert workflow.card(original["id"])["state"] == "verified"
    assert (
        workflow.card_revision(original["id"], 1, edited, reason="补全材料写作目的")["run_id"]
        == revised["run_id"]
    )
    with pytest.raises(LeaseConflict):
        workflow.card_revision(original["id"], 2, edited)
    final = verify(workflow, revised, worker="revision-verifier")
    replacement = workflow.card(final["card_ids"][0])
    assert replacement["id"] != original["id"]
    assert replacement["replaces_card_id"] == original["id"]
    assert replacement["lineage_id"] == original["lineage_id"]
    assert replacement["metadata"]["purpose"]["value"] == "申请经费"
    assert workflow.card(original["id"])["metadata"]["purpose"]["certainty"] == "unknown"


def test_independent_observation_cannot_approve_source_findings_missing_from_candidate(tmp_path):
    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    run = workflow.enqueue(source())
    submit_author(workflow, run, draft(event=False))
    enrich(workflow, run, draft(event=False))
    with pytest.raises(EvidenceMismatch, match="matching source-backed"):
        verify(workflow, run, decision="approve")


def test_reverification_rejects_initial_native_verifier_even_with_another_worker_id(tmp_path):
    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    run = workflow.enqueue(source())
    workflow.start_execution(run["run_id"], "native-fixture")
    incomplete = draft(event=False)
    submit_author(workflow, run, incomplete)
    enriched = enrich(workflow, run, incomplete)
    verify(workflow, run, decision="pending")
    repaired = draft()
    repaired["research_plan"] = enriched["research_plan"]
    enrich(workflow, run, repaired, stage="repair", worker="repairer")
    packet = workflow.claim(run["run_id"], "disguised-verifier", stage="reverify")
    workflow.bind_native_worker(
        run["run_id"], "disguised-verifier", "native-verifier", "native-fixture"
    )
    for article in packet["source_index"]:
        workflow.read_source(packet["task_token"], 0, 1000, article_id=article["id"])
    with pytest.raises(EvidenceMismatch, match="same native agent"):
        workflow.candidate(packet["task_token"], "disguised-verifier", observation())


def pending_card(workflow):
    run = workflow.enqueue(source())
    submit_author(workflow, run, draft(event=False))
    enriched = enrich(workflow, run, draft(event=False))
    verify(workflow, run, decision="pending")
    enrich(workflow, run, enriched, stage="repair", worker="repairer")
    final = verify(workflow, run, decision="pending", stage="reverify", worker="fresh-review")
    return workflow.card(final["card_ids"][0])


def test_pending_card_is_not_offered_for_parallel_human_decision_during_revision(tmp_path):
    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    original = pending_card(workflow)
    assert workflow.pending_items()["items"][0]["card_id"] == original["id"]
    candidate = workflow.card_draft(original["id"])
    candidate["summary"] += "仅能证明申请。"
    run = workflow.card_revision(original["id"], 1, candidate)
    assert workflow.pending_items()["items"] == []
    assert workflow.card(original["id"])["state"] == "pending"
    with pytest.raises(LeaseConflict, match="revision"):
        workflow.decision(original["id"], "accept", 1)
    workflow.pause(run["run_id"])
    assert workflow.pending_items()["items"][0]["card_id"] == original["id"]
    accepted = workflow.decision(original["id"], "accept", 1)
    workflow.resume(run["run_id"])
    final = verify(workflow, run, decision="pending", worker="revision-review")
    assert final["state"] == "superseded" and final["card_ids"] == []
    assert workflow.card(original["id"])["version"] == accepted["version"]
    assert workflow.claim(run["run_id"], "stale-coordinator", stage="verify") is None
    assert workflow.pause(run["run_id"])["state"] == "superseded"
    assert workflow.runtime_failure(run["run_id"], {"code": "tool_error"})["state"] == "superseded"


def test_source_reported_action_cannot_be_approved_as_a_completed_plan(tmp_path):
    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    run = workflow.enqueue(source())
    candidate = draft()
    candidate["events"][0]["event_status"] = "planned"
    submit_author(workflow, run, candidate)
    enrich(workflow, run, candidate)
    with pytest.raises(EvidenceMismatch, match="Event status"):
        verify(workflow, run)


@pytest.mark.parametrize("human,preserve", [(False, False), (True, False), (False, True)])
def test_later_book_backlinks_are_frozen_and_visible_from_original_card(tmp_path, human, preserve):
    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    original_draft = draft()
    original_snapshot = source()
    if preserve:
        original_snapshot["research_questions"] = [
            {"id": f"question-{index}", "definition_version": 1} for index in range(11)
        ]
        original_draft["question_findings"] = [
            {
                "id": f"finding-{index}",
                "question_id": f"question-{index}",
                "definition_version": 1,
                "text": "本函仅载申请，未载拨付结果。",
                "status": "partial",
                "evidence_ids": ["e1"],
                "unresolved": ["实际拨付需要后续独立材料。"],
            }
            for index in range(11)
        ]
    original_run = workflow.enqueue(original_snapshot)
    submit_author(workflow, original_run, original_draft)
    enrich(workflow, original_run, original_draft)
    original = workflow.card(verify(workflow, original_run)["card_ids"][0])
    target = completed(workflow, "book-b")
    if human:
        original = workflow.decision(original["id"], "accept", 1)
    snapshot = source()
    snapshot["research_scope"] = {
        "kind": "link_refresh",
        "source_card_id": original["id"],
        "preserve_source_card": preserve,
    }
    snapshot["link_target_cards"] = [
        {
            "card_id": target["id"],
            "version": target["version"],
            "title": target["title"],
            "publication_sha256": target["publication_sha256"],
        }
    ]
    linking_draft = draft()
    if preserve:
        snapshot["research_questions"] = original_snapshot["research_questions"][:10]
        linking_draft["question_findings"] = original_draft["question_findings"][:10]
    run = workflow.enqueue(snapshot, purpose="link_refresh")
    submit_author(workflow, run, linking_draft)
    enrich(workflow, run, linking_draft, target=target)
    final = verify(workflow, run, worker="link-review", target=target)
    package = workflow.card(final["card_ids"][0])
    assert package["purpose"] == "link_refresh"
    assert package["replaces_card_id"] == (None if human or preserve else original["id"])
    assert package["linked_card_id"] == (original["id"] if human or preserve else None)
    edges = workflow.card_relationships(original["id"])["items"]
    assert len(edges) == 1 and edges[0]["target_card_id"] == target["id"]
    assert edges[0]["card_id"] == package["id"]
    assert {e["book_id"] for e in edges[0]["evidence"]} == {"book-a", "book-b"}
    assert workflow.relation(edges[0]["id"])["target_card_version"] == target["version"]
    assert workflow.card(original["id"])["state"] == ("human_accepted" if human else "verified")
    if preserve:
        assert len(workflow.card(original["id"])["question_findings"]) == 11
        assert len(package["question_findings"]) == 10
        assert all(
            mention["card_id"] == original["id"]
            for entity in workflow.catalogue_entities(book_id="book-a")["items"]
            for mention in entity["source_mentions"]
        )
