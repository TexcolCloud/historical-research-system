"""Synthetic protocol checks: original observations precede candidate judgment."""

import json

import pytest
from test_store import complete, document


def test_compact_detail_receipts_and_bounded_candidate_access(tmp_path):
    from PIL import Image

    from hrs_codex.details import original_detail
    from hrs_codex.server import prepare
    from hrs_codex.store import Store

    source = tmp_path / "raster.png"
    Image.new("RGB", (60, 80), "white").save(source)
    store = Store(tmp_path / "library")
    doc_id = prepare(store, str(source))["id"]
    candidate = "甲😀" * 3000
    complete(store, doc_id, "ocr", "ocr", {"markdown": candidate})
    task = store.claim_task(doc_id, "review", "review", source_first=True, scheduled=True)
    with pytest.raises(ValueError, match="observations"):
        store.candidate_text(doc_id, task["task_id"], task["lease_token"])
    original = reading(store, doc_id, task)
    detail, _ = original_detail(store, doc_id, 1, [0, 0, 1, 1])
    short = {k: detail[k] for k in ("image_sha256", "metadata_sha256")}
    store.review_candidate(
        doc_id, task["task_id"], task["lease_token"], {**original, "original_details": [short]}
    )
    with pytest.raises(ValueError, match="complete candidate"):
        submit(store, doc_id, task, {"verdict": "approved"})
    chunks = [
        store.candidate_text(doc_id, task["task_id"], task["lease_token"], start=i, limit=1000)
        for i in range(0, len(candidate), 1000)
    ]
    assert "".join(c["markdown"] for c in chunks) == candidate
    assert all(c["total_codepoints"] == len(candidate) for c in chunks)
    submit(store, doc_id, task, {"verdict": "approved"})


def reading(store, doc_id, task, *, unresolved=None):
    metadata, _ = store.original_page(
        doc_id, task["page"], task_id=task["task_id"], lease_token=task["lease_token"]
    )
    return {
        "image_sha256": metadata["image_sha256"],
        "image_receipt": metadata["image_receipt"],
        "observations": ["Synthetic original observation: source terminology and numbers checked."],
        "unresolved": unresolved or [],
    }


def submit(store, doc_id, task, result):
    return store.submit_task(
        doc_id,
        task["task_id"],
        task["lease_token"],
        {"input_sha256": task["input_sha256"], **result},
    )


def test_reading_is_required_immutable_and_bound_to_final_decision(tmp_path):
    store, doc_id = document(tmp_path)
    complete(store, doc_id, "ocr", "synthetic-ocr", {"markdown": "Synthetic text"})
    task = store.claim_task(doc_id, "review", "synthetic-independent-review", source_first=True)
    with pytest.raises(ValueError, match="original-first"):
        submit(store, doc_id, task, {"verdict": "approved"})
    with pytest.raises(ValueError, match="Open original_image"):
        store.review_candidate(doc_id, task["task_id"], task["lease_token"], {})
    evidence = reading(store, doc_id, task)
    with pytest.raises(ValueError, match="Open original_image"):
        store.review_candidate(
            doc_id, task["task_id"], task["lease_token"], {**evidence, "image_receipt": "wrong"}
        )
    packet = store.review_candidate(doc_id, task["task_id"], task["lease_token"], evidence)
    assert packet["inputs"]["markdown"] == "Synthetic text"
    assert store.review_candidate(doc_id, task["task_id"], task["lease_token"], evidence) == packet
    with pytest.raises(ValueError, match="cannot change"):
        store.review_candidate(
            doc_id,
            task["task_id"],
            task["lease_token"],
            {**evidence, "observations": ["Rewritten after exposure"]},
        )
    submit(store, doc_id, task, {"verdict": "approved"})
    retained = store.get_task(doc_id, task["task_id"])
    assert retained["receipt"]["source_reading_sha256"] == packet["source_reading"]["sha256"]
    assert retained["source_reading"]["human_approval"] is False
    complete(
        store,
        doc_id,
        "organization",
        "synthetic-org",
        {"articles": [{"title": "Synthetic", "pages": [1]}]},
    )
    store.publish(doc_id)
    path = store._directory(doc_id) / "document.json"
    changed = json.loads(path.read_text("utf-8"))
    changed["tasks"][1]["source_reading"]["reading"]["observations"] = ["Changed evidence"]
    path.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match="reading evidence hash"):
        store.publish(doc_id)


def test_unresolved_source_observations_cannot_turn_into_approval_after_candidate_exposure(
    tmp_path,
):
    store, doc_id = document(tmp_path)
    complete(store, doc_id, "ocr", "synthetic-ocr", {"markdown": "Ambiguous source"})
    task = store.claim_task(doc_id, "review", "synthetic-review", source_first=True)
    evidence = reading(store, doc_id, task, unresolved=["Synthetic source ambiguity"])
    store.review_candidate(doc_id, task["task_id"], task["lease_token"], evidence)
    with pytest.raises(ValueError, match="Unresolved original observations"):
        submit(store, doc_id, task, {"verdict": "approved", "issues": []})
    submit(store, doc_id, task, {"verdict": "pending", "issues": evidence["unresolved"]})
    assert store.status(doc_id)["pages"][0]["state"] == "pending"
    assert store.human_queue()[0]["document_id"] == doc_id
    assert (
        store.human_packet(doc_id, 1)["history"][-1]["source_reading"]["reading"]["unresolved"]
        == evidence["unresolved"]
    )
    assert store.search("Ambiguous") == []


def test_expired_reviewer_requires_new_image_receipt_and_preserves_prior_observations(
    tmp_path, monkeypatch
):
    from hrs_codex import store as module

    store, doc_id = document(tmp_path)
    complete(store, doc_id, "ocr", "synthetic-ocr", {"markdown": "Synthetic text"})
    now = [1000.0]
    monkeypatch.setattr(module.time, "time", lambda: now[0])
    first = store.claim_task(
        doc_id, "review", "synthetic-first", lease_seconds=1, source_first=True
    )
    old_reading = reading(store, doc_id, first)
    store.review_candidate(doc_id, first["task_id"], first["lease_token"], old_reading)
    now[0] = 1002
    second = store.claim_task(doc_id, "review", "synthetic-second", source_first=True)
    assert "source_reading" not in second
    assert second["source_reading_history"][0]["reading"] == old_reading
    with pytest.raises(ValueError, match="Open original_image"):
        store.review_candidate(doc_id, second["task_id"], second["lease_token"], old_reading)
    new_reading = reading(store, doc_id, second)
    assert new_reading["image_receipt"] != old_reading["image_receipt"]
    store.review_candidate(doc_id, second["task_id"], second["lease_token"], new_reading)
    submit(store, doc_id, second, {"verdict": "approved"})
