"""Performance policy must preserve independent original-first decisions."""

import pytest

from hrs.processing.execution_policy import configure_execution
from tests.unit.processing.test_store import document


def test_new_workspaces_enable_speed_policy_without_upgrading_existing_workspaces(tmp_path):
    from hrs.processing.ledger import Ledger
    from hrs.processing.runtime_control import effective_policy
    from hrs.processing.store import Store

    fresh = Store(tmp_path / "fresh")
    assert effective_policy(fresh.root)["overlap_reading"] is True
    assert effective_policy(fresh.root)["batch_pages"] == 8
    existing = tmp_path / "existing"
    existing.mkdir()
    Ledger(existing / "ledger.sqlite3")
    old = Store(existing)
    assert effective_policy(old.root) == {"capacity": 7, "batch_pages": 2, "overlap_reading": False}
    assert not (old.root / "execution-policy.json").exists()


def test_future_review_work_does_not_starve_initial_recognition_lanes():
    from hrs.processing.scheduling import scheduling_plan

    doc = {
        "pages": [{"page": p, "state": "awaiting_ocr"} for p in range(1, 25)],
        "tasks": [{"stage": "ocr", "state": "ready", "page": p} for p in range(1, 25)],
    }
    plan = scheduling_plan(doc, capacity=7, batch_pages=8, overlap_reading=True)
    assert plan["workers"]["ocr"] >= 3
    assert plan["workers"]["review"] >= 3


def test_cold_review_prior_does_not_retire_warm_ocr_workers_before_real_review_samples():
    from hrs.processing.scheduling import scheduling_plan

    doc = {
        "pages": [{"page": p, "state": "awaiting_ocr"} for p in range(1, 25)],
        "tasks": [{"stage": "ocr", "state": "ready", "page": p} for p in range(1, 22)]
        + [
            {
                "stage": "ocr",
                "state": "completed",
                "page": p,
                "receipt": {"duration_seconds": 20, "created_at": 900},
            }
            for p in range(22, 25)
        ],
    }
    plan = scheduling_plan(doc, now=1000, capacity=14, batch_pages=8, overlap_reading=True)
    assert plan["workers"]["ocr"] == 7 and plan["workers"]["review"] == 7


def test_single_page_reviewer_reads_and_freezes_before_ocr_finishes(tmp_path):
    store, identity = document(tmp_path)
    configure_execution(store.root, capacity=14, batch_pages=8, overlap_reading=True)
    assignments = store.dispatch.reserve(identity)["assignments"]
    tokens = {a["stage"]: a["session_token"] for a in assignments}
    assert {"ocr", "review", "organization"} <= tokens.keys()
    original, meta, pixels = store.dispatch.open(tokens["review"], "independent-review")
    assert original["state"] == "preflight" and pixels
    assert "markdown" not in str(original)
    reading = {
        "image_receipt": meta["image_receipt"],
        "image_sha256": meta["image_sha256"],
        "observations": ["Synthetic original read before OCR"],
        "unresolved": [],
    }
    assert store.dispatch.freeze(tokens["review"], "independent-review", reading)["state"] == "wait"
    ocr, _, _ = store.dispatch.open(tokens["ocr"], "ocr-worker")
    store.dispatch.submit(
        tokens["ocr"], "ocr-worker", {"input_sha256": ocr["input_sha256"], "markdown": "原文"}
    )
    assert store.claim_task(identity, "review", "unassigned-reader", scheduled=True) is None
    review, _, _ = store.dispatch.open(tokens["review"], "independent-review")
    assert review["source_reading"]["reading"]["observations"] == reading["observations"]
    with pytest.raises(ValueError, match="cannot change"):
        store.dispatch.freeze(
            tokens["review"], "independent-review", {**reading, "observations": ["changed"]}
        )
    store.candidate_text(identity, review["task_id"], review["lease_token"], limit=4000)
    store.dispatch.submit(
        tokens["review"],
        "independent-review",
        {"input_sha256": review["input_sha256"], "verdict": "approved", "issues": []},
    )
    store.verify_evidence(store.status(identity))


