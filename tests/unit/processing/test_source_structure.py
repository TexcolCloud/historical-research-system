"""The source-fact seam preserves text, approval policy and original-first evidence."""

import pytest

from hrs.processing.errors import EvidenceMismatch, WorkflowStopped
from hrs.processing.hashing import digest
from hrs.processing.source_structure import ROLES, structure_snapshot, validate_structure
from tests.unit.processing.test_store import complete, document


def facts(text="正文", page=1, role="body", **extra):
    return {
        "schema_version": 1,
        "coverage": [
            {"role": name, "status": "present" if name == role else "unknown"}
            for name in sorted(ROLES)
        ],
        "fragments": [
            {
                "fragment_id": f"p{page}:{role}",
                "role": role,
                "layer": "source",
                "exact": text,
                **extra,
            }
        ],
        "relations": [],
    }


def freeze(store, identity, task):
    original, _ = store.original_page(
        identity, task["page"], task_id=task["task_id"], lease_token=task["lease_token"]
    )
    reading = {
        "image_receipt": original["image_receipt"],
        "image_sha256": original["image_sha256"],
        "observations": ["Original page inspected independently"],
        "unresolved": [],
    }
    store.review_candidate(identity, task["task_id"], task["lease_token"], reading)
    return reading


def submit_structure(store, identity, task, value, **extra):
    return store.submit_task(
        identity,
        task["task_id"],
        task["lease_token"],
        {
            "input_sha256": task["input_sha256"],
            "verdict": "complete",
            "source_structure": value,
            **extra,
        },
    )


def published(tmp_path, count=1):
    store, identity = document(tmp_path, count)
    for number in range(1, count + 1):
        complete(store, identity, "ocr", f"ocr-{number}", {"markdown": f"正文{number}"})
        complete(
            store, identity, "review", f"review-{number}", {"verdict": "approved", "issues": []}
        )
    complete(
        store,
        identity,
        "organization",
        "organizer",
        {"articles": [{"title": "正文", "pages": list(range(1, count + 1))}]},
    )
    store.publish(identity)
    return store, identity


def test_exact_fragments_are_bound_to_codepoints_without_rewriting_unicode():
    text = "页眉\n正文甲😀\n正文甲😀\n注释"
    value = facts("正文甲😀", prefix="😀\n", suffix="\n注释")
    bound = validate_structure(value, text, page=1)
    fragment = bound["fragments"][0]
    assert text[fragment["start"] : fragment["end"]] == "正文甲😀"
    assert fragment["text_sha256"] == digest("正文甲😀")
    assert "exact" not in fragment
    assert value["fragments"][0]["exact"] == "正文甲😀"


@pytest.mark.parametrize("mutation", ["coverage", "role", "hash", "offset", "subkind", "relation"])
def test_contract_rejects_partial_coverage_untyped_values_and_unbound_spans(mutation):
    value = facts()
    if mutation == "coverage":
        value["coverage"].pop()
    elif mutation == "role":
        value["fragments"][0]["role"] = "generic_attributes"
    elif mutation == "hash":
        value["fragments"][0]["text_sha256"] = "0" * 64
    elif mutation == "offset":
        value["fragments"][0]["start"] = 1
    elif mutation == "subkind":
        value = facts(role="frontmatter")
    else:
        value["relations"] = [{"kind": "anything", "status": "verified"}]
    with pytest.raises(ValueError):
        validate_structure(value, "正文", page=1)


def test_old_omissions_are_unknown_and_snapshot_does_not_change_source_or_policy(tmp_path):
    store, identity = published(tmp_path)
    before = store.status(identity)
    snapshot = structure_snapshot(before)
    assert snapshot["pages"][0]["status"] == "unreviewed"
    assert all(row["status"] == "unknown" for row in snapshot["pages"][0]["coverage"])
    assert before == store.status(identity)


