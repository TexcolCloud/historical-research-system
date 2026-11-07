from copy import deepcopy

import pytest

from hrs.processing.hashing import digest
from hrs.processing.navigation_verification import (
    apply_link_verifications,
    freeze_link_observations,
    prepare_link_verification,
    verify_link_readings,
)


def document():
    def page(number, text):
        return {
            "page": number,
            "image_sha256": str(number) * 64,
            "approved": {"markdown": text, "sha256": digest(text)},
        }

    pages = [page(1, "目录\n一封信 10"), page(2, "# 一封信\n一九四五年九月十日\n正文")]
    candidate = {
        "physical_page": 2,
        "anchor": "一封信",
        "start": 2,
        "markdown_sha256": pages[1]["approved"]["sha256"],
        "image_sha256": pages[1]["image_sha256"],
    }
    entry = {
        "id": "letter",
        "source_page": 1,
        "source_anchor": "一封信 10",
        "source_sha256": pages[0]["approved"]["sha256"],
        "source_image_sha256": pages[0]["image_sha256"],
        "title": "一封信",
        "target_label": "10",
        "status": "conflict",
        "candidates": [candidate],
    }
    return {
        "id": "doc",
        "source": {"sha256": "a" * 64},
        "pages": pages,
        "tasks": [],
        "navigation": {"entries": [entry], "source_entries": [entry]},
    }


def read_originals(doc, task, *, unresolved=False):
    task["image_accesses"] = {
        str(p["page"]): {"receipt": f"read-{p['page']}", "image_sha256": p["image_sha256"]}
        for p in task["inputs"]["pages"]
    }
    return freeze_link_observations(
        doc,
        task,
        [
            {
                "page": p["page"],
                "image_receipt": f"read-{p['page']}",
                "image_sha256": p["image_sha256"],
                "observations": ["已先看原图的标题和日期"],
                "unresolved": ["字迹不清"] if unresolved else [],
            }
            for p in task["inputs"]["pages"]
        ],
    )


def test_ambiguous_links_queue_one_bounded_native_navigation_pass():
    doc = document()
    assert prepare_link_verification(doc)
    task = doc["tasks"][0]
    task["inputs"] = deepcopy(task["verification_inputs"])
    assert task["stage"] == "navigation"
    assert task["inputs"]["navigation_mode"] == "verify_links"
    assert [p["page"] for p in task["inputs"]["pages"]] == [1, 2]
    assert not prepare_link_verification(doc)
    task["state"] = "completed"
    assert not prepare_link_verification(doc)


def test_unstarted_partial_batch_is_rebuilt_after_a_human_resolves_one_entry():
    doc = document()
    second = deepcopy(doc["navigation"]["entries"][0])
    second["id"] = "second-letter"
    doc["navigation"]["entries"].append(second)
    assert prepare_link_verification(doc)
    first = doc["tasks"][0]
    assert len(first["verification_inputs"]["entries"]) == 2
    doc["navigation"]["entries"][0]["status"] = "matched_manual"
    assert prepare_link_verification(doc)
    assert first["state"] == "superseded"
    assert [e["id"] for e in doc["tasks"][-1]["verification_inputs"]["entries"]] == [
        "second-letter"
    ]


def test_ready_verification_becomes_unnecessary_when_all_links_are_resolved():
    doc = document()
    prepare_link_verification(doc)
    doc["navigation"]["entries"][0]["status"] = "unlinked_manual"
    assert not prepare_link_verification(doc)
    assert doc["tasks"][0]["state"] == "superseded"
    assert len(doc["tasks"]) == 1


def test_human_resolution_never_interrupts_a_leased_original_reading():
    doc = document()
    prepare_link_verification(doc)
    task = doc["tasks"][0]
    task["state"] = "leased"
    task["claim"] = {"worker_id": "independent-sol"}
    doc["navigation"]["entries"][0]["status"] = "matched_manual"
    assert not prepare_link_verification(doc)
    assert task["state"] == "leased"


def test_rebuilding_an_unstarted_snapshot_does_not_consume_the_attempt_budget():
    doc = document()
    prepare_link_verification(doc)
    old = doc["tasks"][0]
    original_key = old["verification_inputs"]["entries"][0]["verification_key"]
    doc["navigation"]["entries"][0]["candidates"][0]["anchor"] = "正文"
    assert prepare_link_verification(doc)
    assert old["state"] == "superseded"
    new = doc["tasks"][-1]
    assert new["verification_inputs"]["entries"][0]["verification_key"] != original_key


def test_link_verification_needs_frozen_original_observations():
    doc = document()
    prepare_link_verification(doc)
    task = doc["tasks"][0]
    task["inputs"] = deepcopy(task["verification_inputs"])
    result = {"links": [{"entry_id": "letter", "target_page": 2, "target_anchor": "一封信"}]}
    with pytest.raises(ValueError, match="original"):
        apply_link_verifications(doc, task, result, {})
    frozen = read_originals(doc, task)
    assert frozen["entries"][0]["id"] == "letter"
    receipt = {"approval_type": "machine", "human_approval": False, "worker_id": "independent-sol"}
    apply_link_verifications(doc, task, result, receipt)
    saved = doc["navigation"]["link_verifications"]["letter"]
    assert saved["target"]["physical_page"] == 2
    assert saved["receipt"]["human_approval"] is False
    assert "decisions" not in doc["navigation"]


def test_unreadable_or_uninspected_targets_never_become_verified_links():
    doc = document()
    prepare_link_verification(doc)
    task = doc["tasks"][0]
    task["inputs"] = deepcopy(task["verification_inputs"])
    read_originals(doc, task, unresolved=True)
    with pytest.raises(ValueError, match="unresolved"):
        apply_link_verifications(
            doc,
            task,
            {"links": [{"entry_id": "letter", "target_page": 2, "target_anchor": "一封信"}]},
            {},
        )
    apply_link_verifications(
        doc, task, {"links": [{"entry_id": "letter", "target_page": None}]}, {}
    )
    assert doc["navigation"]["link_verifications"]["letter"]["target"] is None


def test_changed_source_and_outside_candidates_are_rejected():
    doc = document()
    prepare_link_verification(doc)
    task = doc["tasks"][0]
    task["inputs"] = deepcopy(task["verification_inputs"])
    read_originals(doc, task)
    changed = deepcopy(doc)
    changed["pages"][1]["approved"]["sha256"] = "b" * 64
    result = {"links": [{"entry_id": "letter", "target_page": 2, "target_anchor": "一封信"}]}
    with pytest.raises(ValueError, match="changed"):
        apply_link_verifications(changed, task, result, {})
    with pytest.raises(ValueError, match="candidate"):
        apply_link_verifications(
            doc,
            task,
            {"links": [{"entry_id": "letter", "target_page": 1, "target_anchor": "目录"}]},
            {},
        )


def test_retired_original_readings_remain_part_of_the_evidence_gate():
    doc = document()
    prepare_link_verification(doc)
    task = doc["tasks"][0]
    task["inputs"] = deepcopy(task["verification_inputs"])
    read_originals(doc, task)
    task["navigation_reading_history"] = [
        {
            "reading": task.pop("navigation_original_reading"),
            "image_accesses": task.pop("image_accesses"),
        }
    ]
    verify_link_readings(task)
    task["navigation_reading_history"][0]["reading"]["readings"][0]["observations"] = ["changed"]
    with pytest.raises(ValueError, match="history hash"):
        verify_link_readings(task)
