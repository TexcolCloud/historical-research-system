"""The local queue must never publish unreviewed or stale evidence."""

import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from hrs_codex.store import Store, digest


def document(tmp_path, count=1):
    source = tmp_path / "source.pdf"
    source.write_bytes(b"retained-source")
    pages = []
    for number in range(1, count + 1):
        image = tmp_path / f"page-{number}.png"
        image.write_bytes(f"original-page-{number}".encode())
        pages.append(
            {
                "page": number,
                "image": str(image),
                "image_sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
            }
        )
    store = Store(tmp_path / "data")
    doc = store.register(source, pages, title="Historical evidence")
    return store, doc["id"]


def routed_document(tmp_path, route):
    source = tmp_path / "routed.pdf"
    source.write_bytes(b"source-with-routing")
    image = tmp_path / "routed.png"
    image.write_bytes(b"source-image")
    native = "原生文本。" if route in {"native", "mixed"} else ""
    plan = {
        "policy": "codex-pdf-routing-v1",
        "route": route,
        "native_markdown": native,
        "native_sha256": digest(native),
        "regions": [{"region_id": "figure-1", "bbox": [0.1, 0.2, 0.5, 0.8], "kind": "image"}]
        if route == "mixed"
        else [],
        "reason_codes": ["test"],
    }
    store = Store(tmp_path / "routed-data")
    doc = store.register_manifest(
        {
            "source": {
                "path": str(source),
                "sha256": digest(source.read_bytes()),
                "original_name": "原件.pdf",
                "media_type": "application/pdf",
            },
            "pages": [{"page": 1, "image": str(image), "extraction_plan": plan}],
        }
    )
    return store, doc["id"], plan


def complete(store, doc_id, stage, worker, result):
    task = store.claim_task(doc_id, stage, worker)
    assert task is not None
    store.submit_task(
        doc_id,
        task["task_id"],
        task["lease_token"],
        {"input_sha256": task["input_sha256"], **result},
    )
    return task


def test_document_is_reviewed_before_publication_and_search(tmp_path):
    store, doc_id = document(tmp_path, 2)
    with pytest.raises(ValueError, match="Whole document"):
        store.publish(doc_id)
    complete(store, doc_id, "ocr", "ocr-1", {"markdown": "第一篇文章。"})
    complete(store, doc_id, "review", "review-1", {"verdict": "approved", "issues": []})
    assert store.claim_task(doc_id, "organization", "organizer") is None
    complete(store, doc_id, "ocr", "ocr-2", {"markdown": "第二篇文章。"})
    complete(store, doc_id, "review", "review-2", {"verdict": "approved", "issues": []})
    complete(
        store,
        doc_id,
        "organization",
        "organizer",
        {"articles": [{"title": "第一篇", "pages": [1]}, {"title": "第二篇", "pages": [2]}]},
    )
    publication = store.publish(doc_id)
    assert publication["approval_type"] == "machine"
    assert publication["human_approval"] is False
    assert store.status(doc_id)["state"] == "published"
    assert store.search("第二篇")[0]["page"] == 2
    assert store.publish(doc_id) == publication
    recovered = Store(tmp_path / "data")
    assert recovered.status(doc_id)["pages"][0]["ocr"]["markdown"] == "第一篇文章。"
    assert (
        recovered.status(doc_id)["organization"]["articles"][0]["bibliography"]["status"]
        == "unclaimed"
    )


def test_correction_is_separate_and_original_ocr_retained(tmp_path):
    store, doc_id = document(tmp_path)
    complete(store, doc_id, "ocr", "ocr", {"markdown": "一九三八年，李明赴北平。"})
    complete(
        store,
        doc_id,
        "review",
        "review",
        {
            "verdict": "corrections",
            "markdown": "一九三八年，李明赴北平。注：原件。",
            "corrections": [
                {"before": "北平。", "after": "北平。注：原件。", "reason": "原图有注释"}
            ],
        },
    )
    assert store.status(doc_id)["pages"][0]["state"] == "awaiting_verification"
    with pytest.raises(ValueError, match="Whole document"):
        store.publish(doc_id)
    with pytest.raises(ValueError, match="separate subagent"):
        store.claim_task(doc_id, "verify", "review")
    complete(store, doc_id, "verify", "independent-check", {"verdict": "approved", "issues": []})
    page = store.status(doc_id)["pages"][0]
    assert page["ocr"]["markdown"] == "一九三八年，李明赴北平。"
    assert page["approved"]["markdown"].endswith("注：原件。")
    assert page["candidate"]["corrections"][0]["reason"] == "原图有注释"


