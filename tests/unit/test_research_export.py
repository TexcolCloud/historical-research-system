"""Pure export rendering contracts; no inference, workflow writes or content approval."""

from copy import deepcopy
from types import SimpleNamespace

from hrs.library.footnotes import resolve_footnotes
from hrs.library.records import text_digest
from hrs.research.models import CardDraft
from hrs.research.projection import located_card
from hrs.research.service import Research


def test_complete_research_export_keeps_metadata_roles_plans_and_cross_card_versions():
    text, reply = "甲致函乙。", "乙复函甲。"
    draft = CardDraft.model_validate(
        {
            "id": "card",
            "article_id": "article",
            "title": "函件对照",
            "summary": "仅陈述两份函件记载",
            "metadata": {
                "title_origin": "system",
                "authors": {
                    "value": "甲",
                    "certainty": "stated",
                    "evidence_ids": ["e1"],
                    "note": "作者落款唯一",
                },
            },
            "evidence": [
                {"id": "e1", "article_id": "article", "start": 0, "end": len(text), "quote": text},
                {"id": "e2", "article_id": "reply", "start": 0, "end": len(reply), "quote": reply},
            ],
            "mentions": [
                {
                    "id": "person",
                    "name": "甲",
                    "kind": "person",
                    "identity_basis": "落款",
                    "evidence_ids": ["e1"],
                }
            ],
            "events": [
                {
                    "id": "event",
                    "name": "发函",
                    "description": "甲发送函件",
                    "action": "申请拨款唯一",
                    "place": {
                        "value": "上海",
                        "certainty": "inferred",
                        "evidence_ids": ["e1"],
                        "note": "地点依据唯一",
                    },
                    "participant_ids": ["person"],
                    "participant_roles": [
                        {
                            "mention_id": "person",
                            "role": "发函者唯一",
                            "description": "发函记载",
                            "evidence_ids": ["e1"],
                        }
                    ],
                    "evidence_ids": ["e1"],
                }
            ],
            "coverage": [
                {
                    "id": "unit",
                    "article_id": "article",
                    "start": 0,
                    "end": len(text),
                    "summary": "完整覆盖唯一",
                    "mention_ids": ["person"],
                    "event_ids": ["event"],
                    "relation_ids": ["cross"],
                }
            ],
            "research_plan": [
                {
                    "id": "plan",
                    "question": "是否存在复函唯一？",
                    "status": "answered",
                    "proposed_queries": ["往复函件"],
                    "search_receipt_ids": ["search-1"],
                    "evidence_ids": ["e1", "e2"],
                    "evaluation": "分别核对来往文书唯一",
                    "stop_reason": "两文均已完整审读唯一",
                }
            ],
            "cross_relations": [
                {
                    "id": "cross",
                    "target_card_id": "counterpart-card",
                    "target_card_version": 7,
                    "target_publication_sha256": "9" * 64,
                    "kind": "reply_to",
                    "reason": "明确函复关联唯一",
                    "source_evidence_ids": ["e1"],
                    "target_evidence_ids": ["e2"],
                }
            ],
            "question_findings": [
                {
                    "id": "finding",
                    "question_id": "question",
                    "definition_version": 3,
                    "text": "问题结论唯一",
                    "status": "partial",
                    "evidence_ids": ["e1", "e2"],
                    "unresolved": ["办结尚未有据唯一"],
                }
            ],
        }
    ).model_dump()
    articles = [
        {
            "id": aid,
            "text": body,
            "text_sha256": text_digest(body),
            "parts": [{"start": 0, "end": len(body), "text": body, "source": {"pages": [page]}}],
        }
        for aid, body, page in [("article", text, 1), ("reply", reply, 2)]
    ]
    snapshot = {
        "source_run_id": "source",
        "book_id": "book",
        "publication_sha256": "a" * 64,
        "articles": articles,
    }
    draft.update(
        book_id="book",
        run_id="run",
        source_run_id="source",
        version=1,
        state="verified",
        checks={"decision": "approve"},
        publication_sha256="a" * 64,
    )
    card = located_card(draft, snapshot)
    service = object.__new__(Research)
    service.card = lambda _: card
    service.workflow = SimpleNamespace(snapshot=lambda _: snapshot, sources=lambda _: [])
    exported = service.export("card")
    for expected in (
        "作者落款唯一",
        "申请拨款唯一",
        "地点依据唯一",
        "发函者唯一",
        "完整覆盖唯一",
        "是否存在复函唯一",
        "分别核对来往文书唯一",
        "两文均已完整审读唯一",
        "counterpart-card",
        "target_card_version",
        "7",
        "9" * 64,
        "明确函复关联唯一",
        "问题结论唯一",
        "办结尚未有据唯一",
    ):
        assert expected in exported, expected


