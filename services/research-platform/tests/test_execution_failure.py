import pytest
from test_review import seed

from hrs_platform.outputs import Outputs, execution_parent


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
    PipelineActivities(settings, engine).record_pipeline_failure(run)
    nodes = {row["label"]: row for row in outputs.list_nodes(run)}
    assert nodes["Done"]["state"] == "completed"
    assert nodes["Interrupted"]["state"] == "failed"
    assert nodes["Interrupted"]["finished_at"] is not None
