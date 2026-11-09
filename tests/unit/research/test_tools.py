"""MCP validates leases before source fetch and stores actual server search receipts."""

import json
from types import SimpleNamespace

import pytest

from hrs.codex.research_tools import register_research_tools, status_summary
from hrs.processing.errors import LeaseConflict
from hrs.processing.research_workflow import ResearchWorkflow
from hrs.processing.store import Store
from tests.unit.research.test_workflow import legacy_policy, read_all, snapshot, submit_draft


class Server:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def register(function):
            self.tools[function.__name__] = function
            return function

        return register


def test_large_task_book_mcp_status_retains_counts_without_returning_unbounded_metadata():
    tasks = [
        {
            "task_id": str(index),
            "stage": "author",
            "state": "ready",
            "source_index": "source" * 1000,
        }
        for index in range(2000)
    ]
    summary = status_summary(
        {
            "run_id": "run",
            "book_id": "book",
            "state": "running",
            "version": 1,
            "policy": {"version": 2, "models": {"author": "gpt-6.1-sol"}},
            "counts": {"ready": 2000},
            "tasks": tasks,
            "article_index": "index" * 10000,
        }
    )
    assert summary["counts"]["ready"] == summary["ready_by_stage"]["author"] == 2000
    assert summary["omitted_tasks"] == 2000 and "article_index" not in summary
    assert len(json.dumps(summary, ensure_ascii=False, indent=2)) < 2000


def test_dispatch_contract_waits_for_leased_generation_and_stays_alive_between_stages(tmp_path):
    from tests.unit.research.test_complete_workflow import draft, read, source

    workflow = ResearchWorkflow(Store(tmp_path / "native"))
    run = workflow.enqueue(source())["run_id"]
    assert status_summary(workflow.status(run))["dispatch_action"] == "dispatch"
    packet = workflow.claim(run, "author", stage="author")
    waiting = status_summary(workflow.status(run))
    assert not any(waiting["ready_by_stage"].values())
    assert waiting["active"]["count"] == 1 and waiting["dispatch_action"] == "wait"
    read(workflow, packet)
    workflow.submit(
        packet["task_token"], "author", {"input_hash": packet["input_hash"], "cards": [draft()]}
    )
    continuation = status_summary(workflow.status(run))
    assert continuation["counts"]["completed"] == 1
    assert continuation["ready_by_stage"]["verify"] == 1
    assert continuation["dispatch_action"] == "dispatch"
    workflow.pause(run)
    assert status_summary(workflow.status(run))["dispatch_action"] == "stop"
    workflow.resume(run)
    assert status_summary(workflow.status(run))["dispatch_action"] == "dispatch"
    workflow.claim(run, "verifier", stage="verify")
    assert status_summary(workflow.status(run))["dispatch_action"] == "wait"


def test_live_lease_precedes_external_fetch_and_search_audit_is_server_owned(tmp_path, monkeypatch):

    store = Store(tmp_path / "native")
    workflow = ResearchWorkflow(store)
    # Preserve the original v4 lease/transport contract; v5 extraction coverage
    # and independent inventory are exercised in the complete-workflow suite.
    run = workflow.enqueue(snapshot(), _policy=legacy_policy(4))
    submit_draft(workflow, run["run_id"], "author", "author")
    packet = workflow.claim(run["run_id"], "enricher", stage="enrich")
    calls = []

    class Evidence:
        def search(self, query, book_id, **kwargs):
            calls.append(("search", query, kwargs))
            return {
                "query": query,
                "backend": "hybrid",
                "available": True,
                "hits": [],
                "metrics": {"lexical_ms": 3},
            }

        def freeze_source(self, article_id):
            calls.append(("freeze", article_id))
            return snapshot("external-book")

        def entities(self, *args, **kwargs):
            return [{"id": "sql-visible-entity"}]

    monkeypatch.setattr("hrs.config.Settings.load", lambda: SimpleNamespace(codex_root=store.root))
    monkeypatch.setattr("hrs.storage.db.engine_for", lambda _: object())
    monkeypatch.setattr("hrs.research.sources.ResearchEvidence", lambda *args: Evidence())
    server = Server()
    register_research_tools(server, store, lambda function: function)
    with pytest.raises(ValueError, match="concrete"):
        server.tools["research_fail"](packet["task_token"], "enricher", "tool_error", {})
    assert workflow.status(run["run_id"])["counts"]["leased"] == 1
    with pytest.raises(LeaseConflict):
        server.tools["research_read_evidence"]("missing-token", "external-article")
    assert not calls
    result = server.tools["research_search"](packet["task_token"], "張三", limit=3)
    assert result["backend"] == "hybrid" and calls[0][2]["semantic"] is True
    source = server.tools["research_read_evidence"](packet["task_token"], "external-article")
    assert source["article_id"].startswith("source-")
    assert server.tools["research_entities"]("張三") == [{"id": "sql-visible-entity"}]
    with workflow.ledger.connect() as connection:
        audit = json.loads(
            connection.execute(
                "SELECT payload FROM research_events WHERE event='research_search'"
            ).fetchone()[0]
        )
    assert (
        audit["query"] == "張三" and audit["limit"] == 3 and audit["metrics"] == {"lexical_ms": 3}
    )
    read_all(workflow, packet)
    workflow.submit(
        packet["task_token"],
        "enricher",
        {"input_hash": packet["input_hash"], "cards": packet["cards"]},
    )
    verifier = workflow.claim(run["run_id"], "verifier", stage="verify")
    calls_before = list(calls)
    with pytest.raises(ValueError, match="Only enrichment"):
        server.tools["research_search"](verifier["task_token"], "不應發出真實檢索")
    assert calls == calls_before
    assert store.runtime_control.snapshot()["state"] == "normal"