def test_unclear_original_pending_then_explicit_retry_preserves_verdict(tmp_path):
    store, doc_id = document(tmp_path)
    complete(store, doc_id, "ocr", "ocr", {"markdown": "[无法辨认]"})
    task = complete(
        store, doc_id, "review", "review", {"verdict": "pending", "issues": ["原件破损"]}
    )
    assert store.status(doc_id)["state"] == "pending"
    assert store.search("无法辨认") == []
    assert store.claim_task(doc_id, "review", "unscheduled-review") is None
    with pytest.raises(ValueError, match="Whole document"):
        store.publish(doc_id)
    retry = store.retry_task(doc_id, task["task_id"])
    assert retry["retry_of"] == task["task_id"]
    previous = store.get_task(doc_id, task["task_id"])
    assert previous["state"] == "superseded"
    assert previous["result"]["issues"] == ["原件破损"]
    complete(
        store,
        doc_id,
        "review",
        "retry-review",
        {"verdict": "pending", "issues": ["重试后仍无法辨认"]},
    )
    assert store.status(doc_id)["state"] == "pending"


def test_failed_verification_can_be_repaired_without_changing_retained_evidence(tmp_path):
    store, doc_id = document(tmp_path)
    complete(store, doc_id, "ocr", "ocr", {"markdown": "1938年。"})
    proposal = complete(
        store,
        doc_id,
        "review",
        "first-review",
        {
            "verdict": "corrections",
            "markdown": "1939年。",
            "corrections": [{"before": "1938", "after": "1939", "reason": "First proposal"}],
        },
    )
    failure = complete(
        store,
        doc_id,
        "verify",
        "first-verify",
        {
            "verdict": "pending",
            "issues": ["Clear original says 1940; proposed date is still wrong."],
        },
    )
    assert store.status(doc_id)["state"] == "pending"
    assert store.claim_task(doc_id, "review", "unscheduled-review") is None
    assert store.claim_task(doc_id, "verify", "unscheduled-verifier") is None
    with pytest.raises(ValueError, match="Whole document"):
        store.publish(doc_id)

    reopened = store.retry_task(doc_id, failure["task_id"])
    assert reopened["stage"] == "review"
    assert reopened["retry_of"] == failure["task_id"]
    assert store.claim_task(doc_id, "verify", "premature-verifier") is None
    complete(
        store,
        doc_id,
        "review",
        "fresh-review",
        {
            "verdict": "corrections",
            "markdown": "1940年。",
            "corrections": [{"before": "1938", "after": "1940", "reason": "Clear original date"}],
        },
    )
    old_verification = store.get_task(doc_id, failure["task_id"])
    assert old_verification["state"] == "superseded"
    assert old_verification["inputs"] == failure["inputs"]
    assert old_verification["inputs"]["markdown"] == "1939年。"
    assert old_verification["claim"]["input_sha256"] == failure["input_sha256"]
    assert store.get_task(doc_id, proposal["task_id"])["result"]["markdown"] == "1939年。"
    with pytest.raises(ValueError, match="separate subagent"):
        store.claim_task(doc_id, "verify", "fresh-review")
    complete(store, doc_id, "verify", "fresh-verifier", {"verdict": "approved", "issues": []})
    complete(
        store,
        doc_id,
        "organization",
        "organizer",
        {"articles": [{"title": "Date record", "pages": [1]}]},
    )
    publication = store.publish(doc_id)
    assert store.status(doc_id)["state"] == "published"
    assert store.status(doc_id)["pages"][0]["ocr"]["markdown"] == "1938年。"
    assert store.status(doc_id)["pages"][0]["approved"]["markdown"] == "1940年。"
    assert publication["human_approval"] is False
    assert store.search("1940")[0]["page"] == 1