def test_explicit_backfill_is_idempotent_frozen_original_first_and_preserves_publication(tmp_path):
    store, identity = published(tmp_path)
    original = store.status(identity)
    assert store.backfill_structure(identity)["queued_pages"] == [1]
    assert store.backfill_structure(identity)["queued_pages"] == []
    task = store.claim_task(identity, "structure", "fresh-sol")
    assert task["source_first_required"] is True
    with pytest.raises(ValueError, match="original-first"):
        submit_structure(store, identity, task, facts("正文1"))
    reading = freeze(store, identity, task)
    with pytest.raises(ValueError, match="cannot change"):
        store.review_candidate(
            identity, task["task_id"], task["lease_token"], {**reading, "observations": ["changed"]}
        )
    submit_structure(store, identity, task, facts("正文1"))
    after = store.status(identity)
    assert after["state"] == "published"
    assert after["publication"] == original["publication"]
    assert after["pages"][0]["approved"] == original["pages"][0]["approved"]
    assert after.get("processing_policy") == original.get("processing_policy")
    assert store.structure_snapshot(identity)["pages"][0]["status"] == "reviewed"
    assert store.backfill_structure(identity)["queued_pages"] == []
    assert store.backfill_structure(identity, pages=[1], force=True)["queued_pages"] == [1]


def test_backfill_uses_native_sol_session_and_replay_is_idempotent(tmp_path):
    store, identity = published(tmp_path)
    store.backfill_structure(identity)
    store.request_run(identity)
    assignments = store.dispatch.reserve(identity)["assignments"]
    assert len(assignments) == 1
    assignment = assignments[0]
    assert assignment["stage"] == "structure" and assignment["agent_type"] == "historical_review"
    task, image, _ = store.dispatch.open(assignment["session_token"], "native-structure")
    assert task["actual_model"] == "gpt-6.1-sol"
    store.dispatch.freeze(
        assignment["session_token"],
        "native-structure",
        {
            "image_receipt": image["image_receipt"],
            "image_sha256": image["image_sha256"],
            "observations": ["Full original inspected"],
            "unresolved": [],
        },
    )
    result = {
        "input_sha256": task["input_sha256"],
        "verdict": "complete",
        "source_structure": facts("正文1"),
    }
    store.dispatch.submit(assignment["session_token"], "native-structure", result)
    version = store.status(identity)["structure_version"]
    assert store.dispatch.submit(assignment["session_token"], "native-structure", result)[
        "already_committed"
    ]
    assert store.status(identity)["structure_version"] == version


def test_pause_cannot_be_reopened_by_backfill_and_explicit_resume_preserves_publication(tmp_path):
    store, identity = published(tmp_path)
    store.backfill_structure(identity)
    store.request_run(identity)
    store.pause_document(identity)
    with pytest.raises(WorkflowStopped):
        store.backfill_structure(identity, pages=[1], force=True)
    assert store.dispatch.reserve(identity)["assignments"] == []
    store.request_run(identity, resume=True)
    task = store.claim_task(identity, "structure", "fresh-resumed")
    freeze(store, identity, task)
    submit_structure(store, identity, task, facts("正文1"))
    assert store.status(identity)["state"] == "published"


def test_tampered_structure_cannot_survive_evidence_validation(tmp_path):
    store, identity = published(tmp_path)
    store.backfill_structure(identity)
    task = store.claim_task(identity, "structure", "independent")
    freeze(store, identity, task)
    submit_structure(store, identity, task, facts("正文1"))
    damaged = store.status(identity)
    damaged["pages"][0]["source_structure"]["fragments"][0]["end"] = 1
    store.checkpoints.write(damaged)
    with pytest.raises(EvidenceMismatch, match="differs"):
        store.structure_snapshot(identity)


def test_new_review_requires_complete_structure_and_unknown_metadata_is_not_content_pending(
    tmp_path,
):
    store, identity = document(tmp_path)
    store.freeze_policy(identity, {"review_failure": "bounded", "structure_contract_version": 1})
    complete(store, identity, "ocr", "terra", {"markdown": "正文"})
    task = store.claim_task(identity, "review", "sol")
    freeze(store, identity, task)
    base = {"input_sha256": task["input_sha256"], "verdict": "approved", "issues": []}
    with pytest.raises(ValueError, match="requires source_structure"):
        store.submit_task(identity, task["task_id"], task["lease_token"], base)
    store.submit_task(
        identity, task["task_id"], task["lease_token"], {**base, "source_structure": facts()}
    )
    assert store.status(identity)["pages"][0]["state"] == "approved"
    assert store.structure_snapshot(identity)["pages"][0]["status"] == "reviewed"


def test_unverified_cross_page_relation_stays_unknown_and_cannot_certify_target(tmp_path):
    store, identity = published(tmp_path)
    store.backfill_structure(identity)
    task = store.claim_task(identity, "structure", "independent")
    freeze(store, identity, task)
    value = facts("正文1")
    value["relations"] = [
        {
            "kind": "continues",
            "from_fragment_id": "p1:body",
            "to_fragment_id": "p2:body",
            "status": "verified",
            "evidence": "uninspected target",
        }
    ]
    with pytest.raises(ValueError, match="cross-page"):
        submit_structure(store, identity, task, value)
    value["relations"][0]["status"] = "unknown"
    submit_structure(store, identity, task, value)
    assert store.structure_snapshot(identity)["pages"][0]["relations"][0]["status"] == "unknown"


