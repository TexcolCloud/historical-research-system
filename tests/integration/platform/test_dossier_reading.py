"""Synthetic v7 reading contracts; no native inference or accuracy approval."""

import json
import re
from copy import deepcopy
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update

from hrs.library.books import get_run
from hrs.processing import research_workflow
from hrs.research.api import CardNotesUpdate
from hrs.research.discovery import ResearchDiscovery
from hrs.research.models import DocumentMetadata
from hrs.research.sources import publication_snapshot
from hrs.search.indexing import BookIndexer
from hrs.storage import models as db
from tests.integration.platform.test_card_only_auto import (
    card_book as card_book,
)
from tests.integration.platform.test_card_only_auto import (
    completed_card as completed_card,
)
from tests.integration.platform.test_card_only_auto import (
    index_again,
    service_app,
)
from tests.integration.platform.test_research import research_book as research_book
from tests.unit.research.test_dossier_cards import dossier

DOSSIER_POLICY = deepcopy(research_workflow.POLICY)
HEADINGS = [
    "一、基本信息与完整出处",
    "二、研究对象与时空边界",
    "三、形成背景与文本结构",
    "四、核心内容",
    "五、史料性质、来源角色与局限",
    "六、史料价值",
    "七、证据边界",
    "八、可用论证单元",
    "九、规范引注",
    "十、关联人物、事件与史料",
    "十一、证据与页码映射",
]
PERSONAL_NOTE = "私人阅读设想PersonalNoteNotSource917"
DERIVED_VALUE = "限于本文陈述的史料价值DossierValue917"


@pytest.fixture
def dossier_book(research_book, monkeypatch):
    # The reused fixture intentionally installs v4; capture the current v7 at collection.
    assert DOSSIER_POLICY["version"] == 7 and DOSSIER_POLICY["dossier_cards"]
    monkeypatch.setattr(research_workflow, "POLICY", deepcopy(DOSSIER_POLICY))
    _, engine, bridge, _, _, chapter_id, first_page = research_book
    with engine.begin() as connection:
        content_ref = connection.scalar(
            select(db.chapters.c.content).where(db.chapters.c.id == chapter_id)
        )
        content = json.loads(bridge.objects.read_bytes(content_ref))
        tail = "\n（续页，未载补充事项。）\n"
        original = content["parts"][0]
        content["parts"].append(
            {
                "span_id": "second-page",
                "start": original["end"],
                "end": original["end"] + len(tail),
                "text": tail,
                "source": {
                    **original["source"],
                    "pages": [5],
                    "page": 5,
                    "original_start": 0,
                    "original_end": len(tail),
                },
            }
        )
        connection.execute(
            update(db.chapters)
            .where(db.chapters.c.id == chapter_id)
            .values(
                content=bridge.objects.put_bytes(json.dumps(content, ensure_ascii=False).encode()),
                pages=[4, 5],
                codepoints=len(first_page) + len(tail),
            )
        )
    return research_book


