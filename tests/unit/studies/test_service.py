"""Synthetic personal-study persistence contracts, not historical accuracy tests."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from hrs.library.errors import ServiceError
from hrs.processing.store import Store
from hrs.studies.service import StudyService


def research_stub(tmp_path):
    card = {
        "id": "card",
        "version": 1,
        "state": "verified",
        "source_current": True,
        "book_id": "00000000-0000-0000-0000-000000000001",
        "article_id": "article",
        "source_run_id": "source-run",
        "publication_sha256": "published",
        "article_sha256": "article-hash",
        "title": "Synthetic source",
        "superseded": False,
        "evidence": [
            {
                "id": "e1",
                "article_id": "article",
                "quote": "甲",
                "start": 0,
                "end": 1,
                "pages": [9],
                "footnote_ids": [],
                "text_sha256": "hash",
            }
        ],
    }

    def current_card(identity, version=None):
        if identity != card["id"] or version != card["version"] or not card["source_current"]:
            raise ServiceError(409, "来源已变化")
        return deepcopy(card)

    def gate():
        return SimpleNamespace(
            current_cards=[
                {
                    "id": card["id"],
                    "version": card["version"],
                    "book_id": card["book_id"],
                    "publication_sha256": card["publication_sha256"],
                }
            ]
            if card["source_current"]
            else []
        )

    workspace = SimpleNamespace(
        current_card=current_card, _network_query=gate, available_books=lambda: [card["book_id"]]
    )
    return SimpleNamespace(bridge=SimpleNamespace(store=Store(tmp_path)), workspace=workspace), card


def test_material_references_and_canvas_remain_versioned_personal_records(tmp_path):
    research, card = research_stub(tmp_path)
    service = StudyService(research)
    topic = service.create_topic({"title": "专题"})
    topic = service.add_material(
        topic["id"], {"expected_version": 1, "card_id": "card", "card_version": 1}
    )
    material = topic["materials"][0]
    assert material["source_current"] and not material["evidence"]
    note = service.create_record(
        topic["id"], {"kind": "argument", "title": "个人判断", "source_refs": [material["id"]]}
    )
    canvas = service.create_canvas(
        topic["id"],
        {
            "title": "画布",
            "document": {
                "nodes": [
                    {"id": "node", "record_kind": "record", "record_id": note["id"], "x": 1, "y": 2}
                ],
            },
        },
    )
    assert service.canvas(canvas["id"])["document"]["nodes"][0]["record_id"] == note["id"]
    card.update(source_current=False, version=2)
    assert not service.topic(topic["id"])["materials"][0]["source_current"]
    retained = service.material(topic["id"], material["id"])
    assert retained["card_version"] == 1 and retained["evidence"][0]["quote"] == "甲"
    changed = service.update_canvas(canvas["id"], {"expected_version": 1, "title": "移动后的画布"})
    assert changed["version"] == 2
    with pytest.raises(ServiceError):
        service.remove_material(topic["id"], material["id"], expected_version=topic["version"])
    assert service.record(note["id"])["origin"] == "personal"


def test_canvas_exports_all_referenced_records_and_owned_footnotes(tmp_path):
    research, card = research_stub(tmp_path)
    card["evidence"][0]["footnotes"] = [{"id": "note-1", "text": "原文注释", "number": "1"}]
    card["evidence"][0]["footnote_ids"] = ["note-1"]
    service = StudyService(research)
    topic = service.create_topic({"title": "导出专题"})
    topic = service.add_material(
        topic["id"], {"expected_version": 1, "card_id": "card", "card_version": 1}
    )
    material = topic["materials"][0]
    for index in range(201):
        record = service.create_record(
            topic["id"], {"kind": "note", "title": f"札记{index}", "source_refs": [material["id"]]}
        )
    canvas = service.create_canvas(
        topic["id"],
        {
            "title": "备份",
            "document": {
                "nodes": [
                    {
                        "id": "source",
                        "record_kind": "source",
                        "record_id": material["id"],
                        "x": 0,
                        "y": 0,
                    },
                    {
                        "id": "note",
                        "record_kind": "record",
                        "record_id": record["id"],
                        "x": 300,
                        "y": 0,
                    },
                ]
            },
        },
    )
    import json

    backup = json.loads(service.export_canvas(canvas["id"]))
    assert [item["id"] for item in backup["records"]] == [record["id"]]
    assert backup["topic"]["materials"][0]["evidence"][0]["quote"] == "甲"
    markdown = service.export_canvas(canvas["id"], format="markdown")
    assert "原文注释" in markdown and "note-1" in markdown
    assert "PDF物理页" in markdown and "印刷页码未提供" in markdown


def test_import_uses_local_frozen_sources_and_rolls_back_failed_adoption(tmp_path, monkeypatch):
    import json

    research, card = research_stub(tmp_path)
    service = StudyService(research)
    topic = service.create_topic({"title": "原专题"})
    topic = service.add_material(
        topic["id"], {"expected_version": 1, "card_id": "card", "card_version": 1}
    )
    material_id = topic["materials"][0]["id"]
    note = service.create_record(
        topic["id"], {"kind": "thought", "title": "个人记录", "source_refs": [material_id]}
    )
    canvas = service.create_canvas(
        topic["id"],
        {
            "title": "原画布",
            "document": {
                "nodes": [
                    {
                        "id": "note",
                        "record_kind": "record",
                        "record_id": note["id"],
                        "x": 0,
                        "y": 0,
                    },
                ]
            },
        },
    )
    backup = json.loads(service.export_canvas(canvas["id"]))
    backup["topic"]["materials"][0]["evidence"][0]["quote"] = "伪造的导入文本"
    backup["records"][0]["origin"] = "ai_adopted"
    target = service.create_topic({"title": "恢复专题"})
    card.update(source_current=False, version=2)
    restored = service.import_canvas(target["id"], {"expected_version": 1, "backup": backup})
    assert restored["import_warnings"]
    restored_topic = service.topic(target["id"])
    assert restored_topic["book_ids"] == []
    retained = service.material(target["id"], restored_topic["materials"][0]["id"])
    assert retained["evidence"][0]["quote"] == "甲" and retained["card_version"] == 1
    assert service.record(restored["document"]["nodes"][0]["record_id"])["origin"] == "personal"
    another = service.create_topic({"title": "回滚专题"})
    original_create = service.store.create

    def fail_record(kind, *args, **kwargs):
        if kind == "record":
            raise RuntimeError("Synthetic interruption")
        return original_create(kind, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(service.store, "create", fail_record)
        with pytest.raises(RuntimeError):
            service.import_canvas(another["id"], {"expected_version": 1, "backup": backup})
    assert service.topic(another["id"])["version"] == 1
    assert not service.store.list("material", topic_id=another["id"])["items"]
    backup["workspace_id"] = "foreign-workspace"
    with pytest.raises(ServiceError) as wrong_workspace:
        service.import_canvas(another["id"], {"expected_version": 1, "backup": backup})
    assert wrong_workspace.value.status_code == 409


def test_personal_record_dates_and_provenance_are_validated(tmp_path):
    research, _ = research_stub(tmp_path)
    service = StudyService(research)
    topic = service.create_topic({"title": "格式专题"})
    with pytest.raises(ServiceError):
        service.create_record(
            topic["id"], {"kind": "thought", "title": "伪造AI身份", "origin": "ai_adopted"}
        )
    with pytest.raises(ServiceError):
        service.create_record(
            topic["id"],
            {
                "kind": "clue",
                "title": "非法日期",
                "date": {
                    "text": "1936年2月30日",
                    "normalized": "1936-02-30",
                    "certainty": "stated",
                },
            },
        )
    assert not service.records(topic["id"])["items"]


def test_new_source_versions_are_added_without_replacing_old_references(tmp_path):
    research, card = research_stub(tmp_path)
    service = StudyService(research)
    topic = service.create_topic({"title": "修订专题"})
    topic = service.add_material(
        topic["id"], {"expected_version": 1, "card_id": "card", "card_version": 1}
    )
    old_id = topic["materials"][0]["id"]
    card["version"] = 2
    card["evidence"][0]["quote"] = "乙"
    topic = service.add_material(
        topic["id"], {"expected_version": topic["version"], "card_id": "card", "card_version": 2}
    )
    assert len(topic["materials"]) == 2
    assert service.material(topic["id"], old_id)["evidence"][0]["quote"] == "甲"
    assert not topic["materials"][0]["source_current"]
    assert topic["materials"][1]["source_current"]


def test_pins_do_not_expand_or_require_explicit_whole_book_scope(tmp_path):
    research, card = research_stub(tmp_path)
    service = StudyService(research)
    topic = service.create_topic({"title": "单篇选材"})
    topic = service.add_material(
        topic["id"], {"expected_version": 1, "card_id": "card", "card_version": 1}
    )
    assert topic["book_ids"] == []
    topic = service.update_topic(
        topic["id"], {"expected_version": topic["version"], "book_ids": [card["book_id"]]}
    )
    topic = service.update_topic(
        topic["id"], {"expected_version": topic["version"], "book_ids": []}
    )
    assert topic["book_ids"] == [] and len(topic["materials"]) == 1


def test_timeline_unions_explicit_books_with_pins_and_material_filter_narrows(tmp_path):
    import json

    from hrs.processing.research_workflow import ResearchWorkflow
    from hrs.research.network import NetworkQuery
    from tests.unit.research.test_network_timeline import gate, recorded_card

    research, _ = research_stub(tmp_path)
    workflow = ResearchWorkflow(research.bridge.store)
    whole_book = recorded_card(workflow, "whole-book", date="1935")
    pinned = recorded_card(workflow, "pinned", date="1936", event_dates=["1934", "1936", "1937"])
    pinned["version"] = 2
    pinned["evidence"].append({**pinned["evidence"][0], "id": "selected"})
    pinned["events"][1]["evidence_ids"] = ["selected"]
    pinned["events"][2]["date"]["evidence_ids"] = ["selected"]
    with workflow.ledger.connect(write=True) as connection:
        connection.execute(
            "INSERT INTO research_cards VALUES (?,?,?,?,?,?)",
            (pinned["id"], 2, pinned["run_id"], pinned["book_id"], "verified", json.dumps(pinned)),
        )
        workflow.catalogue.record(pinned, connection=connection)
    outside = recorded_card(workflow, "outside", date="1937")
    research.workspace._network_query = lambda: NetworkQuery(
        workflow.ledger, current_cards=gate(whole_book, pinned, outside)
    )
    service = StudyService(research)
    # The catalogue fixture uses synthetic book identities; the read scope still uses
    # real native card versions, publication hashes and SQL catalogue projections.
    topic = service.store.create(
        "topic",
        {
            "title": "组合范围",
            "book_ids": [whole_book["book_id"]],
            "materials": [
                {
                    "id": "pin",
                    "card_id": pinned["id"],
                    "card_version": pinned["version"],
                    "publication_sha256": pinned["publication_sha256"],
                    "book_id": pinned["book_id"],
                    "evidence_ids": ["selected"],
                }
            ],
        },
    )
    assert [item["card_id"] for item in service.timeline(topic["id"])["items"]] == [
        whole_book["id"],
        pinned["id"],
        pinned["id"],
    ]
    assert [
        item["card_id"] for item in service.timeline(topic["id"], material_id="pin")["items"]
    ] == [pinned["id"], pinned["id"]]
    page = service.timeline(topic["id"], offset=1, limit=1)
    assert page["total"] == 3 and page["items"][0]["name"] == "Event 001 pinned"
    documents = service.timeline(topic["id"], date_basis="formation")
    assert documents["total"] == 2
    topic = service.store.save(
        "topic",
        topic["id"],
        {"book_ids": [whole_book["book_id"], pinned["book_id"]]},
        expected_version=topic["version"],
    )
    assert service.timeline(topic["id"])["total"] == 4
    empty = service.create_topic({"title": "空专题"})
    assert service.timeline(empty["id"])["total"] == 0


def test_canvas_context_reads_references_beyond_drawer_page_without_full_text(tmp_path):
    research, _ = research_stub(tmp_path)
    service = StudyService(research)
    topic = service.create_topic({"title": "大专题"})
    first = service.create_record(
        topic["id"], {"kind": "argument", "title": "较早的论据", "text": "甲" * 3000}
    )
    for index in range(201):
        service.create_record(topic["id"], {"kind": "note", "title": f"札记{index}"})
    assert service.records(topic["id"], kind="argument", limit=20)["items"][0]["id"] == first["id"]
    canvas = service.create_canvas(
        topic["id"],
        {
            "title": "引用画布",
            "document": {
                "nodes": [
                    {
                        "id": "old",
                        "record_kind": "record",
                        "record_id": first["id"],
                        "x": 0,
                        "y": 0,
                    },
                ]
            },
        },
    )
    context = service.canvas_context(canvas["id"], expected_version=1)
    assert [record["id"] for record in context["records"]] == [first["id"]]
    assert len(context["records"][0]["text"]) == 600
    assert context["records"][0]["text_truncated"]
    assert len(service.record(first["id"])["text"]) == 3000
    with pytest.raises(ServiceError):
        service.canvas_context(canvas["id"], expected_version=2)


def test_topic_timeline_filters_and_sorts_before_pagination_across_record_pages(tmp_path):
    from hrs.research.network import NetworkQuery

    research, _ = research_stub(tmp_path)
    research.workspace._network_query = lambda: NetworkQuery(
        research.bridge.store.checkpoints.ledger, current_cards=[]
    )
    service = StudyService(research)
    topic = service.create_topic({"title": "定年专题"})
    for year in reversed(range(1800, 2001)):
        service.create_record(
            topic["id"],
            {
                "kind": "clue",
                "title": str(year),
                "date": {"text": f"{year}年", "normalized": str(year), "certainty": "stated"},
            },
        )
    service.create_record(topic["id"], {"kind": "clue", "title": "未定年"})
    result = service.timeline(
        topic["id"], date_from="1850", date_to="1870", include_undated=False, offset=5, limit=10
    )
    assert result["total"] == result["dated_total"] == 21 and result["undated_total"] == 0
    assert [item["name"] for item in result["items"]] == list(map(str, range(1855, 1865)))
    unknown = service.timeline(topic["id"], date_from="2100", include_undated=True)
    assert unknown["total"] == unknown["undated_total"] == 1
    assert unknown["items"][0]["name"] == "未定年"
    with pytest.raises(ServiceError) as foreign_material:
        service.timeline(topic["id"], material_id="outside-topic")
    assert foreign_material.value.status_code == 404
