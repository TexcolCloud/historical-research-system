"""Failing projection/submission writes must leave replayable, unchanged ledgers."""

import sqlite3
from copy import deepcopy

import pytest

from hrs.processing.errors import EvidenceMismatch, LeaseConflict
from hrs.processing.research_workflow import ResearchWorkflow
from hrs.processing.store import Store
from hrs.research.card_validation import require_checked_ids
from hrs.research.catalogue import ResearchCatalogue
from tests.unit.research.test_catalogue import card
from tests.unit.research.test_workflow import draft, legacy_policy, read_all, snapshot


def ledger_rows(ledger, prefix):
    with ledger.connect() as connection:
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE ? ORDER BY name",
            (prefix + "%",),
        ).fetchall()
        return {
            name: connection.execute(f'SELECT * FROM "{name}" ORDER BY rowid').fetchall()
            for (name,) in tables
        }


@pytest.mark.parametrize(
    "checked,required,valid",
    [
        ([], [], True),
        (["a"], ["a"], True),
        (["b", "a"], ["a", "b"], True),
        ([], ["a"], False),
        (["a", "a"], {"a"}, False),
        (["a", "a"], ["a", "b"], False),
        (["other"], ["a"], False),
        (["a", "extra"], ["a"], False),
    ],
)
def test_verification_ids_keep_exact_frozen_coverage_and_context(checked, required, valid):
    message = "Independent check is incomplete: source inventory"
    if valid:
        require_checked_ids(checked, required, message)
    else:
        with pytest.raises(EvidenceMismatch, match=message):
            require_checked_ids(checked, required, message)


@pytest.mark.parametrize("table", ["documents", "members", "entities", "edges"])
def test_catalogue_failure_rolls_back_all_projections_and_can_retry(tmp_path, table):
    ledger = Store(tmp_path / "native").checkpoints.ledger
    catalogue = ResearchCatalogue(ledger)
    first = card()
    catalogue.record(first)
    before = ledger_rows(ledger, "research_catalogue_")
    revised = {**deepcopy(first), "version": 2, "created_at": 2}
    revised["mentions"][0]["aliases"] = [{"name": "别名", "evidence_ids": ["e1"]}]
    with ledger.connect(write=True) as connection:
        connection.execute(
            f"CREATE TRIGGER reject_projection BEFORE INSERT ON research_catalogue_{table} "
            "BEGIN SELECT RAISE(ABORT,'injected projection failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="injected projection failure"):
        catalogue.record(revised)
    assert ledger_rows(ledger, "research_catalogue_") == before
    with ledger.connect(write=True) as connection:
        connection.execute("DROP TRIGGER reject_projection")
    catalogue.record(revised)
    committed = ledger_rows(ledger, "research_catalogue_")
    catalogue.record(revised)
    assert ledger_rows(ledger, "research_catalogue_") == committed
    assert catalogue.entities()["items"][0]["source_mentions"][0]["card_version"] == 2


def test_catalogue_respects_the_callers_transaction(tmp_path):
    ledger = Store(tmp_path / "native").checkpoints.ledger
    catalogue = ResearchCatalogue(ledger)
    catalogue.record(card())
    before = ledger_rows(ledger, "research_catalogue_")
    with pytest.raises(RuntimeError, match="outer operation failed"):
        with ledger.connect(write=True) as connection:
            catalogue.record(card("second", "book-b"), connection=connection)
            raise RuntimeError("outer operation failed")
    assert ledger_rows(ledger, "research_catalogue_") == before


def test_submission_failure_preserves_lease_and_same_token_retry_is_idempotent(tmp_path):
    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    run = workflow.enqueue(snapshot(), _policy=legacy_policy(4))
    packet = workflow.claim(run["run_id"], "synthetic-author", stage="author")
    read_all(workflow, packet)
    result = {"input_hash": packet["input_hash"], "cards": [draft()]}
    before = ledger_rows(workflow.ledger, "research_")
    with workflow.ledger.connect(write=True) as connection:
        connection.execute(
            "CREATE TRIGGER reject_submission BEFORE INSERT ON research_submissions "
            "BEGIN SELECT RAISE(ABORT,'injected submission failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="injected submission failure"):
        workflow.submit(packet["task_token"], "synthetic-author", result)
    assert ledger_rows(workflow.ledger, "research_") == before
    with workflow.ledger.connect(write=True) as connection:
        connection.execute("DROP TRIGGER reject_submission")
    committed = workflow.submit(packet["task_token"], "synthetic-author", result)
    after = ledger_rows(workflow.ledger, "research_")
    assert workflow.submit(packet["task_token"], "synthetic-author", result) == committed
    assert ledger_rows(workflow.ledger, "research_") == after
    with pytest.raises(LeaseConflict, match="cannot be replaced"):
        workflow.submit(packet["task_token"], "synthetic-author", {**result, "cards": []})
    assert ledger_rows(workflow.ledger, "research_") == after
