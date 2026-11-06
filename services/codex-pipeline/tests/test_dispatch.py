"""Capacity includes native agents which are still starting, across every book."""

from concurrent.futures import ThreadPoolExecutor

from test_store import document

from hrs_codex.store import Store


def test_concurrent_dispatch_reserves_capacity_before_native_spawn(tmp_path):
    store, identity = document(tmp_path, count=10)
    with ThreadPoolExecutor(4) as pool:
        plans = list(pool.map(lambda _: Store(store.root).dispatch.reserve(identity), range(4)))
    assignments = [assignment for plan in plans for assignment in plan["assignments"]]
    assert len(assignments) == 7
    assert len({assignment["session_token"] for assignment in assignments}) == 7
    assert store.dispatch.reserve(identity)["assignments"] == []
    assert all(session["state"] == "starting" for session in store.dispatch.sessions())
    assert store.status(identity)["counts"]["leased"] == sum(
        assignment["stage"] == "ocr" for assignment in assignments
    )


def test_session_binds_native_identity_and_reuses_at_most_two_pages(tmp_path):
    import pytest

    store, identity = document(tmp_path, count=3)
    assignment = next(
        assignment
        for assignment in store.dispatch.reserve(identity)["assignments"]
        if assignment["stage"] == "ocr"
    )
    token = assignment["session_token"]
    packet, metadata, pixels = store.dispatch.open(token, "native-worker")
    assert pixels and metadata["image_receipt"]
    assert packet["claim"]["worker_id"] == "native-worker"
    with pytest.raises(ValueError, match="identity"):
        store.dispatch.open(token, "different-worker")
    store.dispatch.submit(
        token,
        "native-worker",
        {"input_sha256": packet["claim"]["input_sha256"], "markdown": "原文"},
    )
    session = next(
        session for session in store.dispatch.sessions() if session["session_token"] == token
    )
    assert session["state"] == "idle" and session["completed"] == 1
    assert session["startup_seconds"] >= 0


def test_review_worker_can_prewarm_while_ocr_is_still_running(tmp_path):
    store, identity = document(tmp_path, count=10)
    assignments = store.dispatch.reserve(identity)["assignments"]
    review = next(assignment for assignment in assignments if assignment["stage"] == "review")
    packet, _, _ = store.dispatch.open(review["session_token"], "prewarmed-reviewer")
    assert packet["state"] == "wait"
    ocr = next(assignment for assignment in assignments if assignment["stage"] == "ocr")
    task, _, _ = store.dispatch.open(ocr["session_token"], "ocr-worker")
    store.dispatch.submit(
        ocr["session_token"],
        "ocr-worker",
        {"input_sha256": task["input_sha256"], "markdown": "正文"},
    )
    reviewed, metadata, pixels = store.dispatch.open(review["session_token"], "prewarmed-reviewer")
    assert reviewed["stage"] == "review" and pixels and metadata["image_receipt"]


def test_technical_failure_releases_the_task_into_a_bounded_session_cooldown(tmp_path):
    store, identity = document(tmp_path)
    assignment = store.dispatch.reserve(identity)["assignments"][0]
    task, _, _ = store.dispatch.open(assignment["session_token"], "failing-worker")
    store.report_task_failure(identity, task["task_id"], task["lease_token"], "429")
    session = next(
        session
        for session in store.dispatch.sessions()
        if session["session_token"] == assignment["session_token"]
    )
    assert session["state"] == "cooldown"
    assert store.status(identity)["tasks"][0]["state"] == "ready"
    assert store.dispatch.reserve(identity)["assignments"] == []


def test_replaying_a_committed_packet_returns_its_receipt_without_new_events(tmp_path):
    store, identity = document(tmp_path)
    assignment = store.dispatch.reserve(identity)["assignments"][0]
    token = assignment["session_token"]
    task, _, _ = store.dispatch.open(token, "retrying-worker")
    result = {"input_sha256": task["input_sha256"], "markdown": "正文"}
    first = store.dispatch.submit(token, "retrying-worker", result)
    sequence = store.checkpoints.ledger.sequence()
    again = store.dispatch.submit(token, "retrying-worker", result)
    assert again["task_id"] == first["task_id"]
    assert again["already_committed"] is True
    assert store.checkpoints.ledger.sequence() == sequence


def test_closing_a_worker_which_never_started_requeues_its_reserved_task(tmp_path):
    store, identity = document(tmp_path)
    assignment = store.dispatch.reserve(identity)["assignments"][0]
    store.dispatch.close(assignment["session_token"], "never-started")
    assert store.status(identity)["tasks"][0]["state"] == "ready"
    assert store.checkpoints.ledger.capacity_used() == 0
    assert store.dispatch.close(assignment["session_token"], "never-started")["state"] == "exited"


def test_dispatch_identifies_retired_native_threads_before_replacement_spawn(tmp_path):
    from hrs_codex.execution_policy import configure_execution

    store, identity = document(tmp_path, count=10)
    configure_execution(store.root, capacity=2, overlap_reading=False)
    assignments = store.dispatch.reserve(identity)["assignments"]
    native_threads = {a["session_token"] for a in assignments}
    assert len(native_threads) == 2
    first, second = sorted(native_threads)
    # MCP sessions can end before the coordinator receives both native completions.
    for token in (first, second):
        store.dispatch.close(token, "finished-before-parent-wait")
    native_threads.remove(first)
    result = store.dispatch.reserve(identity, active_session_tokens=list(native_threads))
    assert result["retired_sessions"] == [second]
    native_threads.difference_update(result["retired_sessions"])
    native_threads.update(a["session_token"] for a in result["assignments"])
    assert len(native_threads) <= 2