def test_claim_exclusion_stale_hash_and_lease_recovery(tmp_path):
    store, doc_id = document(tmp_path)
    with ThreadPoolExecutor(2) as pool:
        claims = list(
            pool.map(lambda worker: Store(store.root).claim_task(doc_id, "ocr", worker), ["a", "b"])
        )
    claim = next(c for c in claims if c)
    assert sum(c is not None for c in claims) == 1
    with pytest.raises(ValueError, match="input hash"):
        store.submit_task(
            doc_id,
            claim["task_id"],
            claim["lease_token"],
            {"input_sha256": "wrong", "markdown": "text"},
        )
    path = store.root / "documents" / doc_id / "document.json"
    stored = json.loads(path.read_text(encoding="utf-8"))
    stored["tasks"][0]["claim"]["expires_at"] = time.time() - 1
    path.write_text(json.dumps(stored), encoding="utf-8")
    recovered = store.claim_task(doc_id, "ocr", "recovered")
    assert recovered["lease_token"] != claim["lease_token"]
    with pytest.raises(ValueError, match="active lease"):
        store.submit_task(
            doc_id,
            claim["task_id"],
            claim["lease_token"],
            {"input_sha256": claim["input_sha256"], "markdown": "old"},
        )
    store.submit_task(
        doc_id,
        recovered["task_id"],
        recovered["lease_token"],
        {"input_sha256": recovered["input_sha256"], "markdown": "new"},
    )


def test_changed_original_rejected_at_submission(tmp_path):
    store, doc_id = document(tmp_path)
    task = store.claim_task(doc_id, "ocr", "ocr")
    from pathlib import Path

    Path(task["inputs"]["image"]).write_bytes(b"changed")
    with pytest.raises(ValueError, match="image hash mismatch"):
        store.submit_task(
            doc_id,
            task["task_id"],
            task["lease_token"],
            {"input_sha256": task["input_sha256"], "markdown": "text"},
        )


def test_review_cannot_change_text_while_claiming_approval(tmp_path):
    store, doc_id = document(tmp_path)
    complete(store, doc_id, "ocr", "ocr", {"markdown": "原始文字"})
    with pytest.raises(ValueError, match="separate subagent"):
        store.claim_task(doc_id, "review", "ocr")
    task = store.claim_task(doc_id, "review", "review")
    with pytest.raises(ValueError, match="separate verification"):
        store.submit_task(
            doc_id,
            task["task_id"],
            task["lease_token"],
            {"input_sha256": task["input_sha256"], "verdict": "approved", "markdown": "改写文字"},
        )
    with pytest.raises(ValueError, match="Unresolved issues"):
        store.submit_task(
            doc_id,
            task["task_id"],
            task["lease_token"],
            {"input_sha256": task["input_sha256"], "verdict": "approved", "issues": ["日期错误"]},
        )


def test_organization_must_cover_valid_physical_pages(tmp_path):
    store, doc_id = document(tmp_path, 2)
    for number in range(2):
        complete(store, doc_id, "ocr", f"ocr-{number}", {"markdown": f"page-{number}"})
        complete(store, doc_id, "review", f"review-{number}", {"verdict": "approved"})
    task = store.claim_task(doc_id, "organization", "organizer")
    for pages in ([1], [1, 3]):
        with pytest.raises(ValueError, match="physical page|mapping"):
            store.submit_task(
                doc_id,
                task["task_id"],
                task["lease_token"],
                {
                    "input_sha256": task["input_sha256"],
                    "articles": [{"title": "Article", "pages": pages}],
                },
            )


def test_correction_requires_unique_complete_patch(tmp_path):
    store, doc_id = document(tmp_path)
    complete(store, doc_id, "ocr", "ocr", {"markdown": "字字，原文。"})
    task = store.claim_task(doc_id, "review", "review")
    with pytest.raises(ValueError, match="uniquely located"):
        store.submit_task(
            doc_id,
            task["task_id"],
            task["lease_token"],
            {
                "input_sha256": task["input_sha256"],
                "verdict": "corrections",
                "markdown": "文字，原文。",
                "corrections": [{"before": "字", "after": "文", "reason": "original"}],
            },
        )


def test_manifest_identity_and_published_artifact_integrity(tmp_path):
    store, doc_id = document(tmp_path)
    source = tmp_path / "different-source.png"
    source.write_bytes(b"second original")
    image = tmp_path / "page-1.png"
    doc = store.register_manifest(
        {
            "source": {
                "path": str(source),
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "original_name": "史料原件.png",
                "media_type": "image/png",
            },
            "pages": [{"page": 1, "image": str(image)}],
        }
    )
    assert doc["title"] == "史料原件"
    assert doc["source"]["original_name"] == "史料原件.png"
    assert doc["source"]["media_type"] == "image/png"
    complete(store, doc_id, "ocr", "ocr", {"markdown": "原始内容"})
    complete(store, doc_id, "review", "review", {"verdict": "approved"})
    complete(
        store, doc_id, "organization", "organizer", {"articles": [{"title": "原文", "pages": [1]}]}
    )
    publication = store.publish(doc_id)
    from pathlib import Path

    Path(publication["markdown_path"]).write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="Published markdown hash"):
        store.publish(doc_id)


