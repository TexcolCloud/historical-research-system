import pytest

from hrs_platform.outputs import Outputs, execution_parent
from test_review import seed


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