def finish_dossier(book, *, decision="approve"):
    """Exercise native author/source-first independent verify using explicit synthetic payloads."""
    _, _, bridge, book_id, _, _, first_page = book
    bridge.tick()
    workflow = bridge.workflow
    run = workflow.list_runs(book_id)[0]
    article = workflow.snapshot(run["run_id"])["articles"][0]
    mentions, findings = [], []
    for identity, kind, name in (
        ("person", "person", "人物甲"),
        ("document", "document", "Synthetic article"),
    ):
        start = article["text"].index(name)
        location = {
            "article_id": article["id"],
            "start": start,
            "end": start + len(name),
            "quote": name,
        }
        mentions.append(
            {
                "id": identity,
                "kind": kind,
                "name": name,
                "identity_key": article["id"] + ":" + identity,
                "identity_basis": "合成原文中的人物和篇名。",
                "evidence_ids": ["e1"],
                "locations": [location],
            }
        )
        findings.append({"id": identity, "kind": kind, **location, "description": name})
    claim = "人物甲提出主张。"
    start = article["text"].index(claim)
    for identity, kind in (("claim", "fact"), ("appears-in", "relation")):
        findings.append(
            {
                "id": identity,
                "kind": kind,
                "article_id": article["id"],
                "start": start,
                "end": start + len(claim),
                "quote": claim,
                "description": "人物甲在本文提出主张。",
            }
        )
    explanation = dossier()
    for key, value in explanation.items():
        if isinstance(value, dict) and "value" in value:
            value.update(value=f"{key}：人物甲在本文提出主张，不能推定已被采纳。")
    explanation["research_value"]["value"] = DERIVED_VALUE
    explanation["argument_units"][0].update(
        title="原文主张与后续采纳的边界",
        claim="人物甲提出主张，但本件不能证明主张已被采纳。",
        applicability_limit="仅限本文所载陈述。",
        corroboration_needed="若研究采纳情况，仍需后续记录。",
    )
    explanation["citation"].update(
        full={
            "value": "《Synthetic article》（出版信息未详）",
            "certainty": "inferred",
            "evidence_ids": ["e1"],
            "note": "据原文标题整理，未伪造作者、出版年代或印刷页。",
        },
        short={
            "value": "《Synthetic article》",
            "certainty": "stated",
            "evidence_ids": ["e1"],
            "note": "",
        },
        completeness="partial",
    )
    draft = {
        "id": "master",
        "article_id": article["id"],
        "title": article["title"],
        "summary": "合成软件契约：人物甲提出主张，未详后续。",
        "metadata": DocumentMetadata().model_dump(),
        "dossier": explanation,
        "evidence": [
            {
                "id": "e1",
                "article_id": article["id"],
                "start": 0,
                "end": len(first_page),
                "quote": first_page,
                "footnote_ids": [note["id"] for note in article["footnotes"]],
            }
        ],
        "items": [{"id": "claim", "kind": "evidence", "text": claim, "evidence_ids": ["e1"]}],
        "mentions": mentions,
        "relations": [
            {
                "id": "appears-in",
                "subject_id": "person",
                "predicate": "appears_in",
                "object_id": "document",
                "reason": "此人物主张见于本文。",
                "evidence_ids": ["e1"],
            }
        ],
        "coverage": [
            {
                "id": f"part-{index}",
                "article_id": article["id"],
                "start": part["start"],
                "end": part["end"],
                "summary": "正文与原注。" if index == 0 else "续页未载补充事项。",
                "mention_ids": ["person", "document"] if index == 0 else [],
                "relation_ids": ["appears-in"] if index == 0 else [],
            }
            for index, part in enumerate(article["parts"])
        ],
    }
    stages = (
        ("author", "verify")
        if decision == "approve"
        else ("author", "verify", "repair", "reverify")
    )
    for stage in stages:
        worker = "synthetic-dossier-" + stage
        packet = workflow.claim(run["run_id"], worker, stage=stage)
        for source in packet["source_index"]:
            workflow.read_source(packet["task_token"], article_id=source["id"])
        if stage in {"author", "repair"}:
            result = {"cards": [draft]}
        else:
            candidate = workflow.candidate(
                packet["task_token"],
                worker,
                {
                    "article_id": article["id"],
                    "text_sha256": article["text_sha256"],
                    "summary": "独立通读合成正文、原注和续页。",
                    "inventory": {
                        "coverage": [
                            {"start": p["start"], "end": p["end"], "summary": "逐段通读。"}
                            for p in article["parts"]
                        ],
                        "findings": findings,
                    },
                },
            )
            assert candidate["required_checks"]["checked_dossier_fields"]
            result = {
                "decision": decision,
                **candidate["required_checks"],
                "coverage_assessments": [
                    {
                        "finding_id": finding["id"],
                        "status": "covered",
                        "candidate_ids": ["master/" + finding["id"]],
                        "reason": "同篇精确原文与对应实体、事实和关系。",
                    }
                    for finding in findings
                ],
                "issues": ["合成待人工核对边界。"] if decision == "pending" else [],
            }
        run = workflow.submit(
            packet["task_token"], worker, {"input_hash": packet["input_hash"], **result}
        )
    bridge.projection.sync(run)
    return workflow.card(run["card_ids"][0])


@pytest.fixture
def completed_dossier(dossier_book):
    return dossier_book, finish_dossier(dossier_book)


def test_matching_index_automatically_queues_v7_once(dossier_book, monkeypatch):
    _, engine, bridge, book_id, source_run, _, _ = dossier_book
    publication, document, _ = index_again(dossier_book, monkeypatch)
    bridge.tick()
    assert bridge.workflow.list_runs(book_id) == []
    publication.index_if_needed(get_run(engine, source_run), document)
    bridge.reconcile_at = 0
    bridge.tick()
    queued = bridge.workflow.list_runs(book_id)[0]
    assert queued["policy"]["version"] == 7 and queued["policy"]["dossier_cards"]
    assert [(task["stage"], task["state"]) for task in queued["tasks"]] == [("author", "ready")]
    publication, document, _ = index_again(dossier_book, monkeypatch)
    publication.index_if_needed(get_run(engine, source_run), document)
    bridge.reconcile_at = 0
    bridge.tick()
    assert [run["run_id"] for run in bridge.workflow.list_runs(book_id)] == [queued["run_id"]]
    with engine.connect() as connection:
        assert (
            connection.scalar(
                select(func.count())
                .select_from(db.outbox)
                .where(db.outbox.c.kind == "start_research")
            )
            == 1
        )


