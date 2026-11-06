"""Minimal local configuration uses the same defaults as deployment and initialization."""

import json
import os

import pytest
from sqlalchemy.engine import make_url

from hrs.config import Settings, load_environment


@pytest.fixture(autouse=True)
def isolated_configuration(monkeypatch):
    for key in os.environ:
        if (
            key.startswith(("PLATFORM_", "INGEST_", "CARDS_", "DEEPSEEK_"))
            or key == "HRS_PROJECT_ROOT"
        ):
            monkeypatch.delenv(key)


def write_environment(root, **overrides):
    values = {
        "INGEST_DEV_DB_PASSWORD": "local-only-password",
        "INGEST_S3_ACCESS_KEY": "local-only-access",
        "INGEST_S3_SECRET_KEY": "local-only-secret",
        **overrides,
    }
    path = root / ".env"
    path.write_text(
        "".join(f"{key}={json.dumps(value)}\n" for key, value in values.items()), "utf-8"
    )
    return path


def test_three_credentials_load_without_mutating_configuration(tmp_path):
    path = write_environment(tmp_path)
    before_file, before_process = path.read_bytes(), dict(os.environ)

    settings = Settings.load(tmp_path)
    env = load_environment(tmp_path)

    assert settings.processing_backend == "codex"
    assert settings.s3_endpoint == "http://127.0.0.1:58333"
    assert settings.s3_bucket == "ingestion-acceptance"
    assert settings.s3_region == "us-east-1"
    assert make_url(settings.database_url.get_secret_value()).database == "historical_research_v2"
    assert make_url(env["PLATFORM_TEST_DATABASE_URL"]).database == "historical_research_v2_test"
    assert make_url(env["PLATFORM_DOCKER_DATABASE_URL"]).host == "host.docker.internal"
    assert path.read_bytes() == before_file
    assert dict(os.environ) == before_process


def test_database_password_is_encoded_once_for_every_derived_url(tmp_path):
    password = "p@ss:/#?% +$'\\end"
    write_environment(tmp_path, INGEST_DEV_DB_PASSWORD=password)

    env = load_environment(tmp_path)

    for key in (
        "INGEST_DATABASE_URL",
        "PLATFORM_DATABASE_URL",
        "PLATFORM_TEST_DATABASE_URL",
        "PLATFORM_DOCKER_DATABASE_URL",
    ):
        assert make_url(env[key]).password == password


def test_explicit_connections_and_process_overrides_take_precedence(tmp_path, monkeypatch):
    business = "postgresql+psycopg://restored:restore-pass@archive:55441/hrs_restore_example"
    test = "postgresql+psycopg://tester:test-pass@tests:55442/historical_research_v2_test"
    docker = "postgresql+psycopg://docker:docker-pass@container-db:5432/custom"
    write_environment(
        tmp_path,
        INGEST_DATABASE_URL="postgresql+psycopg://admin:admin-pass@custom-db:55440/ingestion_dev",
        PLATFORM_DATABASE_URL=business,
        PLATFORM_TEST_DATABASE_URL=test,
        PLATFORM_DOCKER_DATABASE_URL=docker,
        INGEST_S3_BUCKET="file-bucket",
    )
    monkeypatch.setenv("PLATFORM_S3_BUCKET", "process-bucket")
    monkeypatch.setenv("PLATFORM_DATABASE_URL", business + "_process")

    env = load_environment(tmp_path)
    settings = Settings.load(tmp_path)

    assert settings.database_url.get_secret_value() == business + "_process"
    assert settings.s3_bucket == "process-bucket"
    assert env["PLATFORM_TEST_DATABASE_URL"] == test
    assert env["PLATFORM_DOCKER_DATABASE_URL"] == docker
    assert make_url(env["INGEST_DATABASE_URL"]).database == "ingestion_dev"


def test_container_host_and_empty_derived_overrides_use_minimal_configuration(tmp_path):
    write_environment(
        tmp_path,
        INGEST_DATABASE_HOST="host.docker.internal",
        INGEST_DEV_DB_PORT="55440",
        PLATFORM_DATABASE_URL="",
        PLATFORM_TEST_DATABASE_URL="",
        PLATFORM_DOCKER_DATABASE_URL="",
    )

    env = load_environment(tmp_path)

    for key in (
        "PLATFORM_DATABASE_URL",
        "PLATFORM_TEST_DATABASE_URL",
        "PLATFORM_DOCKER_DATABASE_URL",
    ):
        url = make_url(env[key])
        assert url.host == "host.docker.internal"
        assert url.port == 55440


def test_invalid_admin_connection_does_not_echo_credentials(tmp_path):
    secret = "private-password-must-not-appear"
    write_environment(tmp_path, INGEST_DATABASE_URL=f"not-a-url:{secret}")

    with pytest.raises(ValueError) as caught:
        load_environment(tmp_path)

    assert secret not in str(caught.value)
    assert "PostgreSQL" in str(caught.value)
