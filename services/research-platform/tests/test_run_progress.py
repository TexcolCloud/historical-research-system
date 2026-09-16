from fastapi.testclient import TestClient
from sqlalchemy import select, update

from hrs_platform import models as db
from hrs_platform.services.lifecycle import RunLifecycle
from hrs_platform.main import create_app
from test_review import seed


def test_page_progress_is_observational_and_does_not_release_content(platform):
    settings, engine = platform
    run, _ = seed(engine)
    with engine.begin() as connection:
        connection.execute(
            update(db.runs).where(db.runs.c.id == run).values(state="processing", stage="vision")
        )
    activities = RunLifecycle(engine)
    activities.report_progress(run, 12, 481)
    activities.report_progress(run, 12, 481)
    with engine.connect() as connection:
        row = connection.execute(select(db.runs).where(db.runs.c.id == run)).mappings().one()
        events = list(connection.execute(select(db.events).where(db.events.c.run_id == run)).mappings())
    assert row["revision"] == 0 and row["pending_count"] == 2
    assert row["state"] == "processing" and row["result"]["review_initialized"]
    assert len(events) == 1 and events[0]["kind"] == "run.progress"
    with TestClient(create_app(settings, engine)) as client:
        response = client.get(f"/api/v2/books/{row['book_id']}/runs")
    assert response.json()[0]["progress"] == {"stage": "vision", "completed": 12, "total": 481}
    # A delayed progress report cannot change a finished stage or open the review gate.
    with engine.begin() as connection:
        connection.execute(
            update(db.runs).where(db.runs.c.id == run).values(state="awaiting_review", stage="review")
        )
    activities.report_progress(run, 481, 481)
    with engine.connect() as connection:
        row = connection.execute(select(db.runs).where(db.runs.c.id == run)).mappings().one()
    assert row["result"]["progress"]["completed"] == 12
    assert row["pending_count"] == 2