def test_changed_saved_machine_result_rejected(tmp_path):
    store, doc_id = document(tmp_path)
    complete(store, doc_id, "ocr", "ocr", {"markdown": "原文"})
    path = store.root / "documents" / doc_id / "document.json"
    stored = json.loads(path.read_text(encoding="utf-8"))
    stored["tasks"][0]["result"]["markdown"] = "伪造文字"
    path.write_text(json.dumps(stored), encoding="utf-8")
    with pytest.raises(ValueError, match="machine result|result hash"):
        store.claim_task(doc_id, "review", "review")


def test_multiple_corrections_apply_to_original_spans_not_inserted_text(tmp_path):
    store, doc_id = document(tmp_path)
    complete(store, doc_id, "ocr", "ocr", {"markdown": "A B"})
    task = store.claim_task(doc_id, "review", "review")
    corrections = [
        {"before": "A", "after": "B", "reason": "First original glyph"},
        {"before": "B", "after": "C", "reason": "Second original glyph"},
    ]
    with pytest.raises(ValueError, match="entire corrected markdown"):
        store.submit_task(
            doc_id,
            task["task_id"],
            task["lease_token"],
            {
                "input_sha256": task["input_sha256"],
                "verdict": "corrections",
                "markdown": "C B",
                "corrections": corrections,
            },
        )
    store.submit_task(
        doc_id,
        task["task_id"],
        task["lease_token"],
        {
            "input_sha256": task["input_sha256"],
            "verdict": "corrections",
            "markdown": "B C",
            "corrections": corrections,
        },
    )
    page = store.status(doc_id)["pages"][0]
    assert page["ocr"]["markdown"] == "A B"
    assert page["candidate"]["markdown"] == "B C"
    assert page["state"] == "awaiting_verification"


def test_corrections_with_overlapping_original_spans_are_rejected(tmp_path):
    store, doc_id = document(tmp_path)
    complete(store, doc_id, "ocr", "ocr", {"markdown": "ABCD"})
    task = store.claim_task(doc_id, "review", "review")
    with pytest.raises(ValueError, match="overlap"):
        store.submit_task(
            doc_id,
            task["task_id"],
            task["lease_token"],
            {
                "input_sha256": task["input_sha256"],
                "verdict": "corrections",
                "markdown": "XBZ",
                "corrections": [
                    {"before": "ABC", "after": "XBC", "reason": "Original prefix"},
                    {"before": "BCD", "after": "BZ", "reason": "Overlapping original suffix"},
                ],
            },
        )
    assert "candidate" not in store.status(doc_id)["pages"][0]


def test_native_route_is_local_extraction_then_independent_review(tmp_path):
    store, doc_id, plan = routed_document(tmp_path, "native")
    doc = store.status(doc_id)
    assert doc["pages"][0]["ocr"]["approval_type"] == "extraction"
    assert doc["pages"][0]["ocr"]["worker_id"] == "local:pdfium"
    assert doc["tasks"][0]["stage"] == "extraction"
    assert store.claim_task(doc_id, "ocr", "ocr") is None
    assert (
        store.claim_task(doc_id, "review", "review")["inputs"]["markdown"]
        == plan["native_markdown"]
    )
    with pytest.raises(ValueError, match="Whole document"):
        store.publish(doc_id)


@pytest.mark.parametrize("route", ["mixed", "ocr", "blank_candidate"])
def test_non_native_routes_still_need_codex_ocr(tmp_path, route):
    store, doc_id, plan = routed_document(tmp_path, route)
    task = store.claim_task(doc_id, "ocr", "ocr")
    assert task["inputs"]["extraction_plan"] == plan
    assert "ocr" not in store.status(doc_id)["pages"][0]
    assert store.claim_task(doc_id, "review", "review") is None


