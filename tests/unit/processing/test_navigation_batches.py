import time
from copy import deepcopy

import pytest

from hrs.processing.hashing import digest
from hrs.processing.navigation_coverage import coverage_report
from hrs.processing.page_observation import observation
from hrs.processing.task_inputs import task_inputs
from hrs.processing.task_results import apply_navigation
from tests.unit.processing.test_navigation_coverage import page, proposal


def document():
    return {
        "id": "batch",
        "source": {"sha256": "a" * 64},
        "tasks": [],
        "pages": [
            page(1, "说明", "frontmatter"),
            *[page(number, f"标题{number}……{number}") for number in range(2, 9)],
            page(9, "正文", "text"),
        ],
        "navigation": {},
    }


def claim(doc):
    inputs = task_inputs(None, doc, {"stage": "navigation", "page": None})
    return {
        "task_id": "parse",
        "inputs": inputs,
        "image_accesses": {str(p["page"]): {} for p in inputs["pages"]},
    }


def test_parse_freezes_at_most_three_core_pages_and_adjacent_context():
    doc = document()
    task = claim(doc)
    assert task["inputs"]["coverage_contract_version"] == 2
    assert task["inputs"]["toc_candidate_pages"] == [2, 3, 4]
    assert task["inputs"]["toc_context_pages"] == [1, 5]
    assert [p["page"] for p in task["inputs"]["pages"]] == [1, 2, 3, 4, 5]
    apply_navigation(
        doc, task, {"entries": [proposal(n, f"标题{n}", str(n)) for n in (2, 3, 4)]}, {}
    )
    assert coverage_report(doc)["missing_pages"] == [5, 6, 7, 8]
    assert claim(doc)["inputs"]["toc_candidate_pages"] == [5, 6, 7]


def test_cross_boundary_entry_does_not_claim_the_entire_context_page_complete():
    doc = document()
    doc["pages"][3] = page(4, "前半标题\n4")
    doc["pages"][3]["observation"] = observation(
        doc["pages"][3], {"kind": "toc", "printed_label": "4"}
    )
    doc["pages"][4] = page(5, "后半标题……5\n下一条……6")
    task = claim(doc)
    entries = [proposal(n, f"标题{n}", str(n)) for n in (2, 3)] + [
        {
            "title": "前半标题后半标题",
            "target_label": "5",
            "source_spans": [
                {"page": 4, "anchor": "前半标题"},
                {"page": 5, "anchor": "后半标题……5"},
            ],
        }
    ]
    apply_navigation(doc, task, {"entries": entries}, {})
    assert 5 in coverage_report(doc)["missing_pages"]
    assert "5" not in doc["navigation"]["coverage_pages"]


def test_unresolved_retries_only_after_neighbor_evidence_changes():
    doc = document()
    pending = deepcopy(doc["pages"][4]["approved"])
    doc["pages"][4].pop("approved")
    task = claim(doc)
    result = {
        "entries": [proposal(n, f"标题{n}", str(n)) for n in (2, 3)],
        "page_coverage": [
            {"page": 4, "status": "unresolved", "reason": "跨页后一页尚未通过核验"},
        ],
    }
    apply_navigation(doc, task, result, {})
    assert coverage_report(doc)["unresolved_pages"] == [4]
    assert 4 not in claim(doc)["inputs"]["toc_candidate_pages"]
    doc["pages"][4]["approved"] = pending
    assert 4 in coverage_report(doc)["missing_pages"]
    assert claim(doc)["inputs"]["toc_candidate_pages"] == [4, 5, 6]


def test_retained_frozen_navigation_inputs_are_not_replanned():
    doc = document()
    frozen = {"stage": "navigation", "pages": [{"page": 99}], "toc_sha256": digest("old")}
    assert task_inputs(None, doc, {"stage": "navigation", "page": None, "inputs": frozen}) is frozen


@pytest.mark.parametrize("paused", [False, True])
def test_explicit_replan_waits_for_expiry_preserves_receipts_and_provider_pressure(
    tmp_path, paused
):
    from tests.unit.processing.test_store import complete
    from tests.unit.processing.test_store import document as stored_document

    store, identity = stored_document(tmp_path)
    complete(store, identity, "ocr", "ocr", {"markdown": "目录\n甲……1"})
    complete(
        store,
        identity,
        "review",
        "review",
        {"verdict": "approved", "issues": [], "page_observation": {"kind": "toc"}},
    )
    with store.context.verified(identity) as doc:
        old = next(t for t in doc["tasks"] if t["stage"] == "navigation")
        old.update(
            parse_contract_version=1,
            state="leased",
            inputs={"stage": "navigation", "coverage_contract_version": 1, "pages": []},
        )
        old["claim"] = {
            "input_sha256": digest(old["inputs"]),
            "lease_token": "legacy",
            "expires_at": time.time() + 60,
        }
        doc["run_request"] = {
            "state": "paused" if paused else "retryable_error",
            "technical_failures": 1,
            "retry_limit": 1,
            "history": [],
            **({"pause_requested": True} if paused else {}),
        }
        store.checkpoints.write(doc)
    with pytest.raises(ValueError, match="active workers"):
        store.repair_structure(identity, apply=True, replan_navigation=True)
    with store.context.verified(identity) as doc:
        old = next(t for t in doc["tasks"] if t["stage"] == "navigation")
        old["claim"]["expires_at"] = time.time() - 1
        store.checkpoints.write(doc)
    store.runtime_control.failure("rate_limit", "retained-pressure", model="gpt-6.1-sol")
    with store.checkpoints.ledger.connect() as connection:
        pressure = list(
            connection.execute("SELECT scope,payload FROM runtime_control ORDER BY scope")
        )
    before = store.status(identity)
    result = store.repair_structure(identity, apply=True, replan_navigation=True)
    after = store.status(identity)
    previous = next(t for t in after["tasks"] if t["task_id"] == old["task_id"])
    assert previous["state"] == "superseded"
    assert previous["inputs"] == old["inputs"] and previous["claim"] == old["claim"]
    assert result["superseded_tasks"][0]["task_id"] == old["task_id"]
    assert after["pages"][0]["approved"] == before["pages"][0]["approved"]
    assert after["run_request"]["state"] == ("paused" if paused else "pending")
    assert after["run_request"]["technical_failures"] == (1 if paused else 0)
    assert any(
        t["stage"] == "navigation" and t["state"] == "ready" and t["parse_contract_version"] == 2
        for t in after["tasks"]
    )
    with store.checkpoints.ledger.connect() as connection:
        assert (
            list(connection.execute("SELECT scope,payload FROM runtime_control ORDER BY scope"))
            == pressure
        )
