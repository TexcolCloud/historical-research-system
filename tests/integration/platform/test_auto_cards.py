from types import SimpleNamespace

import pytest


def test_card_creation_is_absent_from_active_api():
    from fastapi import FastAPI

    from hrs.api.router import api_router

    app = FastAPI()
    app.include_router(api_router)
    assert not any(path.endswith("/card-runs") for path in app.openapi()["paths"])


def test_card_retry_api_returns_gone_without_creating_work(monkeypatch):
    from hrs.api.routes import runs
    from hrs.library.errors import ServiceError

    monkeypatch.setattr(runs.books, "get_run", lambda *_: {"kind": "cards"})
    with pytest.raises(ServiceError) as error:
        runs.retry(None, "card-run", SimpleNamespace(request_id="request"))
    assert error.value.status_code == 410