def test_verified_cross_page_relation_requires_both_originals_and_survives_receipt_checks(tmp_path):
    store, identity = published(tmp_path, count=2)
    store.backfill_structure(identity)
    first = store.claim_task(identity, "structure", "independent-1")
    freeze(store, identity, first)
    submit_structure(store, identity, first, facts("正文1"))
    second = store.claim_task(identity, "structure", "independent-2")
    freeze(store, identity, second)
    relations = [
        {
            "kind": "continues",
            "from_fragment_id": "p1:body",
            "to_fragment_id": "p2:body",
            "status": "verified",
            "evidence": "The original sentence continues onto the next page",
        }
    ]
    with pytest.raises(ValueError, match="both endpoints"):
        submit_structure(
            store, identity, second, facts("正文2", page=2), structure_relations=relations
        )
    store.original_page(identity, 1, task_id=second["task_id"], lease_token=second["lease_token"])
    submit_structure(store, identity, second, facts("正文2", page=2), structure_relations=relations)
    snapshot = store.structure_snapshot(identity)
    assert snapshot["pages"][0]["relations"] == relations
    store.verify_evidence(store.status(identity))
    # A new fragment interpretation with the same source text cannot silently reuse a prior link.
    store.backfill_structure(identity, pages=[1], force=True)
    recheck = store.claim_task(identity, "structure", "independent-3")
    freeze(store, identity, recheck)
    replacement = facts("正文1")
    replacement["fragments"][0]["layer"] = "unknown"
    submit_structure(store, identity, recheck, replacement)
    assert store.structure_snapshot(identity)["pages"][0]["relations"] == []


@pytest.mark.parametrize("route", ["native", "mixed", "ocr", "blank_candidate"])
def test_prepare_new_import_reuses_native_candidate_with_strict_review_and_frozen_policy(
    tmp_path, monkeypatch, route
):
    from hrs.processing.application import prepare
    from hrs.processing.store import Store

    source, image = tmp_path / "new.pdf", tmp_path / "page.png"
    source.write_bytes(b"new-native-source")
    image.write_bytes(b"original-pixels")
    manifest = {
        "source": {"path": str(source), "sha256": digest(source.read_bytes())},
        "pages": [
            {
                "page": 1,
                "image": str(image),
                "extraction_plan": {
                    "route": route,
                    "native_markdown": "候选正文",
                    "native_sha256": digest("候选正文"),
                },
            }
        ],
    }
    monkeypatch.setattr("hrs.processing.application.render_document", lambda *_: manifest)
    store = Store(tmp_path / "workspace")
    new = prepare(store, str(source))
    assert new["processing_policy"]["structure_contract_version"] == 1
    assert new["pages"][0]["extraction_plan"]["native_markdown"] == "候选正文"
    assert prepare(store, str(source))["processing_policy"] == new["processing_policy"]
    if route != "native":
        assert [task["stage"] for task in new["tasks"]] == ["ocr"]
        assert "ocr" not in new["pages"][0] and "approved" not in new["pages"][0]
        assert store.claim_task(new["id"], "review", "fresh-sol") is None
        assert store.claim_task(new["id"], "ocr", "fresh-terra")["actual_model"] == "gpt-6.1-terra"
        return
    assert [task["stage"] for task in new["tasks"]] == ["extraction", "review"]
    assert new["pages"][0]["ocr"]["approval_type"] == "extraction"
    assert new["pages"][0]["state"] == "awaiting_review"
    assert "approved" not in new["pages"][0]
    assert store.claim_task(new["id"], "ocr", "fresh-terra") is None
    with pytest.raises(ValueError, match="Whole document"):
        store.publish(new["id"])
    task = store.claim_task(new["id"], "review", "fresh-sol", source_first=False)
    assert task["source_first_required"] is True and task["actual_model"] == "gpt-6.1-sol"
    result = {"input_sha256": task["input_sha256"], "verdict": "approved", "issues": []}
    with pytest.raises(ValueError, match="original-first"):
        store.submit_task(new["id"], task["task_id"], task["lease_token"], result)
    freeze(store, new["id"], task)
    with pytest.raises(ValueError, match="requires source_structure"):
        store.submit_task(new["id"], task["task_id"], task["lease_token"], result)
    assert "approved" not in store.status(new["id"])["pages"][0]
    store.submit_task(
        new["id"],
        task["task_id"],
        task["lease_token"],
        {**result, "source_structure": facts("候选正文")},
    )
    assert store.status(new["id"])["pages"][0]["approved"]["markdown"] == "候选正文"
    legacy = Store(tmp_path / "legacy-workspace")
    frozen_policy = {"review_failure": "human", "review_model": "gpt-6.1-sol"}
    old = legacy.register_manifest(manifest, processing_policy=frozen_policy)
    assert prepare(legacy, str(source))["processing_policy"] == frozen_policy
    assert [task["stage"] for task in legacy.status(old["id"])["tasks"]] == ["extraction", "review"]