def test_export_keeps_complete_card_meaning_and_frozen_publication_provenance():
    article_id, external_id = "primary-article", "external-frozen-article"
    text = "# 原文标题\n\n甲[^1]提出主张。\n\n[^1]: 编者原注，不能当作甲的发言。\n"
    parts = [
        {
            "start": 0,
            "end": len(text),
            "text": text,
            "source": {
                "pages": [7],
                "original_start": 31,
                "original_end": 31 + len(text),
                "image_sha256": "d" * 64,
            },
        }
    ]
    note = resolve_footnotes(text, parts)[0]
    start, end = text.index("甲"), text.index("\n", text.index("甲"))
    external_text = "乙提出相反意见。"
    draft = CardDraft.model_validate(
        {
            "id": "local-card",
            "article_id": article_id,
            "title": "完整导出卡片",
            "summary": "提要唯一：区分甲的原话与编者原注。",
            "document_type": "会议记录唯一",
            "source_layer": "原始记录与编者注分层唯一",
            "formation_date": {
                "text": "民国三十年一月",
                "normalized": "1941-01",
                "certainty": "stated",
                "evidence_ids": ["e1"],
            },
            "event_date": {
                "text": "此前一月",
                "normalized": "1940-12",
                "certainty": "inferred",
                "evidence_ids": ["e2"],
            },
            "items": [
                {
                    "id": "structure",
                    "kind": "section",
                    "title": "原文结构唯一",
                    "text": "结构正文唯一",
                    "evidence_ids": ["e1"],
                },
                {
                    "id": "fact",
                    "kind": "evidence",
                    "title": "史料要点唯一",
                    "text": "证据内容唯一",
                    "evidence_ids": ["e1"],
                },
                {
                    "id": "argument",
                    "kind": "argument",
                    "title": "研究论点唯一",
                    "text": "推论正文唯一",
                    "evidence_ids": ["e1", "e2"],
                    "epistemic_state": "inferred",
                    "attribution": "研究者归纳唯一",
                    "limitations": ["条目边界唯一"],
                },
                {
                    "id": "issue",
                    "kind": "issue",
                    "title": "研究问题唯一",
                    "text": "问题正文唯一",
                    "evidence_ids": ["e2"],
                },
                {
                    "id": "use",
                    "kind": "paper_use",
                    "title": "论文用途唯一",
                    "text": "仅可用于比较，不能证明整体趋势。",
                    "evidence_ids": ["e1"],
                },
            ],
            "evidence": [
                {
                    "id": "e1",
                    "article_id": article_id,
                    "start": start,
                    "end": end,
                    "quote": text[start:end],
                    "footnote_ids": [note["id"]],
                },
                {
                    "id": "e2",
                    "article_id": external_id,
                    "start": 0,
                    "end": len(external_text),
                    "quote": external_text,
                },
            ],
            "mentions": [
                {
                    "id": "person",
                    "kind": "person",
                    "name": "甲",
                    "identity_basis": "身份依据唯一",
                    "evidence_ids": ["e1"],
                },
                {
                    "id": "place",
                    "kind": "place",
                    "name": "地区乙",
                    "identity_basis": "地点依据唯一",
                    "evidence_ids": ["e2"],
                },
            ],
            "events": [
                {
                    "id": "event",
                    "name": "会议事件唯一",
                    "description": "事件经过唯一",
                    "date": {
                        "text": "一月三日",
                        "normalized": "1941-01-03",
                        "certainty": "disputed",
                        "evidence_ids": ["e1"],
                    },
                    "participant_ids": ["person"],
                    "evidence_ids": ["e1", "e2"],
                }
            ],
            "relations": [
                {
                    "id": "relation",
                    "subject_id": "person",
                    "predicate": "限制支持",
                    "object_id": "argument",
                    "role": "limit",
                    "reason": "关系依据唯一，反例不能省略。",
                    "evidence_ids": ["e2"],
                }
            ],
            "identity_links": [
                {
                    "local_mention_id": "person",
                    "entity_id": "other-global-entity",
                    "relation": "possible_same_as",
                    "reason": "仅同名线索，不足以合并身份。",
                    "evidence_ids": ["e1", "e2"],
                }
            ],
            "tags": ["标签甲", "标签乙"],
            "limitations": ["整体使用边界唯一：禁止推及未载地区。"],
            "alternatives": ["备选解释唯一：可能出自编者整理。"],
            "questions": ["待核问题唯一：原件是否有删节？"],
            "no_argument_reason": "原文没有直接因果论证，研究论点仅为归纳。",
        }
    ).model_dump()
    snapshot = {
        "book_id": "primary-book",
        "book_title": "原始文献书名",
        "source_run_id": "primary-source-run",
        "document_id": "native-document",
        "workspace_id": "frozen-workspace",
        "publication_sha256": "a" * 64,
        "chapters_sha256": "c" * 64,
        "source": {"sha256": "f" * 64},
        "articles": [
            {
                "id": article_id,
                "text": text,
                "text_sha256": text_digest(text),
                "parts": parts,
            }
        ],
    }
    external = {
        "id": external_id,
        "original_article_id": "external-reader-article",
        "source_run_id": "external-source-run",
        "text": external_text,
        "text_sha256": text_digest(external_text),
        "parts": [
            {
                "start": 0,
                "end": len(external_text),
                "text": external_text,
                "source": {"pages": [12], "image_sha256": "e" * 64},
            }
        ],
    }
    draft.update(
        id="published-card",
        local_id="local-card",
        book_id="primary-book",
        run_id="research-run",
        source_run_id="primary-source-run",
        article_title="原文标题",
        article_sha256=text_digest(text),
        publication_sha256="a" * 64,
        version=3,
        revision_versions=[1, 2, 3],
        created_at=1700000000,
        state="human_accepted",
        approval_type="user",
        source_current=False,
        source_status="stale",
        needs_update=True,
        superseded=True,
        checks={
            "decision": "pending",
            "input_hash": "b" * 64,
            "checked_card_ids": ["local-card"],
            "checked_item_ids": ["local-card/argument"],
            "checked_evidence_ids": ["local-card/e1", "local-card/e2"],
            "checked_relation_ids": ["local-card/relation"],
            "checked_identity_links": ["local-card/0"],
            "checked_research_disposition": True,
            "issues": ["独立复核遗留问题唯一"],
        },
        human_decision={"action": "edit", "previous_version": 2, "at": 1700000010},
    )
    draft["evidence"][0].update(
        footnotes=[note],
        pages=[7],
        book_id="primary-book",
        source_run_id="primary-source-run",
        publication_sha256="a" * 64,
    )
    draft["evidence"][1].update(
        footnotes=[],
        pages=[12],
        book_id="external-book",
        source_run_id="external-source-run",
        publication_sha256="9" * 64,
    )
    card = located_card(draft, snapshot, [external])
    retained = deepcopy((card, snapshot, external))
    service = object.__new__(Research)
    service.card = lambda _: card
    service.workflow = SimpleNamespace(snapshot=lambda _: snapshot, sources=lambda _: [external])

    exported = service.export(card["id"], base_url="http://localhost:8123")

    sections = [
        "卡片与来源版本",
        "史料属性与年代",
        "提要",
        "卡片内容",
        "人物、机构、地点与实体记载",
        "事件（events）",
        "关系网络",
        "跨卡身份关联",
        "使用边界",
        "备选解释",
        "待核问题",
        "核验与人工决定",
        "原文证据",
    ]
    positions = [exported.index("## " + section) for section in sections]
    assert positions == sorted(positions)
    for expected in (
        "卡片版本（version）**：3",
        "已保留修订（revision_versions）**：1、2、3",
        "primary-source-run",
        "research-run",
        "publication_sha256",
        "chapters_sha256",
        "a" * 64,
        "c" * 64,
        text_digest(text),
        "f" * 64,
        "会议记录唯一",
        "原始记录与编者注分层唯一",
        "民国三十年一月",
        "1941-01",
        "stated",
        "此前一月",
        "1940-12",
        "inferred",
        "结构正文唯一",
        "证据内容唯一",
        "推论正文唯一",
        "研究者归纳唯一",
        "条目边界唯一",
        "问题正文唯一",
        "仅可用于比较，不能证明整体趋势。",
        "身份依据唯一",
        "地点依据唯一",
        "事件经过唯一",
        "1941-01-03",
        "disputed",
        "甲（`person`）",
        "限制支持",
        "研究论点唯一（`argument`）",
        "limit",
        "关系依据唯一，反例不能省略。",
        "other-global-entity",
        "possible_same_as",
        "仅同名线索，不足以合并身份。",
        "标签甲、标签乙",
        "整体使用边界唯一：禁止推及未载地区。",
        "备选解释唯一：可能出自编者整理。",
        "待核问题唯一：原件是否有删节？",
        "原文没有直接因果论证，研究论点仅为归纳。",
        "独立复核遗留问题唯一",
        "local-card/relation",
        "local-card/0",
        "人工决定**：edit",
        "人工决定所据此前版本**：2",
        "历史卡片",
        "已有后续版本",
        "甲[^research-1-1]提出主张。",
        "[^research-1-1]: 编者原注，不能当作甲的发言。",
        note["id"],
        "[e1](#research-evidence-1)",
        '<a id="research-evidence-1"></a>',
        "乙提出相反意见。",
        "external-source-run",
        "9" * 64,
        "http://localhost:8123/#/books/external-book?",
        "chapter=external-reader-article",
        "physicalPage=12",
        "run=external-source-run",
    ):
        assert expected in exported, expected
    assert (card, snapshot, external) == retained
