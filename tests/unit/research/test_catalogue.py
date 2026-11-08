"""Business identity/relationship contracts over retained synthetic source records."""

from copy import deepcopy

import pytest

from hrs.processing.errors import EvidenceMismatch, LeaseConflict
from hrs.processing.hashing import digest
from hrs.processing.store import Store
from hrs.research.catalogue import ResearchCatalogue


def card(identity="card-a", book="book-a", name="张三"):
    text = f"{name}致函李四申请经费。"
    return {
        "id": identity,
        "version": 1,
        "state": "verified",
        "approval_type": "native",
        "book_id": book,
        "document_id": book,
        "article_id": "article-a",
        "run_id": "run-" + book,
        "publication_sha256": digest(text),
        "article_sha256": digest(text),
        "created_at": 1,
        "title": "申请函",
        "summary": "原文记载申请经费，未说明拨付。",
        "evidence": [
            {
                "id": "e1",
                "article_id": "article-a",
                "start": 0,
                "end": len(text),
                "quote": text,
                "pages": [1],
                "article_sha256": digest(text),
                "publication_sha256": digest(text),
                "book_id": book,
            }
        ],
        "mentions": [
            {
                "id": "sender",
                "kind": "person",
                "name": name,
                "identity_basis": "原文发函者",
                "evidence_ids": ["e1"],
            }
        ],
        "events": [],
        "relations": [],
        "identity_links": [],
        "items": [],
    }


def test_canonical_identity_survives_card_revisions_without_name_only_merge(tmp_path):
    store = Store(tmp_path / "native")
    catalogue = ResearchCatalogue(store.checkpoints.ledger)
    first = card()
    catalogue.record(first)
    entity = catalogue.entities()["items"][0]
    revised = deepcopy(first)
    revised.update(id="revised-card", version=1, created_at=2)
    revised["mentions"][0]["id"] = "renamed-local-id"
    catalogue.record(revised)
    assert catalogue.entities()["items"][0]["id"] == entity["id"]
    assert catalogue.entity(entity["id"])["source_mentions"][0]["card_id"] == "revised-card"
    catalogue.record(card("other-card", "book-b"))
    assert catalogue.entities()["total"] == 2


def test_identity_decisions_require_both_sources_and_preserve_merge_split_alias_history(tmp_path):
    store = Store(tmp_path / "native")
    catalogue = ResearchCatalogue(store.checkpoints.ledger)
    catalogue.record(card())
    catalogue.record(card("second", "book-b"))
    first, second = catalogue.entities()["items"]
    refs = [
        *first["source_mentions"][0]["evidence_refs"],
        *second["source_mentions"][0]["evidence_refs"],
    ]
    with pytest.raises(EvidenceMismatch, match="both identities"):
        catalogue.decision(
            first["id"],
            "merge",
            first["version"],
            target_id=second["id"],
            target_version=second["version"],
            evidence_refs=refs[:1],
            reason="不够的单边证据",
        )
    merged = catalogue.decision(
        first["id"],
        "merge",
        first["version"],
        target_id=second["id"],
        target_version=second["version"],
        evidence_refs=refs,
        reason="合成的人类身份裁决",
    )
    assert len(merged["source_mentions"]) == 2
    assert catalogue.entity(second["id"])["id"] == first["id"]
    with pytest.raises(LeaseConflict):
        catalogue.decision(
            first["id"],
            "alias",
            first["version"],
            alias="别号",
            evidence_refs=refs[:1],
            reason="迟到写入",
        )
    aliased = catalogue.decision(
        first["id"],
        "alias",
        merged["version"],
        alias="别号",
        evidence_refs=refs[:1],
        reason="合成的人工别名维护",
    )
    removed = catalogue.decision(
        first["id"],
        "alias_remove",
        aliased["version"],
        alias="别号",
        evidence_refs=refs[:1],
        reason="撤销错误别名，保留历史",
    )
    assert not any(alias["name"] == "别号" for alias in removed["aliases"])
    assert any(alias["name"] == "别号" for alias in removed["withdrawn_aliases"])
    split = catalogue.decision(
        first["id"],
        "split",
        removed["version"],
        mention_ids=[removed["source_mentions"][0]["id"]],
        evidence_refs=refs,
        reason="撤销合并，将一种来源提及拆为独立身份",
    )
    assert len(split["source_mentions"]) == 1 and catalogue.entities()["total"] == 2


def test_reading_locations_are_exact_hash_bound_and_do_not_follow_name_regex(tmp_path):
    store = Store(tmp_path / "native")
    catalogue = ResearchCatalogue(store.checkpoints.ledger)
    source = card()
    source["mentions"][0]["locations"] = [
        {"article_id": "article-a", "start": 0, "end": 2, "quote": "张三"}
    ]
    catalogue.record(source)
    reading = catalogue.reading("article-a", source["article_sha256"])
    assert [(item["start"], item["end"], item["quote"]) for item in reading["annotations"]] == [
        (0, 2, "张三")
    ]
    assert reading["cards"][0]["id"] == source["id"]
    assert catalogue.reading("article-a", "f" * 64)["annotations"] == []