def test_capacity_is_shared_and_prewarming_cannot_oversubscribe(tmp_path):
    store, identity = document(tmp_path, 40)
    configure_execution(store.root, capacity=14, batch_pages=8, overlap_reading=True)
    first = store.dispatch.reserve(identity)
    assert len(first["assignments"]) == 14
    assert any(a["stage"] == "review" for a in first["assignments"])
    assert not store.dispatch.reserve(identity)["assignments"]
    assert store.checkpoints.ledger.capacity_used() == 14


def test_configured_program_ceiling_is_shared_across_concurrent_dispatchers(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from hrs.processing.store import Store

    store, identity = document(tmp_path, 80)
    configure_execution(store.root, capacity=64)
    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(lambda _: Store(store.root).dispatch.reserve(identity), range(4)))
    tokens = [a["session_token"] for result in results for a in result["assignments"]]
    assert len(tokens) == len(set(tokens)) == 64
    assert store.checkpoints.ledger.capacity_used() == 64
    assert not store.dispatch.reserve(identity)["assignments"]


def test_preflight_tampering_cannot_reach_a_review_decision(tmp_path):
    from hrs.processing.hashing import digest

    store, identity = document(tmp_path)
    configure_execution(store.root, capacity=14)
    tokens = {
        a["stage"]: a["session_token"] for a in store.dispatch.reserve(identity)["assignments"]
    }
    _, meta, _ = store.dispatch.open(tokens["review"], "independent")
    store.dispatch.freeze(
        tokens["review"],
        "independent",
        {
            "image_receipt": meta["image_receipt"],
            "image_sha256": meta["image_sha256"],
            "observations": ["synthetic"],
            "unresolved": [],
        },
    )
    task, _, _ = store.dispatch.open(tokens["ocr"], "ocr")
    store.dispatch.submit(
        tokens["ocr"], "ocr", {"input_sha256": task["input_sha256"], "markdown": "原文"}
    )
    task, _, _ = store.dispatch.open(tokens["review"], "independent")
    doc = store.checkpoints.read(identity)
    reading = next(t for t in doc["tasks"] if t["task_id"] == task["task_id"])["source_reading"]
    reading["reading"]["preflight"]["reading"]["observations"] = ["rewritten"]
    reading["sha256"] = digest({k: v for k, v in reading.items() if k != "sha256"})
    store.checkpoints.write(doc)
    with pytest.raises(ValueError, match="Preflight"):
        store.verify_evidence(store.status(identity))


def test_worker_reuses_warm_session_beyond_two_pages_with_bounded_context(tmp_path):
    store, identity = document(tmp_path, 4)
    configure_execution(store.root, capacity=2, batch_pages=8, overlap_reading=False)
    token = next(
        a["session_token"]
        for a in store.dispatch.reserve(identity)["assignments"]
        if a["stage"] == "ocr"
    )
    for _ in range(4):
        task, _, _ = store.dispatch.open(token, "warm-ocr")
        assert task["stage"] == "ocr"
        store.dispatch.submit(
            token, "warm-ocr", {"input_sha256": task["input_sha256"], "markdown": "正文"}
        )
    assert store.dispatch.open(token, "warm-ocr")[0]["state"] == "exited"


def test_restarted_session_budget_does_not_include_an_old_worker_name(tmp_path):
    from tests.unit.processing.test_store import complete

    store, identity = document(tmp_path, 4)
    configure_execution(store.root, capacity=2, batch_pages=2, overlap_reading=False)
    assignments = store.dispatch.reserve(identity)["assignments"]
    first = next(a["session_token"] for a in assignments if a["stage"] == "ocr")
    for assignment in assignments:
        if assignment["session_token"] != first:
            store.dispatch.close(assignment["session_token"], "unstarted")
    for _ in range(2):
        task, _, _ = store.dispatch.open(first, "reused-short-name")
        store.dispatch.submit(
            first,
            "reused-short-name",
            {"input_sha256": task["input_sha256"], "markdown": "Retained"},
        )
    for page in range(2):
        complete(
            store, identity, "review", f"independent-{page}", {"verdict": "approved", "issues": []}
        )
    previous = store.status(identity)["pages"][:2]
    second = store.dispatch.reserve(identity)["assignments"][0]["session_token"]
    assert second != first
    task, _, _ = store.dispatch.open(second, "reused-short-name")
    assert store.dispatch.session(second)["completed"] == 0
    store.dispatch.submit(
        second,
        "reused-short-name",
        {"input_sha256": task["input_sha256"], "markdown": "New context"},
    )
    next_task, _, _ = store.dispatch.open(second, "reused-short-name")
    assert next_task.get("stage") == "ocr", "An old native context must not exhaust a new session"
    assert store.dispatch.session(second)["completed"] == 1
    assert store.status(identity)["pages"][:2] == previous