def test_matching_v6_is_retained_without_upgrade_or_duplicate(dossier_book):
    settings, engine, bridge, book_id, source_run, _, _ = dossier_book
    snapshot = publication_snapshot(
        settings, engine, bridge.store, bridge.objects, get_run(engine, source_run)
    )
    old_policy = deepcopy(DOSSIER_POLICY)
    old_policy.update(version=6)
    old_policy.pop("dossier_cards")
    old = bridge.workflow.enqueue(snapshot, _policy=old_policy)
    bridge.projection.sync(old)
    bridge.tick()
    bridge.reconcile_at = 0
    bridge.tick()
    retained = bridge.start(book_id)
    assert retained["run_id"] == old["run_id"] and retained["policy"] == old_policy
    assert len(bridge.workflow.list_runs(book_id)) == 1


def test_v7_http_summary_editable_dto_and_export_preserve_dossier_and_source(completed_dossier):
    book, native = completed_dossier
    _, _, bridge, book_id, source_run, chapter_id, _ = book
    _, app = service_app(book)
    base = f"/api/v2/research/cards/{native['id']}"
    before = deepcopy(bridge.workflow.card(native["id"]))
    with TestClient(app) as client:
        saved = client.patch(base + "/notes", json={"expected_version": 0, "text": PERSONAL_NOTE})
        assert saved.status_code == 200
        detail = client.get(base)
        assert detail.status_code == 200
        card = detail.json()
        assert card["dossier"] == native["dossier"]
        assert (
            card["checks"]["checked_dossier_fields"] == native["checks"]["checked_dossier_fields"]
        )
        assert card["pages"] == [4, 5] and card["evidence"][0]["pages"] == [4]
        assert (
            card["printed_pages"] == ""
            and card["dossier"]["citation"]["printed_pages"]["certainty"] == "unknown"
        )
        assert card["source_citation"] == native["dossier"]["citation"]["full"]["value"]
        summary = client.get(f"/api/v2/research/books/{book_id}/cards").json()["items"][0]
        for field in (
            "pages",
            "printed_pages",
            "source_citation",
            "dossier_available",
            "display_id",
        ):
            assert summary[field] == card[field]
        editable = client.get(base + "/draft")
        assert editable.status_code == 200
        draft = editable.json()["card"]
        assert draft["dossier"] == native["dossier"] and "human_notes" not in draft
        exported = client.get(base + "/export")
        assert exported.status_code == 200
        markdown = exported.text
        assert re.findall(r"^## (.+)$", markdown, re.M) == [
            *HEADINGS,
            "我的阅读笔记（用户记录，非原文）",
        ]
        assert "**PDF物理页**：4、5" in markdown and "**原书页**：未详" in markdown
        assert "属于原文的注释。" in markdown and "[^research-1-" in markdown
        assert "人物甲 → appears_in → Synthetic article" in markdown
        for value in native["dossier"].values():
            if isinstance(value, dict) and "value" in value:
                assert value["value"] in markdown
        unit = native["dossier"]["argument_units"][0]
        assert all(
            unit[field] in markdown
            for field in ("claim", "applicability_limit", "corroboration_needed")
        )
        url = re.search(r"\[对照原文\]\(([^)]+)\)", markdown).group(1)
        query = parse_qs(urlsplit(url).fragment.split("?", 1)[1])
        assert query["chapter"] == [chapter_id] and query["physicalPage"] == ["4"]
        assert query["run"] == [source_run] and query["textSha"] == [
            card["evidence"][0]["text_sha256"]
        ]
        # The visible heading starts after '# '; exported links preserve that canonical target.
        assert query["start"] == ["2"] and query["end"] == [str(len(book[-1]))]
        audit = json.loads(re.search(r"```json\n(.+?)\n```", markdown, re.S).group(1))
        assert (
            audit["dossier"] == native["dossier"]
            and audit["publication_sha256"] == native["publication_sha256"]
        )
        assert audit["article_sha256"] == native["article_sha256"]
        assert audit["evidence"][0]["footnotes"] and "human_notes" not in audit
        assert PERSONAL_NOTE not in json.dumps(audit, ensure_ascii=False)
        notes_heading = markdown.index("## 我的阅读笔记")
        assert markdown.index(PERSONAL_NOTE) > notes_heading
        revision = client.post(
            base + "/revisions",
            json={
                "expected_version": native["version"],
                "card": draft,
                "reason": "合成DTO往返合同",
            },
        )
        assert revision.status_code == 200, revision.text
        status = revision.json()
        assert status["policy"]["version"] == 7 and status["policy"]["dossier_cards"]
        ready = [task for task in status["tasks"] if task["state"] == "ready"]
        assert len(ready) == 1 and ready[0]["stage"] == "verify"
        assert bridge.workflow._task_cards(ready[0])[0]["dossier"] == native["dossier"]
    assert bridge.workflow.card(native["id"]) == before