def test_structure_admission_excludes_source_and_all_prior_structure_authors(tmp_path):
    store, identity = published(tmp_path)
    for worker in ("fresh-structure-1", "fresh-structure-2"):
        store.backfill_structure(identity, pages=[1], force=True)
        before = store.status(identity)
        for excluded in (
            "ocr-1",
            "review-1",
            *(("fresh-structure-1",) if worker == "fresh-structure-2" else ()),
        ):
            assert store.claim_task(identity, "structure", excluded) is None
            assert store.status(identity) == before
        task = store.claim_task(identity, "structure", worker)
        assert task and task["actual_model"] == "gpt-6.1-sol"
        freeze(store, identity, task)
        submit_structure(store, identity, task, facts("正文1"))
    store.backfill_structure(identity, pages=[1], force=True)
    before = store.status(identity)
    for excluded in ("ocr-1", "review-1", "fresh-structure-1", "fresh-structure-2"):
        assert store.claim_task(identity, "structure", excluded) is None
    assert store.status(identity) == before
    assert store.claim_task(identity, "structure", "fresh-structure-3") is not None


def test_native_reservation_cannot_bind_a_source_author_but_accepts_a_fresh_session_identity(
    tmp_path,
):
    store, identity = published(tmp_path)
    store.backfill_structure(identity)
    store.request_run(identity)
    assignment = store.dispatch.reserve(identity)["assignments"][0]
    token = assignment["session_token"]
    before = store.status(identity)
    with pytest.raises(ValueError, match="independent native worker"):
        store.dispatch.open(token, "ocr-1")
    assert store.status(identity) == before
    worker = "structure-" + token
    task, original, _ = store.dispatch.open(token, worker)
    assert task["worker_id"] == worker and task["actual_model"] == "gpt-6.1-sol"
    store.dispatch.freeze(
        token,
        worker,
        {
            "image_receipt": original["image_receipt"],
            "image_sha256": original["image_sha256"],
            "observations": ["Fresh native Sol original-first structure inspection"],
            "unresolved": [],
        },
    )
    store.dispatch.submit(
        token,
        worker,
        {
            "input_sha256": task["input_sha256"],
            "verdict": "complete",
            "source_structure": facts("正文1"),
        },
    )
    assert store.structure_snapshot(identity)["pages"][0]["status"] == "reviewed"


def test_warm_structure_worker_remains_eligible_for_an_independent_different_page(tmp_path):
    store, identity = published(tmp_path, count=2)
    store.backfill_structure(identity)
    first = store.claim_task(identity, "structure", "unique-sol-session-token")
    freeze(store, identity, first)
    submit_structure(store, identity, first, facts("正文1"))
    store.backfill_structure(identity, pages=[1], force=True)
    # Its completed author identity excludes only page 1, not the other page in this session.
    second = store.claim_task(identity, "structure", "unique-sol-session-token")
    assert second and second["page"] == 2 and second["actual_model"] == "gpt-6.1-sol"
    freeze(store, identity, second)
    submit_structure(store, identity, second, facts("正文2", page=2))
    assert all(row["status"] == "reviewed" for row in store.structure_snapshot(identity)["pages"])
    assert [
        task["page"]
        for task in store.status(identity)["tasks"]
        if task["stage"] == "structure" and task["state"] == "ready"
    ] == [1]


