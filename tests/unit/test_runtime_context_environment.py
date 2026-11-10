"""MCP deployment overrides survive Codex's filtered stdio environment."""

import json

from hrs.codex.runtime_context import command
from hrs.processing.store import Store


def test_mcp_forwards_deployment_names_without_serializing_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("HRS_CODEX_ROOT", str(tmp_path / "native"))
    monkeypatch.setenv("PLATFORM_DATABASE_URL", "postgresql://private-test-value")
    monkeypatch.setenv("PLATFORM_S3_SECRET_KEY", "private-test-secret")
    monkeypatch.setenv("INGEST_S3_BUCKET", "isolated-bucket")
    monkeypatch.setenv("UNRELATED_SECRET", "unrelated-test-secret")
    Store(tmp_path / "native")
    args, _, _ = command(
        "codex", tmp_path / "native", "doc", tmp_path / "logs", None, tmp_path,
    )
    forwarded = json.loads(next(
        value.split("=", 1)[1] for value in args
        if value.startswith("mcp_servers.historical_research.env_vars=")
    ))
    assert {"HRS_CODEX_ROOT", "PLATFORM_DATABASE_URL", "PLATFORM_S3_SECRET_KEY",
            "INGEST_S3_BUCKET"}.issubset(forwarded)
    assert "UNRELATED_SECRET" not in forwarded
    serialized = " ".join(args)
    for secret in ("private-test-value", "private-test-secret", "unrelated-test-secret"):
        assert secret not in serialized
