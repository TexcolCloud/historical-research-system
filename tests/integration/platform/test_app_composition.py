"""Application seams are testable without database, S3 or model requests."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from hrs.api import main
from hrs.api.deps import get_review, get_search
from hrs.config import Settings
from hrs.library.errors import Problem, ServiceError


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
    from hrs.library import processing_bridge

    monkeypatch.setattr(processing_bridge, "CodexBridge", Mock())
    owned, injected = SimpleNamespace(dispose=Mock()), SimpleNamespace(dispose=Mock())
    monkeypatch.setattr(main, "engine_for", lambda _: owned)
    with TestClient(main.create_app(settings)):
        pass
    owned.dispose.assert_called_once()
    with TestClient(main.create_app(settings, injected)):
        pass
    injected.dispose.assert_not_called()


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