def test_native_fallback_and_fullpage_budget_preserve_history(tmp_path):
    store, doc_id, _ = routed_document(tmp_path, "native")
    original = store.status(doc_id)["tasks"][0]
    result = store.fallback_page(doc_id, 1, "Bad reading order")
    assert result["fallback_budget"] == {"fullpage_switches": 1, "fullpage_retries": 0}
    assert store.get_task(doc_id, original["task_id"])["result"] == original["result"]
    complete(store, doc_id, "ocr", "ocr", {"markdown": "unclear"})
    complete(store, doc_id, "review", "review", {"verdict": "pending", "issues": ["unclear"]})
    with pytest.raises(ValueError, match="new evidence"):
        store.fallback_page(doc_id, 1, "Try again")
    store.fallback_page(doc_id, 1, "Original detail", new_evidence="Higher resolution crop")
    complete(store, doc_id, "ocr", "ocr-2", {"markdown": "still unclear"})
    complete(
        store, doc_id, "review", "review-2", {"verdict": "pending", "issues": ["still unclear"]}
    )
    with pytest.raises(ValueError, match="budget exhausted"):
        store.fallback_page(doc_id, 1, "Again", new_evidence="Another crop")
    assert len(store.status(doc_id)["pages"][0]["fallback_history"]) == 2
    assert store.human_queue()[0]["page"] == 1
    store.verify_evidence(store.checkpoints.read(doc_id))


def test_fallback_protects_leases_human_drafts_and_source(tmp_path):
    store, doc_id, _ = routed_document(tmp_path, "mixed")
    store.claim_task(doc_id, "ocr", "ocr")
    with pytest.raises(ValueError, match="active task leases"):
        store.fallback_page(doc_id, 1, "bad extraction")
    doc = store.checkpoints.read(doc_id)
    doc["tasks"][0]["claim"]["expires_at"] = 0
    store.checkpoints.write(doc)
    complete(store, doc_id, "ocr", "recovered", {"markdown": "text"})
    complete(store, doc_id, "review", "review", {"verdict": "pending", "issues": ["unclear"]})
    packet = store.human_packet(doc_id, 1)
    store.record_human_review(
        doc_id,
        1,
        input_sha256=packet["input_sha256"],
        reviewer="Researcher",
        markdown="Human draft",
        note="Still checking",
        verdict="pending",
    )
    with pytest.raises(ValueError, match="human decisions or drafts"):
        store.fallback_page(doc_id, 1, "replace draft")
    doc = store.checkpoints.read(doc_id)
    store.artifact_path(doc, doc["source"]["path"]).write_bytes(b"changed source")
    with pytest.raises(ValueError, match="source hash mismatch"):
        store.request_run(doc_id)
    with pytest.raises(ValueError, match="source hash mismatch"):
        store.fallback_page(doc_id, 1, "replace changed source")


def test_run_request_deduplicates_renews_and_waits_for_task_lease(tmp_path, monkeypatch):
    store, doc_id = document(tmp_path)
    import hrs_codex.run_workflow as module

    now = [1000.0]
    monkeypatch.setattr(module.time, "time", lambda: now[0])
    first = store.request_run(doc_id)
    assert store.request_run(doc_id) == first
    with ThreadPoolExecutor(2) as pool:
        claims = list(pool.map(lambda owner: Store(store.root).claim_run(owner), ["a", "b"]))
    run = next(c for c in claims if c)
    assert sum(c is not None for c in claims) == 1
    task = store.claim_task(doc_id, "ocr", "ocr", lease_seconds=100)
    assert store.renew_run(doc_id, run["token"], lease_seconds=1200)["lease_expires_at"] == 2200
    waiting = store.finish_run(doc_id, run["token"])
    assert waiting["state"] == "retryable_error"
    assert waiting["ready_after"] == task["expires_at"]
    assert waiting["technical_failures"] == 0
    assert store.claim_run("c") is None
    now[0] = 1101
    assert store.claim_run("c")["attempts"] == 2
    with pytest.raises(ValueError, match="active lease"):
        store.finish_run(doc_id, run["token"])


def test_run_technical_errors_stop_after_three_and_require_resume(tmp_path, monkeypatch):
    store, doc_id = document(tmp_path)
    import hrs_codex.run_workflow as module

    now = [1000.0]
    monkeypatch.setattr(module.time, "time", lambda: now[0])
    store.request_run(doc_id)
    for attempt in range(3):
        run = store.claim_run("runner")
        failed = store.finish_run(doc_id, run["token"], error={"type": "transport"})
        assert failed["technical_failures"] == attempt + 1
        assert failed["state"] == "retryable_error"
        assert store.status(doc_id)["pages"][0]["state"] == "awaiting_ocr"
        now[0] = failed["ready_after"] + 1
    store.request_run(doc_id)
    assert store.claim_run("runner") is None
    store.request_run(doc_id, resume=True)
    assert store.claim_run("runner")["technical_failures"] == 0


