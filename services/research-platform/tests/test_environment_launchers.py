import importlib.util
from pathlib import Path

from sqlalchemy.engine import make_url


def load_launcher(filename):
    path = Path(__file__).resolve().parents[3] / "scripts" / filename
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_native_storage_and_search_use_the_same_minimal_configuration(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        "INGEST_DEV_DB_PASSWORD=launcher-test\n"
        "INGEST_S3_ACCESS_KEY=launcher-access\n"
        "INGEST_S3_SECRET_KEY=launcher-secret\n",
        encoding="utf-8",
    )
    launcher = load_launcher("hrs_codex_platform.py")
    monkeypatch.setattr(launcher, "ROOT", tmp_path)
    calls = []
    monkeypatch.setattr(launcher.subprocess, "run", lambda *args, **kwargs: calls.append(kwargs))

    launcher.infrastructure()

    assert len(calls) == 2
    for call in calls:
        assert call["env"]["INGEST_S3_BUCKET"] == "ingestion-acceptance"
        assert make_url(call["env"]["PLATFORM_DOCKER_DATABASE_URL"]).host == "host.docker.internal"
        assert call["cwd"] == tmp_path


def test_legacy_compose_derives_defaults_and_preserves_explicit_call_environment(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("INGEST_DEV_DB_PASSWORD=launcher-test\n", encoding="utf-8")
    launcher = load_launcher("hrs_v2.py")
    monkeypatch.setattr(launcher, "ROOT", tmp_path)
    calls = []
    monkeypatch.setattr(launcher.subprocess, "run", lambda *args, **kwargs: calls.append(kwargs))

    launcher.compose("ps")
    explicit = {"caller-owned": "preserved"}
    launcher.compose("ps", env=explicit)

    assert make_url(calls[0]["env"]["PLATFORM_DOCKER_DATABASE_URL"]).database == "historical_research_v2"
    assert calls[1]["env"] is explicit
