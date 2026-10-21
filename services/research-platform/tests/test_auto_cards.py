from types import SimpleNamespace
from unittest.mock import patch

import pytest

from hrs_platform.jobs.pipeline import PipelineActivities


@pytest.mark.parametrize("enabled", [True, False])
def test_retired_cards_cannot_be_enabled_by_legacy_configuration(enabled):
    pipeline = PipelineActivities(SimpleNamespace(auto_cards_enabled=enabled), None)
    with patch("hrs_platform.jobs.pipeline.Cards") as cards:
        result = pipeline.create_card_run("book-run")
        cards.assert_not_called()
        assert result == {"run_id": "book-run", "skipped": True, "reason": "cards_retired"}


def test_book_pipeline_finishes_after_indexing_without_card_child(monkeypatch):
    import asyncio

    from hrs_platform.jobs import workflows

    executed = []

    async def step(name, run_id, **options):
        executed.append(name)
        if name == "initialize_review":
            return {"pending_count": 0, "revision": 1}
        return {"run_id": run_id, "state": "completed"}

    monkeypatch.setattr(workflows, "execute_with_service_recovery", step)
    monkeypatch.setattr(workflows.workflow, "info", lambda: SimpleNamespace(task_queue="test"))
    result = asyncio.run(workflows.BookWorkflow().run({"run_id": "book-run", "gpu_queue": "gpu"}))
    assert result["state"] == "completed"
    assert executed == [
        "verify_upload", "convert_document", "initialize_review", "organize_book",
        "publish_book", "index_book", "finish_book",
    ]


def test_card_creation_is_absent_from_active_api():
    from fastapi import FastAPI

    from hrs_platform.api.main import api_router

    app = FastAPI()
    app.include_router(api_router)
    assert not any(path.endswith("/card-runs") for path in app.openapi()["paths"])


@pytest.mark.parametrize("kind,payload", [
    ("start_cards", {}), ("retry_run", {"run_kind": "cards"}),
])
def test_retired_card_outbox_events_are_acknowledged_without_starting_workflow(kind, payload):
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    from hrs_platform.jobs.worker import dispatch_event

    engine, client = MagicMock(), AsyncMock()
    event = {"id": 1, "kind": kind, "payload": payload, "run_id": "card-run"}
    asyncio.run(dispatch_event(engine, client, SimpleNamespace(), event))
    client.start_workflow.assert_not_awaited()
    statement = engine.begin.return_value.__enter__.return_value.execute.call_args.args[0]
    assert statement.compile().params["delivered"] is True


def test_card_retry_api_returns_gone_without_creating_work(monkeypatch):
    from hrs_platform.api.routes import runs
    from hrs_platform.domain.errors import ServiceError

    monkeypatch.setattr(runs.books, "get_run", lambda *_: {"kind": "cards"})
    with pytest.raises(ServiceError) as error:
        runs.retry(None, "card-run", SimpleNamespace(request_id="request"))
    assert error.value.status_code == 410
