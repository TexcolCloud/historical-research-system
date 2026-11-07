import time
from copy import deepcopy

import pytest

from hrs.processing.workflow_rules import metadata_work_remaining, request_continuation


def document(*, mode="verify_links", state="ready", published=True):
    return {
        "id": "synthetic",
        "state": "published" if published else "processing",
        "pages": [],
        "tasks": [
            {
                "stage": "navigation",
                "state": state,
                "verification_inputs": {"navigation_mode": mode},
            }
        ],
        "publication": {"markdown_sha256": "retained-publication"},
    }


@pytest.mark.parametrize(
    "state,remaining",
    [
        ("ready", True),
        ("leased", True),
        ("completed", False),
        ("pending", False),
        ("superseded", False),
    ],
)
def test_metadata_remaining_is_bound_to_automatic_link_work(state, remaining):
    doc = document(state=state)
    before = deepcopy(doc)
    assert metadata_work_remaining(doc) is remaining
    assert doc == before
    doc["tasks"][0]["verification_inputs"]["navigation_mode"] = "parse_toc"
    assert not metadata_work_remaining(doc)


def test_published_metadata_continuation_keeps_the_active_lease_and_publication():
    doc = document()
    publication = deepcopy(doc["publication"])
    doc["run_request"] = {
        "state": "running",
        "token": "synthetic-lease",
        "history": [],
        "lease_expires_at": time.time() + 900,
    }
    request = request_continuation(doc, followup=True)
    assert request["state"] == "running"
    assert request["token"] == "synthetic-lease"
    assert request["continuation_requested"]
    assert doc["publication"] == publication
    assert doc["state"] == "published"


@pytest.mark.parametrize(
    "flag,state", [("pause_requested", "paused"), ("cancel_requested", "cancelled")]
)
def test_stop_requests_take_precedence_over_published_metadata(flag, state):
    doc = document()
    doc["run_request"] = {"state": state, flag: True, "history": []}
    assert request_continuation(doc)["state"] == state
    assert doc["run_request"][flag]


def test_published_without_automatic_metadata_retains_completed_behavior():
    doc = document(state="completed")
    assert request_continuation(doc)["state"] == "succeeded"


def test_metadata_request_does_not_grant_extra_technical_budget():
    doc = document()
    doc["run_request"] = {
        "state": "retryable_error",
        "technical_failures": 3,
        "retry_limit": 3,
        "history": [],
        "automatic_checkpoint_used": True,
    }
    request = request_continuation(doc)
    assert request["state"] == "retryable_error"
    assert request["technical_failures"] == request["retry_limit"] == 3
    assert request["automatic_checkpoint_used"]
