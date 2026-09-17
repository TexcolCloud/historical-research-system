"""Application seams are testable without database, S3 or model requests."""

import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from hrs_platform import main
from hrs_platform.api.deps import get_review, get_search
from hrs_platform.core.config import Settings
from hrs_platform.domain.errors import Problem, ServiceError, TaskError
from hrs_platform.jobs.errors import activity_errors


@pytest.fixture
def settings(tmp_path):
    return Settings(
        database_url="postgresql+psycopg://test:test@127.0.0.1/test",
        s3_endpoint="http://127.0.0.1:1",
        s3_bucket="test",
        s3_access_key="test",
        s3_secret_key="test",
        project_root=tmp_path,
        cache_root=tmp_path / "compute",
    )


def test_request_dependencies_are_overrideable_and_isolated_between_apps(settings):
    first = main.create_app(settings, engine=SimpleNamespace())
    second = main.create_app(settings, engine=SimpleNamespace())
    list_issues = Mock(return_value=[])
    first.dependency_overrides[get_review] = lambda: SimpleNamespace(list=list_issues)
    with TestClient(first) as client:
        assert client.get("/api/v2/reviews").json() == []
        assert client.get("/api/v2/reviews/not-a-uuid").status_code == 422
        assert client.get("/health/live").json() == {"status": "ok"}
    list_issues.assert_called_once_with(None, True, 0, 100)
    assert second.dependency_overrides == {}
    assert first.state.review is not second.state.review


def test_search_dependency_preserves_headers_and_recoverable_error_contract(settings):
    app = main.create_app(settings, engine=SimpleNamespace())

    def search(*args, **kwargs):
        kwargs["metrics"].update(search_ms=12.5, evidence_status="adequate")
        return []

    app.dependency_overrides[get_search] = lambda: SimpleNamespace(search=search)
    with TestClient(app) as client:
        response = client.get("/api/v2/search", params={"q": "合成问题"})
        assert response.status_code == 200 and response.json() == []
        assert response.headers["Server-Timing"] == "search;dur=12.500"
        assert response.headers["X-Retrieval-Evidence"] == "adequate"

        def failed(*args, **kwargs):
            raise Problem("retrieval_wait", "synthetic unavailable", status=503, retryable=True)

        app.dependency_overrides[get_search] = lambda: SimpleNamespace(search=failed)
        response = client.get("/api/v2/search", params={"q": "合成问题"})
        assert response.status_code == 503
        assert response.json() == {
            "detail": "synthetic unavailable",
            "code": "retrieval_wait",
            "retryable": True,
        }


def test_only_application_owned_engine_is_disposed(settings, monkeypatch):
    owned, injected = SimpleNamespace(dispose=Mock()), SimpleNamespace(dispose=Mock())
    monkeypatch.setattr(main, "engine_for", lambda _: owned)
    with TestClient(main.create_app(settings)):
        pass
    owned.dispose.assert_called_once()
    with TestClient(main.create_app(settings, injected)):
        pass
    injected.dispose.assert_not_called()


def test_business_and_core_modules_do_not_depend_on_http_or_worker_composition():
    package = Path(main.__file__).parent
    for folder in ("services", "core", "domain"):
        for path in (package / folder).rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text("utf-8"))):
                names = (
                    [node.module or ""]
                    if isinstance(node, ast.ImportFrom)
                    else [a.name for a in node.names]
                    if isinstance(node, ast.Import)
                    else []
                )
                assert not any(
                    n == "hrs_platform.main" or n.startswith((
                        "hrs_platform.api", "hrs_platform.jobs", "fastapi", "starlette", "temporalio",
                    ))
                    for n in names
                ), f"{path}:{getattr(node, 'lineno', 0)} reverses application layering"
                if folder == "domain":
                    assert not any(n.startswith(("hrs_platform.services", "torch", "transformers")) for n in names), str(path)
                if path.parent.name == "models":
                    assert not any(n.startswith("hrs_platform.services.cards") for n in names), str(path)
                if path.name == "indexing.py":
                    assert "hrs_platform.services.retrieval.search" not in names, str(path)


@pytest.mark.parametrize("status", [404, 409, 422, 502])
def test_domain_rejections_keep_the_existing_http_body(settings, status):
    app = main.create_app(settings, engine=SimpleNamespace())
    def rejected(*args):
        raise ServiceError(status, "synthetic rejection")
    app.dependency_overrides[get_review] = lambda: SimpleNamespace(list=rejected)
    with TestClient(app) as client:
        response = client.get("/api/v2/reviews")
    assert response.status_code == status
    assert response.json() == {"detail": "synthetic rejection"}


@pytest.mark.parametrize("asynchronous", [False, True])
def test_activity_boundary_retains_recovery_type_details_and_retry_policy(asynchronous):
    import asyncio
    from temporalio.exceptions import ApplicationError
    from hrs_platform.services.storage import storage_heartbeat

    receipt = {"retry_at": 12345, "attempt": 8}
    def rejected():
        raise TaskError("wait", receipt, type="model_transport_wait", non_retryable=True)
    async def async_rejected():
        rejected()
    previous = storage_heartbeat.get()
    with pytest.raises(ApplicationError) as failure:
        if asynchronous:
            asyncio.run(activity_errors(async_rejected)())
        else:
            activity_errors(rejected)()
    assert failure.value.type == "model_transport_wait"
    assert failure.value.details == (receipt,)
    assert failure.value.non_retryable is True
    assert failure.value.message == "wait"
    assert isinstance(failure.value.__cause__, TaskError)
    assert storage_heartbeat.get() is previous