def test_submission_timing_is_reported_without_rewriting_the_machine_receipt(tmp_path):
    store, identity = document(tmp_path)
    token = store.dispatch.reserve(identity)["assignments"][0]["session_token"]
    task, _, _ = store.dispatch.open(token, "timed-worker")
    store.dispatch.submit(
        token, "timed-worker", {"input_sha256": task["input_sha256"], "markdown": "正文"}
    )
    metric = store.scheduling_plan(identity)["metrics"]["ocr"]
    assert metric["phase_medians"]["submission_seconds"] >= 0
    store.verify_evidence(store.status(identity))


def second_document(store, tmp_path, count=10):
    source = tmp_path / "second.pdf"
    source.write_bytes(b"different retained source")
    original = store.list_documents()[0]
    pages = [
        {**page, "image": str(tmp_path / f"page-{page['page']}.png")}
        for page in store.status(original["id"])["pages"]
    ]
    return store.register(source, pages[:count], title="Second source")["id"]


def test_concrete_work_uses_last_shared_slot_before_another_books_prewarm(tmp_path):
    from hrs_codex.execution_policy import configure_execution

    store, first = document(tmp_path, count=10)
    configure_execution(store.root, capacity=7, overlap_reading=True)
    assignments = store.dispatch.reserve(first)["assignments"]
    ocr = next(a for a in assignments if a["stage"] == "ocr")
    store.dispatch.close(ocr["session_token"], "unstarted")
    second = second_document(store, tmp_path)
    result = store.dispatch.reserve(second)
    assert [a["stage"] for a in result["assignments"]] == ["ocr"]
    assert store.checkpoints.ledger.capacity_used() == 7


def test_idle_prewarm_from_another_book_yields_capacity_to_ready_work(tmp_path):
    from hrs_codex.execution_policy import configure_execution

    store, first = document(tmp_path, count=10)
    configure_execution(store.root, capacity=14, overlap_reading=True)
    assignments = store.dispatch.reserve(first)["assignments"]
    for assignment in assignments:
        token = assignment["session_token"]
        if assignment["stage"] == "ocr":
            store.dispatch.close(token, "unstarted")
        else:
            packet, meta, _ = store.dispatch.open(token, "reader-" + token)
            if packet["state"] == "preflight":
                store.dispatch.freeze(
                    token,
                    "reader-" + token,
                    {
                        "image_receipt": meta["image_receipt"],
                        "image_sha256": meta["image_sha256"],
                        "observations": ["independent source-only observation"],
                        "unresolved": [],
                    },
                )
    second = second_document(store, tmp_path)
    configure_execution(store.root, capacity=2, overlap_reading=True)
    result = store.dispatch.reserve(second)
    assert any(a["stage"] == "ocr" for a in result["assignments"])
    assert store.checkpoints.ledger.capacity_used() <= 2
    ended = [
        s for s in store.dispatch.sessions() if s.get("exit_reason") == "capacity_for_ready_work"
    ]
    assert ended and all(s.get("preflight", {}).get("reading") for s in ended)


def test_reclaim_does_not_interrupt_an_unfrozen_original_read_or_reserved_task(tmp_path):
    from hrs_codex.execution_policy import configure_execution

    store, first = document(tmp_path, count=10)
    configure_execution(store.root, capacity=2, overlap_reading=True)
    assignments = store.dispatch.reserve(first)["assignments"]
    reviewer = next(a["session_token"] for a in assignments if a["stage"] == "review")
    assert store.dispatch.open(reviewer, "reading-original")[0]["state"] == "preflight"
    second = second_document(store, tmp_path)
    assert not store.dispatch.reserve(second)["assignments"]
    assert store.dispatch.open(reviewer, "reading-original")[0]["state"] == "wait"
    assert store.checkpoints.ledger.capacity_used() == 2


def test_waiting_organizer_exits_when_human_review_blocks_its_future_work(tmp_path):
    from test_store import complete

    from hrs_codex.execution_policy import configure_execution

    store, identity = document(tmp_path, count=2)
    configure_execution(store.root, capacity=14, overlap_reading=True)
    assignments = store.dispatch.reserve(identity)["assignments"]
    organizer = next(a["session_token"] for a in assignments if a["stage"] == "organization")
    assert store.dispatch.open(organizer, "waiting-organizer")[0]["state"] == "wait"
    for assignment in assignments:
        if assignment["stage"] != "organization":
            store.dispatch.close(assignment["session_token"], "unstarted")
    complete(store, identity, "ocr", "ocr", {"markdown": "Synthetic unreadable content"})
    complete(
        store,
        identity,
        "review",
        "review",
        {
            "verdict": "pending",
            "issues": [{"location": "body", "issue": "Synthetic human handoff"}],
        },
    )
    before = store.checkpoints.revision(identity)
    packet, _, _ = store.dispatch.open(organizer, "waiting-organizer")
    assert packet["state"] == "exited"
    assert store.checkpoints.ledger.capacity_used() == 0
    assert store.checkpoints.revision(identity) == before
    doc = store.status(identity)
    assert doc["pages"][0]["state"] == "pending"
    assert any(
        t["stage"] == "ocr" and t["page"] == 2 and t["state"] == "ready" for t in doc["tasks"]
    )
    assert store.dispatch.reserve(identity, active_session_tokens=[organizer])[
        "retired_sessions"
    ] == [organizer]
