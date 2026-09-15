import pytest
from temporalio.exceptions import ApplicationError
from test_review import seed

from hrs_platform.outputs import Outputs, execution_parent


def test_total_request_budget_is_not_a_local_output_error(platform):
    settings, engine = platform
    run, _ = seed(engine)
    outputs = Outputs(settings, engine)
    with pytest.raises(ApplicationError) as failure:
        outputs.reserve_request(run, "step:http-request:1:1", {"synthetic": True}, 0)
    assert failure.value.type == "model_request_budget" and failure.value.non_retryable
    assert outputs.get(run, "step:http-request:1:1") is None


def test_immutable_evidence_conflict_is_not_a_model_validation_failure(platform):
    settings, engine = platform
    run, _ = seed(engine)
    outputs = Outputs(settings, engine)
    outputs.put(run, "fixed", {"value": "original"}, {"input": "fixed"})
    with pytest.raises(RuntimeError, match="conflicts"):
        outputs.put(run, "fixed", {"value": "changed"}, {"input": "fixed"})
    assert outputs.get(run, "fixed") == {"value": "original"}


def test_failed_research_branch_stops_showing_running_and_restores_parent(platform):
    settings, engine = platform
    run, _ = seed(engine)
    outputs = Outputs(settings, engine)
    parent = outputs.node(run, "main", kind="agent", label="Main", objective="Plan")
    with pytest.raises(ValueError, match="provider unavailable"):
        with outputs.operation(run, "group:0", "Research", kind="group", parent=parent) as branch:
            assert execution_parent.get() == branch
            raise ValueError("provider unavailable")
    node = next(row for row in outputs.list_nodes(run) if row["kind"] == "group")
    assert node["state"] == "failed" and node["finished_at"] is not None
    assert node["parent_id"] == parent
    assert execution_parent.get() is None


def test_terminal_run_closes_abandoned_nodes_without_rewriting_completed_work(platform):
    from hrs_platform.pipeline_activities import PipelineActivities

    settings, engine = platform
    run, _ = seed(engine)
    outputs = Outputs(settings, engine)
    outputs.node(run, "done", kind="agent", label="Done", objective="Read", state="completed")
    outputs.node(run, "abandoned", kind="agent", label="Interrupted", objective="Read")
    outputs.node(run, "waiting", kind="agent", label="Waiting", objective="Read", state="waiting")
    PipelineActivities(settings, engine).record_pipeline_failure(run)
    nodes = {row["label"]: row for row in outputs.list_nodes(run)}
    assert nodes["Done"]["state"] == "completed"
    assert nodes["Interrupted"]["state"] == "failed"
    assert nodes["Interrupted"]["finished_at"] is not None
    assert nodes["Waiting"]["state"] == "failed" and nodes["Waiting"]["finished_at"] is not None


def test_service_recovery_is_visible_and_cleared_when_activity_resumes(platform, monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from hrs_platform.activities import Activities
    from hrs_platform.books import get_run
    from hrs_platform.pipeline_activities import PipelineActivities

    settings, engine = platform
    run, _ = seed(engine)
    pipeline = PipelineActivities(settings, engine)
    monkeypatch.setattr(
        "hrs_platform.pipeline_activities.activity.info",
        lambda: SimpleNamespace(activity_type="generate_cards"),
    )
    monkeypatch.setattr("hrs_platform.pipeline_activities.activity.heartbeat", lambda *_: None)

    async def offline():
        raise ApplicationError(
            "waiting", {"retry_at": 1060.0}, type="model_transport_wait", non_retryable=True
        )

    with pytest.raises(ApplicationError):
        asyncio.run(pipeline.observe(run, offline()))
    saved = get_run(engine, run)
    assert saved["state"] == "processing" and saved["error"]["code"] == "model_transport_wait"
    assert Outputs(settings, engine).list_nodes(run)[0]["state"] == "waiting"

    async def done():
        return {"complete": True}

    assert asyncio.run(pipeline.observe(run, done())) == {"complete": True}
    assert get_run(engine, run)["error"] is None
    assert Outputs(settings, engine).list_nodes(run)[0]["state"] == "completed"
    Activities(settings, engine).transition(run, "processing", "planning", error=saved["error"])
    pipeline.record_pipeline_failure(run)
    stopped = get_run(engine, run)
    assert stopped["state"] == "failed" and stopped["error"]["code"] == "model_transport_exhausted"
