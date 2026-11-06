import pytest

from hrs.library.runs.outputs import Outputs

from .test_review import seed


def test_immutable_evidence_conflict_is_not_a_model_validation_failure(platform):
    settings, engine = platform
    run, _ = seed(engine, settings=settings)
    outputs = Outputs(settings, engine)
    outputs.put(run, "fixed", {"value": "original"}, {"input": "fixed"})
    with pytest.raises(RuntimeError, match="conflicts"):
        outputs.put(run, "fixed", {"value": "changed"}, {"input": "fixed"})
    assert outputs.get(run, "fixed") == {"value": "original"}


def test_execution_projection_reuses_unchanged_objects_and_emits_only_changes(
    platform, monkeypatch
):
    from sqlalchemy import func, select

    from hrs.storage import models as db

    settings, engine = platform
    run, _ = seed(engine, settings=settings)
    outputs = Outputs(settings, engine)
    rows = [
        dict(
            key=f"page:{i}",
            kind="agent",
            label="Review",
            objective=f"Page {i}",
            state="completed",
            details={"page": i},
        )
        for i in range(1000)
    ]
    outputs.nodes(run, rows)
    before = {r["id"]: r["finished_at"] for r in outputs.list_nodes(run)}
    writes = []
    put = outputs.objects.put_bytes
    monkeypatch.setattr(
        outputs.objects, "put_bytes", lambda *a, **kw: (writes.append(1), put(*a, **kw))[1]
    )
    with engine.connect() as connection:
        count = connection.scalar(select(func.count()).select_from(db.events))
    outputs.nodes(run, rows)
    assert not writes
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(db.events)) == count
    rows[510] = {**rows[510], "state": "failed", "details": {"page": 510, "error": "timeout"}}
    outputs.nodes(run, rows)
    assert len(writes) == 1
    after = outputs.list_nodes(run)
    assert sum(r["finished_at"] != before[r["id"]] for r in after) == 1
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(db.events)) == count + 1


def test_execution_window_has_true_counts_and_bounded_history(platform):
    from uuid import uuid4

    from sqlalchemy import insert

    from hrs.storage import models as db

    settings, engine = platform
    run, _ = seed(engine, settings=settings)
    with engine.begin() as connection:
        connection.execute(
            insert(db.execution_nodes),
            [
                dict(
                    id=str(uuid4()),
                    run_id=run,
                    kind="agent",
                    label="Review",
                    objective=f"Page {i}",
                    state="leased" if i == 999 else "completed",
                )
                for i in range(1000)
            ],
        )
    outputs = Outputs(settings, engine)
    current = outputs.window(run)
    assert current["total"] == 1000 and current["matching"] == 1
    assert len(current["records"]) == 1
    history = outputs.window(run, history=True, offset=40)
    assert len(history["records"]) == 40 and history["matching"] == 1000
    assert sum(s["count"] for s in history["stages"]) == 1000