def test_expired_run_backoff_and_human_approval_requeue(tmp_path, monkeypatch):
    store, doc_id = document(tmp_path)
    import hrs_codex.run_workflow as module

    now = [1000.0]
    monkeypatch.setattr(module.time, "time", lambda: now[0])
    store.request_run(doc_id)
    old = store.claim_run("crashed", lease_seconds=1)
    now[0] = 1002
    assert store.claim_run("recovery") is None
    failed = store.status(doc_id)["run_request"]
    now[0] = failed["ready_after"] + 1
    run = store.claim_run("recovery")
    assert run["token"] != old["token"]
    complete(store, doc_id, "ocr", "ocr", {"markdown": "unclear"})
    complete(store, doc_id, "review", "review", {"verdict": "pending", "issues": ["torn source"]})
    assert store.finish_run(doc_id, run["token"])["state"] == "waiting_human"
    packet = store.human_packet(doc_id, 1)
    store.record_human_review(
        doc_id,
        1,
        input_sha256=packet["input_sha256"],
        reviewer="Researcher",
        markdown="Verified text",
        note="Verified original",
        verdict="approved",
    )
    assert store.status(doc_id)["run_request"]["state"] == "pending"
    assert store.claim_run("continue") is not None


def test_explicit_resume_recovers_expired_coordinator_without_racing_active_worker(
    tmp_path, monkeypatch
):
    store, doc_id = document(tmp_path)
    import hrs_codex.run_workflow as module

    now = [1000.0]
    monkeypatch.setattr(module.time, "time", lambda: now[0])
    store.request_run(doc_id)
    old = store.claim_run("crashed", lease_seconds=1)
    store.claim_task(doc_id, "ocr", "still-active", lease_seconds=100)
    now[0] = 1002
    resumed = store.request_run(doc_id, resume=True)
    assert resumed["state"] == "pending"
    assert resumed["technical_failures"] == 0
    assert any(row["event"] == "lease_expired" for row in resumed["history"])
    assert store.claim_run("recovery") is None
    now[0] = 1101
    assert store.claim_run("recovery")["token"] != old["token"]


def test_revision_preserves_old_search_until_new_review_and_publication(tmp_path):
    store, doc_id = document(tmp_path)
    complete(store, doc_id, "ocr", "ocr", {"markdown": "旧版史料"})
    complete(store, doc_id, "review", "review", {"verdict": "approved"})
    complete(
        store, doc_id, "organization", "organizer", {"articles": [{"title": "旧版", "pages": [1]}]}
    )
    old = store.publish(doc_id)
    with pytest.raises(ValueError, match="separate revision"):
        store.fallback_page(doc_id, 1, "new OCR")
    revision = store.begin_revision(doc_id, "Recheck a clear original")
    assert revision["revision"] == 2
    assert store.search("旧版")[0]["page"] == 1
    assert store.status(doc_id)["publication"] is None
    assert store.status(doc_id)["active_publication"]["publication"] == old
    with pytest.raises(ValueError, match="Whole document"):
        store.publish(doc_id)
    complete(
        store,
        doc_id,
        "review",
        "new-review",
        {"verdict": "pending", "issues": ["Need detailed original"]},
    )
    assert store.search("旧版")
    pending = next(t for t in store.status(doc_id)["tasks"] if t["state"] == "pending")
    store.retry_task(doc_id, pending["task_id"])
    complete(
        store,
        doc_id,
        "review",
        "new-review-2",
        {
            "verdict": "corrections",
            "markdown": "新版史料",
            "corrections": [{"before": "旧版", "after": "新版", "reason": "Original verified"}],
        },
    )
    complete(store, doc_id, "verify", "new-verifier", {"verdict": "approved"})
    complete(
        store,
        doc_id,
        "organization",
        "new-organizer",
        {"articles": [{"title": "新版", "pages": [1]}]},
    )
    new = store.publish(doc_id)
    assert new["markdown_sha256"] != old["markdown_sha256"]
    assert store.search("旧版") == []
    assert store.search("新版")
    current = store.status(doc_id)
    assert [version["revision"] for version in current["published_versions"]] == [1, 2]
    old_snapshot = store.publications.published_snapshot(current, current["published_versions"][0])
    assert old_snapshot["pages"][0]["approved"]["markdown"] == "旧版史料"
    assert store.begin_revision(doc_id, "Third review")["revision"] == 3
    assert store.search("新版")


