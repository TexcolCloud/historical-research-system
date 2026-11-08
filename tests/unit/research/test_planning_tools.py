"""Native planning tools preserve worker identity and server-validated scope."""

from types import SimpleNamespace

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import ValidationError

from hrs.codex.research_tools import register_research_tools, rejection_diagnostics, status_summary
from hrs.processing.store import Store
from tests.unit.research.test_tools import Server


def test_native_graph_uses_current_full_provenance_before_bounding_discovery(tmp_path, monkeypatch):
    calls = []
    store = Store(tmp_path / "native")
    settings = SimpleNamespace(codex_root=store.root)
    engine = object()
    monkeypatch.setattr("hrs.config.Settings.load", lambda: settings)
    monkeypatch.setattr("hrs.storage.db.engine_for", lambda _: engine)
    monkeypatch.setattr(
        "hrs.research.sources.ResearchEvidence",
        lambda *_: SimpleNamespace(
            settings=settings,
            engine=engine,
            require_entity=lambda identity: calls.append(("entity", identity)),
        ),
    )
    monkeypatch.setattr(
        "hrs.codex.research_tools.ResearchWorkflow",
        lambda _: SimpleNamespace(
            read_source=lambda *args: calls.append(("lease", args)),
            related=lambda *args, **kwargs: (
                calls.append(("stage", args, kwargs)) or {"edges": ["obsolete-preview"]}
            ),
        ),
    )

    def graph(identity, **options):
        calls.append(("current-full-graph", identity, options))
        return {
            "entity_id": identity,
            "hops": 1,
            "limit": 8,
            "truncated": False,
            "nodes": [{"id": identity, "kind": "person", "name": "甲"}],
            "edges": [],
        }

    monkeypatch.setattr(
        "hrs.research.api.Research",
        lambda actual_settings, actual_engine: (
            SimpleNamespace(
                workspace=SimpleNamespace(graph=graph),
            )
            if actual_settings is settings and actual_engine is engine
            else pytest.fail("wrong workspace")
        ),
    )
    server = Server()
    register_research_tools(server, store, lambda function: function)
    result = server.tools["research_related"]("native-lease", "canonical-entity", limit=20)
    assert [call[0] for call in calls] == ["lease", "entity", "stage", "current-full-graph"]
    assert result["edges"] == [] and result["metadata_only"] is True
    assert result["requested_limit"] == 20 and calls[-1][2] == {"hops": 1, "limit": 8}


def test_research_plan_is_persisted_by_workflow_without_platform_inference(monkeypatch):
    calls = []
    workflow = SimpleNamespace(record_plan=lambda *args: calls.append(args) or {"version": 1})
    monkeypatch.setattr("hrs.codex.research_tools.ResearchWorkflow", lambda _: workflow)
    server = Server()
    register_research_tools(server, object(), lambda function: function)
    questions = [{"id": "q1", "question": "是否有复函", "proposed_queries": ["复函 拨款"]}]
    result = server.tools["research_plan"]("task", "native-worker", questions)
    assert result == {"version": 1}
    assert calls == [("task", "native-worker", questions)]


def test_quote_locations_use_only_the_native_frozen_source_contract(monkeypatch):
    calls = []

    def locate(*args, **kwargs):
        calls.append((args, kwargs))
        return {"matches": [{"quote_index": 0, "start": 9, "end": 12}]}

    monkeypatch.setattr(
        "hrs.codex.research_tools.ResearchWorkflow",
        lambda _: SimpleNamespace(locate_quotes=locate),
    )
    server = Server()
    register_research_tools(server, object(), lambda function: function)
    result = server.tools["research_locate_quotes"](
        "task",
        "native-worker",
        ["原文甲"],
        article_id="frozen-source",
        offset=0,
        limit=10,
    )
    assert result["matches"][0]["start"] == 9
    assert calls == [
        (
            ("task", "native-worker", ["原文甲"]),
            {"article_id": "frozen-source", "offset": 0, "limit": 10},
        )
    ]


def test_many_missing_locations_do_not_hide_the_missing_plan_or_expose_input_values():
    import json

    error = ValidationError.from_exception_data(
        "NativeSubmission",
        [
            {
                "type": "missing",
                "loc": ("cards", 0, "mentions", index, "locations"),
                "input": {"private_input": "never include raw input in diagnostics"},
            }
            for index in range(100)
        ]
        + [{"type": "missing", "loc": ("cards", 0, "research_plan"), "input": {}}],
    )
    audit, reply = rejection_diagnostics(error)
    assert len(audit["issues"]) == reply["issue_count"] == 101
    assert len(reply["issues"]) == 2
    assert reply["issues"][0]["occurrences"] == 100
    assert reply["issues"][0]["indices"] == [[0, index] for index in range(100)]
    assert reply["issues"][0]["omitted_indices"] == 0
    assert reply["issues"][1]["path"] == ["cards", "*", "research_plan"]
    encoded = json.dumps(reply)
    assert len(encoded) < 5000 and "private_input" not in encoded


