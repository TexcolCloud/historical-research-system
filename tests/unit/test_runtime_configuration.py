"""Runtime endpoints and model readiness must describe the services callers use."""

import json
import os

import pytest

from hrs import cli
from hrs.config import Settings
from hrs.search.settings import MODEL_REVISIONS


@pytest.fixture
def environment(tmp_path, monkeypatch):
    for name in os.environ:
        if name.startswith(("PLATFORM_", "INGEST_")) or name == "HRS_PROJECT_ROOT":
            monkeypatch.delenv(name)
    monkeypatch.setenv("HRS_PROJECT_ROOT", str(tmp_path))
    (tmp_path / ".env").write_text(
        "INGEST_DEV_DB_PASSWORD=test\nINGEST_S3_ACCESS_KEY=test\nINGEST_S3_SECRET_KEY=test\n",
        "utf-8",
    )
    return tmp_path


def test_upload_port_derives_the_proxy_url_but_explicit_url_wins(environment, monkeypatch):
    monkeypatch.setenv("PLATFORM_UPLOAD_PORT", "18179")
    assert Settings.load().tus_control_url == "http://127.0.0.1:18179/uploads/"
    monkeypatch.setenv("PLATFORM_TUS_CONTROL_URL", "http://127.0.0.1:18180/custom/")
    assert Settings.load().tus_control_url == "http://127.0.0.1:18180/custom/"


def test_retrieval_mode_derives_from_endpoint_and_can_select_external_loopback(
    environment, monkeypatch
):
    assert Settings.load().retrieval_mode == "managed"
    monkeypatch.setenv("PLATFORM_RETRIEVAL_ENDPOINT", "http://127.0.0.1:18168/retrieval")
    assert Settings.load().retrieval_mode == "managed"
    monkeypatch.setenv("PLATFORM_RETRIEVAL_MODE", "external")
    assert Settings.load().retrieval_mode == "external"
    monkeypatch.delenv("PLATFORM_RETRIEVAL_MODE")
    monkeypatch.setenv("PLATFORM_RETRIEVAL_ENDPOINT", "https://retrieval.example/retrieval")
    assert Settings.load().retrieval_mode == "external"


def test_models_check_rejects_half_downloaded_model_artifacts(environment, capsys):
    for name, revision in MODEL_REVISIONS.items():
        folder = environment / "models/document-retrieval" / name.split("/")[-1] / revision
        folder.mkdir(parents=True)
        (folder / "config.json").write_text("{}", "utf-8")
    with pytest.raises(SystemExit) as error:
        cli._models("check")
    assert error.value.code == 1
    report = json.loads(capsys.readouterr().out)
    assert all(not item["present"] for item in report.values())
    assert all("tokenizer.json" in item["missing"] for item in report.values())


def test_direct_relative_project_configuration_keeps_one_canonical_root(environment, monkeypatch):
    monkeypatch.chdir(environment.parent)
    settings = Settings(
        project_root=environment.name,
        cache_root="cache",
        codex_root="evidence",
        database_url="postgresql+psycopg://unused@127.0.0.1/unused",
        s3_endpoint="http://127.0.0.1:1",
        s3_bucket="unused",
        s3_access_key="unused",
        s3_secret_key="unused",
    )
    assert settings.project_root == environment
    assert settings.models_root == environment / "models/document-retrieval"
    assert settings.web_dist == environment / "web/dist"
    assert settings.codex_root == environment / "evidence"