def test_revision_snapshot_hash_and_original_mutation_blocked(tmp_path):
    store, doc_id, _ = routed_document(tmp_path, "native")
    complete(store, doc_id, "review", "review", {"verdict": "approved"})
    complete(
        store, doc_id, "organization", "organizer", {"articles": [{"title": "原文", "pages": [1]}]}
    )
    store.publish(doc_id)
    revision = store.begin_revision(doc_id, "Audit")
    doc = store.status(doc_id)
    reference = revision["active_publication"]["files"]["document.md"]
    store.artifact_path(doc, reference["path"]).write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="Archived publication evidence hash"):
        store.search("原生文本")


def test_revision_protects_explicit_human_decisions(tmp_path):
    store, doc_id = document(tmp_path)
    complete(store, doc_id, "ocr", "ocr", {"markdown": "unclear"})
    complete(store, doc_id, "review", "review", {"verdict": "pending", "issues": ["unclear"]})
    packet = store.human_packet(doc_id, 1)
    store.record_human_review(
        doc_id,
        1,
        input_sha256=packet["input_sha256"],
        reviewer="Historian",
        markdown="Human approved text",
        note="Checked original",
        verdict="approved",
    )
    complete(
        store,
        doc_id,
        "organization",
        "organizer",
        {"articles": [{"title": "Article", "pages": [1]}]},
    )
    store.publish(doc_id)
    with pytest.raises(ValueError, match="Human approvals and drafts"):
        store.begin_revision(doc_id, "Reopen", pages=[1])
    assert store.status(doc_id)["pages"][0]["approved"]["markdown"] == "Human approved text"


def test_claim_run_document_filter_does_not_claim_other_pending_documents(tmp_path):
    store, first_id = document(tmp_path)
    source = tmp_path / "second.pdf"
    source.write_bytes(b"different original")
    second_id = store.register(source, [{"page": 1, "image": str(tmp_path / "page-1.png")}])["id"]
    store.request_run(first_id)
    store.request_run(second_id)
    selected = store.claim_run("targeted", 900, document_id=second_id)
    assert selected["document_id"] == second_id
    assert store.status(first_id)["run_request"]["state"] == "pending"
    assert store.claim_run("same-target", document_id=second_id) is None
    assert store.claim_run("background")["document_id"] == first_id


def test_human_final_approval_during_running_coordinator_keeps_continuation(tmp_path):
    store, doc_id = document(tmp_path)
    store.request_run(doc_id)
    running = store.claim_run("coordinator")
    complete(store, doc_id, "ocr", "ocr", {"markdown": "unclear"})
    complete(
        store, doc_id, "review", "review", {"verdict": "pending", "issues": ["Original unclear"]}
    )
    packet = store.human_packet(doc_id, 1)
    store.record_human_review(
        doc_id,
        1,
        input_sha256=packet["input_sha256"],
        reviewer="Historian",
        markdown="Verified by human",
        note="Checked the original",
        verdict="approved",
    )
    assert store.status(doc_id)["run_request"]["continuation_requested"] is True
    finished = store.finish_run(doc_id, running["token"])
    assert finished["state"] == "pending"
    assert finished["technical_failures"] == 0
    resumed = store.claim_run("continue", document_id=doc_id)
    assert resumed is not None
    assert store.claim_task(doc_id, "organization", "organizer") is not None


def test_unique_location_check_includes_overlapping_occurrences(tmp_path):
    store, doc_id = document(tmp_path)
    complete(store, doc_id, "ocr", "ocr", {"markdown": "AAA"})
    task = store.claim_task(doc_id, "review", "review")
    with pytest.raises(ValueError, match="uniquely located"):
        store.submit_task(
            doc_id,
            task["task_id"],
            task["lease_token"],
            {
                "input_sha256": task["input_sha256"],
                "verdict": "corrections",
                "markdown": "BA",
                "corrections": [
                    {"before": "AA", "after": "B", "reason": "Ambiguous original span"}
                ],
            },
        )