def test_rejected_native_proposal_is_retained_without_becoming_a_success(monkeypatch):
    import json

    from hrs.processing.errors import EvidenceMismatch

    rejected = []

    def submit(*args):
        raise EvidenceMismatch("Exact quotation does not match its frozen source")

    def retain(*args):
        rejected.append(args)
        return {"recorded": True, "rejection_ref": "rejected-proposal"}

    monkeypatch.setattr(
        "hrs.codex.research_tools.ResearchWorkflow",
        lambda _: SimpleNamespace(submit=submit, record_submission_rejection=retain),
    )
    server = Server()
    register_research_tools(server, object(), lambda function: function)
    candidate = {"input_hash": "frozen-input", "cards": []}
    with pytest.raises(ToolError) as failure:
        server.tools["research_submit"]("task", "native-worker", candidate)
    reply = json.loads(str(failure.value))
    assert reply["error"] == "submission_rejected"
    assert reply["rejection_ref"] == "rejected-proposal"
    assert rejected[0][:3] == ("task", "native-worker", candidate)
    assert "approval" not in reply


def test_task_scope_is_applied_before_one_real_hybrid_request(tmp_path, monkeypatch):
    store = Store(tmp_path / "native")
    searches, audits = [], []
    workflow = SimpleNamespace(
        prepare_search=lambda token, query=None: {"book_ids": ["book-1", "book-2"]},
        record_search=lambda *args, **kwargs: audits.append(kwargs) or {"receipt_id": "search-9"},
    )

    def search(query, book_id, **options):
        searches.append((query, book_id, options))
        return {"query": query, "backend": "hybrid", "available": True, "hits": [], "metrics": {}}

    monkeypatch.setattr("hrs.codex.research_tools.ResearchWorkflow", lambda _: workflow)
    monkeypatch.setattr("hrs.config.Settings.load", lambda: SimpleNamespace(codex_root=store.root))
    monkeypatch.setattr("hrs.storage.db.engine_for", lambda _: object())
    monkeypatch.setattr(
        "hrs.research.sources.ResearchEvidence", lambda *args: SimpleNamespace(search=search)
    )
    server = Server()
    register_research_tools(server, store, lambda function: function)
    with pytest.raises(ValueError, match="scope"):
        server.tools["research_search"]("task", "拨款复函", book_id="other-book")
    assert not searches
    response = server.tools["research_search"]("task", "拨款复函")
    assert response["receipt_id"] == "search-9"
    assert len(searches) == 1
    assert searches[0][2]["book_ids"] == ["book-1", "book-2"]
    assert searches[0][2]["trusted_scope"] is True
    assert audits[0]["book_ids"] == ["book-1", "book-2"]


def test_status_dispatches_bounded_repair_and_independent_reverification():
    summary = status_summary(
        {
            "run_id": "run",
            "book_id": "book",
            "state": "running",
            "version": 2,
            "policy": {"version": 5},
            "counts": {"ready": 2},
            "tasks": [
                {"task_id": "repair", "stage": "repair", "state": "ready"},
                {"task_id": "second-review", "stage": "reverify", "state": "ready"},
            ],
        }
    )
    assert summary["ready_by_stage"]["repair"] == 1
    assert summary["ready_by_stage"]["reverify"] == 1
    assert summary["omitted_tasks"] == 2


def test_counterpart_authorization_is_deferred_to_the_source_first_workflow(tmp_path, monkeypatch):
    store = Store(tmp_path / "native")
    authorizations, frozen = [], []

    def target(token, worker, card, version=None, *, authorize=None):
        authorize({"id": card, "version": version})
        frozen.append((token, worker, card, version))
        return {"card_ref": "frozen-target", "total": 1234}

    workflow = SimpleNamespace(
        target_card=target, target_card_text=lambda *args: {"text": "source"}
    )
    monkeypatch.setattr("hrs.codex.research_tools.ResearchWorkflow", lambda _: workflow)
    monkeypatch.setattr("hrs.config.Settings.load", lambda: SimpleNamespace(codex_root=store.root))
    monkeypatch.setattr("hrs.storage.db.engine_for", lambda _: object())
    monkeypatch.setattr(
        "hrs.research.sources.ResearchEvidence",
        lambda *args: SimpleNamespace(require_card=lambda *values: authorizations.append(values)),
    )
    server = Server()
    register_research_tools(server, store, lambda function: function)
    result = server.tools["research_target_card"]("token", "native-worker", "card-2", version=2)
    assert result["card_ref"] == "frozen-target"
    assert authorizations == [("card-2", 2)]
    assert frozen == [("token", "native-worker", "card-2", 2)]
    assert server.tools["research_target_card_text"]("token", "native-worker", "frozen-target") == {
        "text": "source"
    }