def test_organization_batch_keeps_complete_text_and_frozen_page_hashes(tmp_path):
    from tests.unit.processing.test_store import complete

    store, identity = document(tmp_path, 3)
    text = "正文[^1]\n\n[^1]: 脚注归属。"
    for _ in range(3):
        complete(store, identity, "ocr", "ocr", {"markdown": text})
        complete(store, identity, "review", "review", {"verdict": "approved", "issues": []})
    task = store.claim_task(identity, "organization", "organizer")
    first = store.task_inspection.approved_pages_text(
        identity, task["task_id"], task["lease_token"], limit=2
    )
    assert [p["page"] for p in first["pages"]] == [1, 2]
    assert all(p["markdown"] == text for p in first["pages"])
    last = store.task_inspection.approved_pages_text(
        identity, task["task_id"], task["lease_token"], after_page=first["next_after_page"]
    )
    assert [p["page"] for p in last["pages"]] == [3] and last["next_after_page"] is None
    with pytest.raises(ValueError, match="lease"):
        store.task_inspection.approved_pages_text(identity, task["task_id"], "stale")


def test_last_organization_receipt_publishes_without_waiting_for_coordinator_cleanup(tmp_path):
    from tests.unit.processing.test_store import complete

    store, identity = document(tmp_path)
    complete(store, identity, "ocr", "transcriber", {"markdown": "正文"})
    complete(store, identity, "review", "reader", {"verdict": "approved", "issues": []})
    assignment = next(
        a for a in store.dispatch.reserve(identity)["assignments"] if a["stage"] == "organization"
    )
    task, _, _ = store.dispatch.open(assignment["session_token"], "organizer")
    result = {"input_sha256": task["input_sha256"], "articles": [{"title": "原文", "pages": [1]}]}
    receipt = store.dispatch.submit(assignment["session_token"], "organizer", result)
    assert receipt["state"] == "published" and receipt["publication"]["markdown_sha256"]
    assert store.status(identity)["state"] == "published"
    again = store.dispatch.submit(assignment["session_token"], "organizer", result)
    assert again["publication"] == receipt["publication"] and again["already_committed"]


def test_warm_reviewer_can_freeze_a_new_page_without_reusing_the_old_task(tmp_path):
    store, identity = document(tmp_path, 2)
    configure_execution(store.root, capacity=2)
    assignments = store.dispatch.reserve(identity)["assignments"]
    reviewer = next(a["session_token"] for a in assignments if a["stage"] == "review")
    ocr = next(a["session_token"] for a in assignments if a["stage"] == "ocr")
    for page in (1, 2):
        preview, meta, _ = store.dispatch.open(reviewer, "reader")
        assert preview["state"] == "preflight" and preview["page"] == page
        reading = {
            "image_receipt": meta["image_receipt"],
            "image_sha256": meta["image_sha256"],
            "observations": [f"Synthetic original page {page}"],
            "unresolved": [],
        }
        assert store.dispatch.freeze(reviewer, "reader", reading)["state"] == "wait"
        task, _, _ = store.dispatch.open(ocr, "transcriber")
        store.dispatch.submit(
            ocr, "transcriber", {"input_sha256": task["input_sha256"], "markdown": f"正文{page}"}
        )
        task, _, _ = store.dispatch.open(reviewer, "reader")
        store.candidate_text(identity, task["task_id"], task["lease_token"])
        store.dispatch.submit(
            reviewer,
            "reader",
            {"input_sha256": task["input_sha256"], "verdict": "approved", "issues": []},
        )
    store.verify_evidence(store.status(identity))