def test_projection_policy_four_rebuilds_only_derived_sql(completed_dossier):
    book, card = completed_dossier
    _, engine, bridge, _, _, _, _ = book
    run_id = card["run_id"]
    native_run = deepcopy(bridge.workflow.status(run_id))
    native_card = deepcopy(bridge.workflow.card(card["id"]))
    native_snapshot = deepcopy(bridge.workflow.snapshot(run_id))
    with engine.begin() as connection:
        old = connection.scalar(
            select(db.research_runs.c.summary).where(db.research_runs.c.id == run_id)
        )
        connection.execute(
            update(db.research_runs)
            .where(db.research_runs.c.id == run_id)
            .values(summary={**old, "projection_policy": 3})
        )
        connection.execute(
            update(db.research_cards)
            .where(db.research_cards.c.id == card["id"])
            .values(summary={"id": card["id"]})
        )
    assert run_id not in bridge.projection.versions()
    bridge.reconcile_at = 0
    bridge.tick()
    with engine.connect() as connection:
        projected = connection.scalar(
            select(db.research_cards.c.summary).where(db.research_cards.c.id == card["id"])
        )
        summary = connection.scalar(
            select(db.research_runs.c.summary).where(db.research_runs.c.id == run_id)
        )
    assert summary["projection_policy"] == 4
    assert (
        projected["pages"] == [4, 5]
        and projected["dossier_available"]
        and projected["printed_pages"] == ""
    )
    assert bridge.workflow.status(run_id) == native_run
    assert bridge.workflow.card(card["id"]) == native_card
    assert bridge.workflow.snapshot(run_id) == native_snapshot


@pytest.mark.parametrize("decision", ["approve", "pending"])
def test_discovery_indexes_v7_interpretations_only_after_approval(
    dossier_book, monkeypatch, decision
):
    card = finish_dossier(dossier_book, decision=decision)
    settings, engine, bridge, book_id, _, _, _ = dossier_book
    assert card["state"] == ("verified" if decision == "approve" else "pending")
    service, _ = service_app(dossier_book)
    service.update_card_notes(card["id"], CardNotesUpdate(expected_version=0, text=PERSONAL_NOTE))
    settings = settings.model_copy(
        update={
            "opensearch_url": "http://127.0.0.1:19269",
            "opensearch_index": "test-dossier-" + uuid4().hex,
        }
    )
    discovery = ResearchDiscovery(settings, engine, bridge.objects, bridge.store)
    monkeypatch.setattr(
        BookIndexer,
        "embed_cached",
        lambda self, run, records, metrics: {
            "chunks": [{**record, "vector": [1.0] + [0.0] * 1023} for record in records],
            "model": {"synthetic_contract_only": True},
        },
    )
    with engine.connect() as connection:
        summary = connection.scalar(
            select(db.research_runs.c.summary).where(db.research_runs.c.id == card["run_id"])
        )
    try:
        built = discovery.index(card["run_id"], summary["discovery_input_sha256"])
        found = discovery.search("DossierValue917", book_ids=[book_id], semantic=False)
        rows = discovery.client.search(
            index=discovery.name,
            body={"query": {"match_all": {}}, "size": 100, "_source": {"excludes": ["vector"]}},
        )["hits"]["hits"]
        if decision == "approve":
            assert built["chunks"] > 0 and found["records"]
            assert all(row["derived"] and not row["source_evidence"] for row in found["records"])
            assert any(DERIVED_VALUE in row["_source"]["text"] for row in rows)
        else:
            assert built["chunks"] == 0 and found["records"] == [] and rows == []
        assert PERSONAL_NOTE not in json.dumps(rows, ensure_ascii=False)
    finally:
        if discovery.client.indices.exists(index=discovery.name):
            discovery.client.indices.delete(index=discovery.name)


def test_v6_export_retains_the_existing_non_dossier_path(completed_card):
    book, card = completed_card
    _, app = service_app(book)
    with TestClient(app) as client:
        response = client.get(f"/api/v2/research/cards/{card['id']}/export")
    assert response.status_code == 200
    assert "## 卡片与来源版本" in response.text and "属于原文的注释。" in response.text
    assert "## 一、基本信息与完整出处" not in response.text
    assert "<summary>核验与版本记录</summary>" not in response.text
    assert "dossier" not in book[2].workflow.card_draft(card["id"])
