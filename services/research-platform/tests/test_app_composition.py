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
from hrs_platform.domain.errors import Problem


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
                    n == "hrs_platform.main" or n.startswith(("hrs_platform.api", "hrs_platform.jobs"))
                    for n in names
                ), f"{path}:{getattr(node, 'lineno', 0)} reverses application layering"