def test_rate_limit_reduces_shared_admission_without_interrupting_active_pages(tmp_path):
    store, identity = document(tmp_path, 24)
    configure_execution(store.root, capacity=14)
    assignments = store.dispatch.reserve(identity)["assignments"]
    token = next(a["session_token"] for a in assignments if a["stage"] == "ocr")
    task, _, _ = store.dispatch.open(token, "rate-limited")
    store.report_task_failure(identity, task["task_id"], task["lease_token"], "429")
    assert store.scheduling_plan(identity)["capacity"] == 7
    assert store.checkpoints.ledger.capacity_used() == 14
    assert not store.dispatch.reserve(identity)["assignments"]


def test_provider_capacity_failure_before_worker_start_releases_slot_and_reduces_admission(
    tmp_path,
):
    store, identity = document(tmp_path, 24)
    configure_execution(store.root, capacity=28)
    token = next(
        a["session_token"]
        for a in store.dispatch.reserve(identity)["assignments"]
        if a["stage"] == "review"
    )
    store.dispatch.close(token, "failed-before-open", failure_code="service_unavailable")
    plan = store.scheduling_plan(identity)
    assert plan["capacity"] == 14 and plan["cooldown_until"] > 0
    assert store.dispatch.session(token, allow_closed=True)["state"] == "exited"
    assert all(p["state"] == "awaiting_ocr" for p in store.status(identity)["pages"])


def test_read_ahead_claim_verifies_its_actual_reserved_page(tmp_path, monkeypatch):
    store, identity = document(tmp_path, 2)
    configure_execution(store.root, capacity=14)
    assignments = store.dispatch.reserve(identity)["assignments"]
    reviewers = [a["session_token"] for a in assignments if a["stage"] == "review"]
    for index, token in enumerate(reviewers):
        _, meta, _ = store.dispatch.open(token, f"reader-{index}")
        store.dispatch.freeze(
            token,
            f"reader-{index}",
            {
                "image_receipt": meta["image_receipt"],
                "image_sha256": meta["image_sha256"],
                "observations": ["Synthetic source observation"],
                "unresolved": [],
            },
        )
    for index, a in enumerate(a for a in assignments if a["stage"] == "ocr"):
        task, _, _ = store.dispatch.open(a["session_token"], f"ocr-{index}")
        store.dispatch.submit(
            a["session_token"],
            f"ocr-{index}",
            {"input_sha256": task["input_sha256"], "markdown": "正文"},
        )
    checked = []
    verify = store.context.evidence.verify

    def record(doc, pages=None):
        checked.append(pages)
        verify(doc, pages)

    monkeypatch.setattr(store.context.evidence, "verify", record)
    task, _, _ = store.dispatch.open(reviewers[1], "reader-1")
    assert task["page"] == 2
    assert checked and all(pages == [2] for pages in checked)


def test_capacity_sampling_uses_one_snapshot_during_warm_task_handoff(tmp_path, monkeypatch):
    from hrs.processing.store import Store

    store, identity = document(tmp_path, 3)
    configure_execution(store.root, capacity=2, overlap_reading=False)
    token = next(
        a["session_token"]
        for a in store.dispatch.reserve(identity)["assignments"]
        if a["stage"] == "ocr"
    )
    task, _, _ = store.dispatch.open(token, "worker")
    other = Store(store.root)
    ledger = store.checkpoints.ledger
    sessions = ledger.sessions
    changed = False

    def interleaved():
        nonlocal changed
        rows = sessions()
        if not changed:
            changed = True
            other.dispatch.submit(
                token, "worker", {"input_sha256": task["input_sha256"], "markdown": "正文"}
            )
            other.dispatch.open(token, "worker")
        return rows

    monkeypatch.setattr(ledger, "sessions", interleaved)
    assert ledger.capacity_used() == 2