def test_structure_backfill_cas_rejects_stale_snapshot_without_any_side_effects(tmp_path):
    store, identity = published(tmp_path)
    original = store.structure_snapshot(identity)
    before = store.status(identity)
    with pytest.raises(ValueError, match="Structure snapshot changed"):
        store.backfill_structure(identity, pages=[1], force=True, expected_snapshot_sha256="0" * 64)
    assert store.status(identity) == before
    assert store.backfill_structure(
        identity, pages=[1], expected_snapshot_sha256=original["snapshot_sha256"]
    )["queued_pages"] == [1]
    task = store.claim_task(identity, "structure", "fresh-cas-inspection")
    freeze(store, identity, task)
    submit_structure(store, identity, task, facts("正文1"))
    before = store.status(identity)
    with pytest.raises(ValueError, match="Structure snapshot changed"):
        store.backfill_structure(
            identity, pages=[1], force=True, expected_snapshot_sha256=original["snapshot_sha256"]
        )
    assert store.status(identity) == before


def test_structure_pages_bound_output_and_keep_the_full_snapshot_identity(tmp_path):
    store, identity = document(tmp_path, count=6)
    before = store.status(identity)
    full = store.structure_snapshot(identity)
    first = store.structure_pages(identity)
    last = store.structure_pages(identity, offset=4, limit=20)
    assert first["pages"] == full["pages"][:4]
    assert last["pages"] == full["pages"][4:]
    assert first["snapshot_sha256"] == last["snapshot_sha256"] == full["snapshot_sha256"]
    assert first["structure_version"] == last["structure_version"] == full["structure_version"]
    assert (first["page_count"], first["offset"], first["limit"], first["next_offset"]) == (
        6,
        0,
        4,
        4,
    )
    assert (last["offset"], last["limit"], last["next_offset"]) == (4, 20, None)
    assert first["truncated"] is last["truncated"] is True
    assert store.structure_pages(identity, limit=20)["truncated"] is False
    for arguments in (
        {"offset": True},
        {"offset": 1.0},
        {"offset": "1"},
        {"offset": -1},
        {"offset": 6},
        {"limit": True},
        {"limit": 1.0},
        {"limit": "1"},
        {"limit": 0},
        {"limit": 21},
    ):
        with pytest.raises(ValueError, match="Structure (offset|limit)"):
            store.structure_pages(identity, **arguments)
    assert store.status(identity) == before


@pytest.mark.parametrize("damaged", ["image", "text", "receipt"])
def test_structure_pages_validate_only_selected_pages_once_without_holding_workspace_lock(
    tmp_path, monkeypatch, damaged
):
    from hrs.processing import evidence

    store, identity = published(tmp_path, count=3)
    doc = store.status(identity)
    target = doc["pages"][2]
    if damaged == "image":
        store.artifact_path(doc, target["image"]).write_bytes(b"changed image")
    elif damaged == "text":
        target["approved"]["markdown"] = "changed approved text"
        store.checkpoints.write(doc)
    else:
        task = next(
            task for task in doc["tasks"] if task["task_id"] == target["approved"]["task_id"]
        )
        task["receipt"]["result_sha256"] = "0" * 64
        store.checkpoints.write(doc)
    hashed, original_digest = [], evidence.file_digest

    def checked_digest(path):
        assert not store.lock.is_locked
        hashed.append(path)
        return original_digest(path)

    monkeypatch.setattr(evidence, "file_digest", checked_digest)
    result = store.structure_pages(identity, offset=0, limit=2)
    assert [page["page"] for page in result["pages"]] == [1, 2]
    assert hashed == [
        store.artifact_path(doc, doc["source"]["path"]),
        *(store.artifact_path(doc, page["image"]) for page in doc["pages"][:2]),
    ]
    with pytest.raises(EvidenceMismatch):
        store.structure_pages(identity, offset=2, limit=1)


def test_structure_pages_retry_global_snapshot_changes_before_returning(tmp_path, monkeypatch):
    store, identity = document(tmp_path, count=3)
    before = store.structure_snapshot(identity)
    verify, calls = store.context.evidence.verify, []

    def verify_then_change(doc, *, pages):
        calls.append(pages)
        verify(doc, pages=pages)
        if len(calls) == 1:
            updated = store.status(identity)
            updated["structure_version"] = 1
            store.checkpoints.write(updated)

    monkeypatch.setattr(store.context.evidence, "verify", verify_then_change)
    result = store.structure_pages(identity, limit=1)
    assert calls == [[1], [1]]
    assert result["structure_version"] == 1
    assert result["snapshot_sha256"] != before["snapshot_sha256"]
    assert (
        result["snapshot_sha256"] == structure_snapshot(store.status(identity))["snapshot_sha256"]
    )
