"""Readiness describes SQL and S3 availability without exposing connection details."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from hrs.api.deps import get_engine, get_settings
from hrs.api.routes import health
from hrs.storage.objects import ObjectStorageError


@pytest.fixture
def dependencies(monkeypatch):
    engine, objects = MagicMock(), MagicMock()
    settings = SimpleNamespace(retrieval_device="cpu")
    app = FastAPI()
    app.include_router(health.router)
    app.dependency_overrides[get_engine] = lambda: engine
    app.dependency_overrides[get_settings] = lambda: settings
    monkeypatch.setattr(health, "objects_for", lambda selected: objects, raising=False)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, engine, objects


def test_ready_requires_both_sql_and_s3(dependencies):
    client, engine, objects = dependencies

    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
    engine.connect.return_value.__enter__.return_value.execute.assert_called_once()
    objects.check.assert_called_once_with()


@pytest.mark.parametrize(
    "error",
    [
        EndpointConnectionError(
            endpoint_url="http://private-user:private-password@private-host:8333"
        ),
        ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "private-password at private-host"}},
            "HeadBucket",
        ),
        ObjectStorageError("private-password at private-host"),
    ],
)
def test_unavailable_s3_is_not_reported_ready_or_disclosed(dependencies, error):
    client, _, objects = dependencies
    objects.check.side_effect = error

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert "private-host" not in response.text
    assert "private-password" not in response.text


def test_unavailable_sql_is_not_reported_ready_or_disclosed(dependencies):
    client, engine, objects = dependencies
    engine.connect.side_effect = SQLAlchemyError("private-password at private-host")

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert "private-host" not in response.text
    assert "private-password" not in response.text
    objects.check.assert_not_called()


def test_live_remains_independent_of_storage(dependencies):
    client, engine, objects = dependencies
    engine.connect.side_effect = SQLAlchemyError("unavailable")
    objects.check.side_effect = ObjectStorageError("unavailable")

    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    engine.connect.assert_not_called()
    objects.check.assert_not_called()


@pytest.mark.parametrize(
    "failure",
    [
        OSError("private-host"),
        RuntimeError("private-password"),
        ValueError("different models"),
        None,
    ],
)
def test_required_gpu_down_is_not_ready_or_disclosed(dependencies, monkeypatch, failure):
    client, _, _ = dependencies
    client.app.dependency_overrides[get_settings]().retrieval_device = "cuda"

    def unavailable(settings):
        if failure is not None:
            raise failure
        return {"gpu_available": False}

    monkeypatch.setattr(health, "configured_broker_health", unavailable)
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert "private-host" not in response.text and "private-password" not in response.text
    assert client.get("/health/live").json() == {"status": "ok"}


def test_gpu_ready_preserves_response_shape(dependencies, monkeypatch):
    client, _, _ = dependencies
    client.app.dependency_overrides[get_settings]().retrieval_device = "cuda"
    monkeypatch.setattr(
        health, "configured_broker_health", lambda settings: {"gpu_available": True}
    )
    response = client.get("/health/ready")
    assert response.status_code == 200 and response.json() == {"status": "ready"}


def test_cpu_readiness_does_not_contact_a_gpu_host(dependencies, monkeypatch):
    client, _, _ = dependencies
    monkeypatch.setattr(
        health, "configured_broker_health", lambda settings: pytest.fail("GPU call")
    )
    assert client.get("/health/ready").status_code == 200
