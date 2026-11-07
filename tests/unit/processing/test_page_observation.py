from copy import deepcopy

import pytest

from hrs.processing.hashing import digest
from hrs.processing.navigation import observation
from tests.unit.processing.test_store import complete, document


def test_original_reading_cannot_be_mistaken_for_page_structure():
    page = {
        "image_sha256": "a" * 64,
        "approved": {"markdown": "目录\n甲……1\n9", "sha256": "b" * 64},
    }
    with pytest.raises(ValueError, match="kind"):
        observation(
            page, {"image_receipt": "receipt", "observations": ["目录页"], "unresolved": []}
        )


def test_correction_structure_survives_independent_verification(tmp_path):
    store, identity = document(tmp_path)
    original, corrected = "目录\n甲……1\n9错", "目录\n甲……1\n9"
    complete(store, identity, "ocr", "ocr", {"markdown": original})
    complete(
        store,
        identity,
        "review",
        "review",
        {
            "verdict": "corrections",
            "markdown": corrected,
            "corrections": [{"before": "9错", "after": "9", "reason": "原件页码为9"}],
            "issues": [],
            "page_observation": {
                "kind": "toc",
                "printed_label": "9",
                "headings": [],
                "sequence": "frontmatter",
            },
        },
    )
    before = store.status(identity)
    assert before["pages"][0].get("structure_proposal", {}).get("value", {}).get("kind") == "toc"
    complete(store, identity, "verify", "independent", {"verdict": "approved", "issues": []})
    page = store.status(identity)["pages"][0]
    assert page["observation"]["kind"] == "toc"
    assert page["observation"]["printed_label"] == "9"
    assert page["observation"]["markdown_sha256"] == digest(corrected)
    assert page["approved"]["markdown"] == corrected


def test_explicit_recovery_keeps_approval_and_rejects_unbound_or_ambiguous_fields():
    from hrs.processing.page_observation import restore_observations

    text = "标题\n标题\n已修改正文"
    page = {
        "page": 1,
        "image_sha256": "a" * 64,
        "approved": {"markdown": text, "sha256": digest(text), "approval_type": "human"},
        "observation": {"kind": "unknown"},
    }
    result = {"page_observation": {"kind": "toc", "printed_label": "9", "headings": ["标题"]}}
    doc = {
        "pages": [page],
        "tasks": [
            {
                "task_id": "review",
                "page": 1,
                "stage": "review",
                "inputs": {"image_sha256": "a" * 64},
                "result": result,
                "receipt": {"result_sha256": digest(result), "source_reading_sha256": "reading"},
                "source_reading": {"sha256": "reading"},
            }
        ],
    }
    before = deepcopy(doc)
    plan = restore_observations(doc)
    assert doc == before
    assert plan["restored_pages"] == [1]
    assert plan["changes"][0]["after"]["kind"] == "toc"
    assert plan["changes"][0]["after"]["headings"] == []
    assert plan["changes"][0]["after"]["printed_label"] is None
    restore_observations(doc, apply=True)
    assert doc["pages"][0]["approved"] == before["pages"][0]["approved"]
    assert doc["tasks"] == before["tasks"]


def test_store_repair_is_explicit_audited_idempotent_and_keeps_publication(tmp_path):
    from tests.unit.processing.test_original_reading import reading, submit

    store, identity = document(tmp_path)
    complete(store, identity, "ocr", "ocr", {"markdown": "# 标题\n正文\n1"})
    reviewer = store.claim_task(identity, "review", "independent", source_first=True)
    store.review_candidate(
        identity, reviewer["task_id"], reviewer["lease_token"], reading(store, identity, reviewer)
    )
    submit(
        store,
        identity,
        reviewer,
        {
            "verdict": "approved",
            "issues": [],
            "page_observation": {"kind": "text", "printed_label": "1", "headings": ["标题"]},
        },
    )
    complete(
        store,
        identity,
        "organization",
        "organizer",
        {"articles": [{"title": "标题", "pages": [1]}]},
    )
    store.publish(identity)
    with store.context.verified(identity) as doc:
        doc["pages"][0]["observation"]["kind"] = "unknown"
        doc["pages"][0]["observation"]["headings"] = []
        store.checkpoints.write(doc)
    before = store.status(identity)
    plan = store.repair_structure(identity)
    assert plan["restored_pages"] == [1]
    assert store.status(identity) == before
    applied = store.repair_structure(identity, apply=True)
    assert applied["restored_pages"] == [1]
    after = store.status(identity)
    assert after["pages"][0]["approved"] == before["pages"][0]["approved"]
    assert after["publication"] == before["publication"]
    assert after["tasks"] == before["tasks"]
    assert after["structure_repairs"][0]["changes"][0]["source_task_id"] == reviewer["task_id"]
    assert after["pages"][0]["observation"]["headings"] == ["标题"]
    assert store.repair_structure(identity, apply=True)["restored_pages"] == []
    assert len(store.status(identity)["structure_repairs"]) == 1


def test_structure_repair_waits_for_active_workers(tmp_path):
    store, identity = document(tmp_path)
    store.claim_task(identity, "ocr", "active")
    with pytest.raises(ValueError, match="active workers"):
        store.repair_structure(identity, apply=True)
