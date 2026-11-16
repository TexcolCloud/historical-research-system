"""Structure inspection and rechecks cross the existing workspace/evidence seam."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hrs.api.deps import get_engine, get_settings
from hrs.api.routes import runs
from hrs.library import source_structure as module
from hrs.library.errors import ServiceError
from hrs.library.native_workspace import WrongWorkspace
from hrs.processing.source_structure import ROLES
from tests.unit.processing.test_store import complete, document


@pytest.fixture
def access(tmp_path, monkeypatch):
    store, document_id = document(tmp_path, count=3)
    approved_text = "序言😀" + "甲" * 420 + "正文尾"
    for number, text in ((1, approved_text), (2, "第二页完整正文")):
        complete(store, document_id, "ocr", f"ocr-{number}", {"markdown": text})
        complete(store, document_id, "review", f"review-{number}", {"verdict": "approved"})
    complete(store, document_id, "ocr", "ocr-3", {"markdown": "尚未批准的候选文本不能展示"})
    store.backfill_structure(document_id, pages=[1])
    task = store.claim_task(document_id, "structure", "independent-structure")
    original, _ = store.original_page(
        document_id, 1, task_id=task["task_id"], lease_token=task["lease_token"]
    )
    store.review_candidate(
        document_id,
        task["task_id"],
        task["lease_token"],
        {
            "image_receipt": original["image_receipt"],
            "image_sha256": original["image_sha256"],
            "observations": [
                "Synthetic original observation for the access contract; not real source approval"
            ],
            "unresolved": [],
        },
    )
    store.submit_task(
        document_id,
        task["task_id"],
        task["lease_token"],
        {
            "input_sha256": task["input_sha256"],
            "verdict": "complete",
            "source_structure": {
                "schema_version": 1,
                "coverage": [
                    {"role": role, "status": "present" if role == "frontmatter" else "unknown"}
                    for role in sorted(ROLES)
                ],
                "fragments": [
                    {
                        "fragment_id": "p1:preface",
                        "role": "frontmatter",
                        "layer": "source",
                        "subkind": "preface",
                        "exact": approved_text,
                    }
                ],
                "relations": [],
            },
        },
    )
    engine, run_id = MagicMock(), uuid4()
    service = module.SourceStructure(SimpleNamespace(codex_root=store.root), engine)
    native = MagicMock(return_value=(store, document_id))
    monkeypatch.setattr(service.navigation, "native", native)
    active = MagicMock()
    monkeypatch.setattr(module, "require_run_active", active)
    return SimpleNamespace(
        store=store,
        document_id=document_id,
        run_id=run_id,
        service=service,
        native=native,
        active=active,
        engine=engine,
        text=approved_text,
    )


def request(access, *, expected=None, pages=None, force=False):
    return runs.StructureRecheck(
        expected_snapshot_sha256=(
            expected or access.store.structure_snapshot(access.document_id)["snapshot_sha256"]
        ),
        pages=pages,
        force=force,
    )


def test_page_delegates_workspace_identity_and_returns_source_bound_unicode_excerpt(access):
    before = access.store.status(access.document_id)
    result = access.service.page(access.run_id, 1)
    access.native.assert_called_once_with(access.run_id)
    assert result["document_id"] == access.document_id
    assert result["page_count"] == 3 and result["reviewed_pages"] == 1
    assert result["status"] == "reviewed"
    assert (
        result["image"] == f"/api/v2/runs/{access.run_id}/artifacts/{before['pages'][0]['image']}"
    )
    fragment = result["fragments"][0]
    assert fragment["text"] == access.text[:400] and fragment["text_truncated"] is True
    assert fragment["end"] == len(access.text) and fragment["subkind"] == "preface"
    assert result["markdown_sha256"] == before["pages"][0]["approved"]["sha256"]
    assert result["image_sha256"] == before["pages"][0]["image_sha256"]
    assert access.store.status(access.document_id) == before


@pytest.mark.parametrize("page", [0, -1, 4])
def test_page_rejects_outside_physical_page_sequence(access, page):
    with pytest.raises(ServiceError) as error:
        access.service.page(access.run_id, page)
    assert error.value.status_code == 404


def test_unreviewed_page_remains_unknown_and_pending_candidate_text_is_never_exposed(access):
    approved = access.service.page(access.run_id, 2)
    pending = access.service.page(access.run_id, 3)
    assert approved["status"] == pending["status"] == "unreviewed"
    assert all(row["status"] == "unknown" for row in pending["coverage"])
    assert pending["markdown_sha256"] is None
    assert approved["fragments"] == pending["fragments"] == []
    assert "尚未批准" not in str(pending)
    assert access.store.status(access.document_id)["pages"][2]["ocr"]["markdown"].startswith(
        "尚未批准"
    )


@pytest.mark.parametrize("operation", ["page", "recheck"])
def test_workspace_mismatch_is_propagated_before_any_native_access(access, monkeypatch, operation):
    access.native.side_effect = WrongWorkspace()
    snapshot = MagicMock(side_effect=AssertionError("A foreign workspace must not be inspected"))
    monkeypatch.setattr(access.store, "structure_snapshot", snapshot)
    with pytest.raises(WrongWorkspace) as error:
        if operation == "page":
            access.service.page(access.run_id, 1)
        else:
            access.service.recheck(
                access.run_id,
                runs.StructureRecheck(expected_snapshot_sha256="0" * 64, pages=[1], force=True),
            )
    assert error.value.status_code == 409
    access.native.assert_called_once_with(access.run_id)
    snapshot.assert_not_called()


def test_recheck_rejects_stale_snapshot_before_queue_or_content_changes(access):
    before = access.store.status(access.document_id)
    with pytest.raises(ServiceError) as error:
        access.service.recheck(
            access.run_id, request(access, expected="0" * 64, pages=[1], force=True)
        )
    assert error.value.status_code == 409
    assert access.store.status(access.document_id) == before
    access.active.assert_called_once_with(
        access.engine.begin.return_value.__enter__.return_value, str(access.run_id)
    )


def test_directed_recheck_queues_only_selected_page_and_preserves_content_and_decisions(access):
    before = access.store.status(access.document_id)
    prior_tasks = {task["task_id"] for task in before["tasks"]}
    skipped = access.service.recheck(access.run_id, request(access, pages=[1]))
    assert skipped["queued_pages"] == [] and "run_request" not in skipped
    result = access.service.recheck(access.run_id, request(access, pages=[1], force=True))
    assert result["queued_pages"] == [1]
    assert result["content_approval"] is result["human_approval"] is False
    assert result["run_request"]["state"] == "pending"
    after = access.store.status(access.document_id)
    assert [
        (task["stage"], task["page"])
        for task in after["tasks"]
        if task["task_id"] not in prior_tasks
    ] == [("structure", 1)]
    assert after["pages"] == before["pages"]
    assert after.get("processing_policy") == before.get("processing_policy")
    assert (
        after["organization"] == before["organization"]
        and after["publication"] == before["publication"]
    )
    assert after["structure_backfills"][-1]["directed_recheck"] is True
    duplicate = access.service.recheck(access.run_id, request(access, pages=[1], force=True))
    assert duplicate["queued_pages"] == [] and "run_request" not in duplicate


def test_recheck_skips_unapproved_page_and_does_not_start_a_run(access):
    before = access.store.status(access.document_id)
    result = access.service.recheck(access.run_id, request(access, pages=[3], force=True))
    assert result["queued_pages"] == []
    assert result["skipped"] == [{"page": 3, "reason": "content_not_approved"}]
    assert access.store.status(access.document_id) == before


def test_paused_source_refuses_recheck_without_changing_pause_or_page_records(access):
    access.store.pause_document(access.document_id)
    before = access.store.status(access.document_id)
    with pytest.raises(ServiceError) as error:
        access.service.recheck(access.run_id, request(access, pages=[1], force=True))
    assert error.value.status_code == 409 and "paused" in error.value.detail
    assert access.store.status(access.document_id) == before
    assert before["run_request"]["pause_requested"] is True


@pytest.fixture
def client(access, monkeypatch):
    app = FastAPI()
    app.include_router(runs.router)
    app.dependency_overrides[get_engine] = lambda: access.engine
    app.dependency_overrides[get_settings] = lambda: SimpleNamespace()
    monkeypatch.setattr(module, "SourceStructure", lambda *_: access.service)
    with TestClient(app) as connection:
        yield connection


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_structure", {"fragments": []}),
        ("human_approval", True),
        ("worker_id", "pretend-human"),
        ("schema_version", 2),
    ],
)
def test_api_recheck_rejects_unadvertised_fields_before_entering_service(
    access, client, field, value
):
    before = deepcopy(access.store.status(access.document_id))
    response = client.post(
        f"/api/v2/runs/{access.run_id}/source-structure/recheck",
        json={
            "expected_snapshot_sha256": "0" * 64,
            "pages": [1],
            "force": True,
            field: value,
        },
    )
    assert response.status_code == 422
    assert any(error["type"] == "extra_forbidden" for error in response.json()["detail"])
    access.native.assert_not_called()
    assert access.store.status(access.document_id) == before


@pytest.mark.parametrize("page", ["0", "-1", "1.5"])
def test_api_page_query_rejects_invalid_physical_pages_before_native_resolution(
    access, client, page
):
    response = client.get(f"/api/v2/runs/{access.run_id}/source-structure", params={"page": page})
    assert response.status_code == 422
    access.native.assert_not_called()


def test_api_page_and_recheck_preserve_the_public_contract(access, client):
    response = client.get(f"/api/v2/runs/{access.run_id}/source-structure", params={"page": 1})
    assert response.status_code == 200
    page = response.json()
    assert page["fragments"][0]["text"] == access.text[:400]
    response = client.post(
        f"/api/v2/runs/{access.run_id}/source-structure/recheck",
        json={
            "expected_snapshot_sha256": page["snapshot_sha256"],
            "pages": [1],
            "force": True,
        },
    )
    assert response.status_code == 200
    assert response.json()["queued_pages"] == [1]
    assert response.json()["content_approval"] is response.json()["human_approval"] is False


@pytest.mark.parametrize(
    "field,value", [("pages", [True]), ("pages", ["1"]), ("force", "false"), ("force", 1)]
)
def test_api_recheck_does_not_coerce_json_into_physical_pages_or_force(
    access, client, field, value
):
    body = {"expected_snapshot_sha256": "a" * 64, "pages": [1], "force": False}
    body[field] = value
    response = client.post(f"/api/v2/runs/{access.run_id}/source-structure/recheck", json=body)
    assert response.status_code == 422
    access.native.assert_not_called()


def test_inspector_reports_changed_original_as_conflict_without_overwriting_evidence(access):
    before = access.store.status(access.document_id)
    image = access.store.artifact_path(before, before["pages"][0]["image"])
    image.write_bytes(image.read_bytes() + b"changed")
    with pytest.raises(ServiceError) as error:
        access.service.page(access.run_id, 1)
    assert error.value.status_code == 409
    assert access.store.status(access.document_id) == before


def test_inspector_reports_structure_queue_from_tasks_instead_of_snapshot_hash(access):
    before = access.service.page(access.run_id, 1)
    access.service.recheck(access.run_id, request(access, pages=[1], force=True))
    queued = access.service.page(access.run_id, 1)
    assert queued["snapshot_sha256"] == before["snapshot_sha256"]
    assert queued["pending_structure_pages"] == 1 and queued["structure_task_state"] == "ready"
    access.store.pause_document(access.document_id)
    assert access.service.page(access.run_id, 1)["processing_paused"] is True
