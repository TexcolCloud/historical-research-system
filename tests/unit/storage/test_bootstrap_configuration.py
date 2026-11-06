"""Initialization must create and migrate the actual configured database targets."""

import json
import os
from types import SimpleNamespace

import pytest
from sqlalchemy.engine import make_url

from hrs.storage import bootstrap as module


@pytest.fixture
def initialized(tmp_path, monkeypatch):
    for name in list(os.environ):
        if name.startswith(("PLATFORM_", "INGEST_")) or name == "HRS_PROJECT_ROOT":
            monkeypatch.delenv(name)
    calls = {
        "admin": [],
        "queried": [],
        "created": [],
        "settings": [],
        "migrated": [],
        "disposed": [],
    }

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, statement, parameters=None):
            if isinstance(statement, str):
                calls["queried"].append(parameters[0])
                return SimpleNamespace(fetchone=lambda: None)
            calls["created"].append(statement.as_string())
            return SimpleNamespace(fetchone=lambda: None)

    def connect(url, **options):
        calls["admin"].append((make_url(url), options))
        return Connection()

    engine = SimpleNamespace(dispose=lambda: calls["disposed"].append(True))

    def engine_for(settings):
        calls["settings"].append(settings)
        return engine

    monkeypatch.setattr(module.psycopg, "connect", connect)
    monkeypatch.setattr(module, "engine_for", engine_for)
    monkeypatch.setattr(module, "migrate", lambda selected: calls["migrated"].append(selected))

    def configure(**overrides):
        values = {
            "INGEST_DATABASE_URL": "postgresql+psycopg://owner:test@127.0.0.1:55436/postgres",
            "INGEST_S3_ACCESS_KEY": "unused",
            "INGEST_S3_SECRET_KEY": "unused",
            **overrides,
        }
        (tmp_path / ".env").write_text(
            "".join(f"{name}={json.dumps(value)}\n" for name, value in values.items()), "utf-8"
        )
        return tmp_path

    return configure, calls, engine


def test_bootstrap_initializes_custom_configured_database_instead_of_a_hardcoded_name(initialized):
    configure, calls, engine = initialized
    root = configure(
        PLATFORM_DATABASE_URL="postgresql+psycopg://owner:test@127.0.0.1:55436/custom_library"
    )
    module.bootstrap(root)
    assert set(calls["queried"]) == {"custom_library", "historical_research_v2_test"}
    assert 'CREATE DATABASE "custom_library"' in calls["created"]
    assert (
        make_url(calls["settings"][0].database_url.get_secret_value()).database == "custom_library"
    )
    assert calls["admin"][0][0].database == "postgres" and calls["admin"][0][1] == {
        "autocommit": True
    }
    assert calls["migrated"] == [engine] and calls["disposed"] == [True]


def test_bootstrap_never_creates_a_remote_database_using_the_local_admin(initialized):
    configure, calls, engine = initialized
    selected = "postgresql+psycopg://remote:test@database.example:55440/archive_library"
    module.bootstrap(configure(PLATFORM_DATABASE_URL=selected))
    assert calls["queried"] == ["historical_research_v2_test"]
    assert calls["settings"][0].database_url.get_secret_value() == selected
    assert calls["migrated"] == [engine]


def test_equivalent_explicit_and_implicit_postgresql_ports_select_the_same_admin_endpoint(
    initialized,
):
    configure, calls, _ = initialized
    module.bootstrap(
        configure(
            INGEST_DATABASE_URL="postgresql+psycopg://owner:test@127.0.0.1/postgres",
            PLATFORM_DATABASE_URL="postgresql+psycopg://owner:test@127.0.0.1:5432/custom_library",
        )
    )
    assert "custom_library" in calls["queried"]


def test_migration_failure_still_disposes_the_configured_engine(initialized, monkeypatch):
    configure, calls, _ = initialized

    def failed_migration(engine):
        raise RuntimeError("isolated migration fixture failed")

    monkeypatch.setattr(module, "migrate", failed_migration)
    with pytest.raises(RuntimeError, match="migration fixture"):
        module.bootstrap(configure())
    assert calls["disposed"] == [True]
