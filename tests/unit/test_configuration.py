"""Configuration remains portable without rewriting private local settings."""

from pathlib import Path

from hrs.config import Settings, project_root


def test_deployment_and_resource_overrides_resolve_outside_checkout(tmp_path, monkeypatch):
    deployment = tmp_path / "deployment"
    outside = tmp_path / "outside"
    deployment.mkdir()
    outside.mkdir()
    monkeypatch.chdir(outside)
    monkeypatch.setenv("HRS_PROJECT_ROOT", str(deployment))
    assert project_root() == deployment
    assert project_root(outside) == outside
    settings = Settings.model_validate(
        {
            "project_root": deployment,
            "database_url": "postgresql+psycopg://owner@localhost/test",
            "s3_endpoint": "http://127.0.0.1:58339",
            "s3_bucket": "test",
            "s3_access_key": "test",
            "s3_secret_key": "test",
            "models_root": "weights",
            "web_dist": "frontend",
            "cache_root": "cache",
            "codex_root": "evidence",
        }
    )
    for field, relative in (
        ("models_root", "weights"),
        ("web_dist", "frontend"),
        ("cache_root", "cache"),
        ("codex_root", "evidence"),
    ):
        assert getattr(settings, field) == Path(deployment / relative)
