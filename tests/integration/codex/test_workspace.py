"""One workspace identity across different entry points and working directories."""

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from hrs.codex import workbench_lifecycle
from hrs.codex.workbench import create_review_server
from hrs.config import Settings
from hrs.processing import workspace
from hrs.processing.runner import Runner
from hrs.processing.store import Store, atomic_json


@pytest.fixture
def workspace_project(tmp_path, monkeypatch):
    for name in os.environ:
        if name.startswith(("PLATFORM_", "INGEST_")) or name == "HRS_CODEX_ROOT":
            monkeypatch.delenv(name)
    config = tmp_path / "workspace.json"
    config.write_text(json.dumps({"root": "default-evidence"}), encoding="utf-8")
    monkeypatch.setattr(workspace, "PROJECT", tmp_path)
    monkeypatch.setattr(workspace, "CONFIG", config)
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.chdir(outside)
    return tmp_path


@pytest.mark.parametrize("setting", ["HRS_CODEX_ROOT", "PLATFORM_CODEX_ROOT"])
def test_dotenv_workspace_is_project_relative_without_credentials_or_creation(workspace_project, setting):
    environment = f"{setting}=local/evidence\n"
    (workspace_project / ".env").write_text(environment, encoding="utf-8")

    assert workspace.resolve_workspace() == workspace_project / "local/evidence"
    assert not (workspace_project / "local").exists()
    assert (workspace_project / ".env").read_text(encoding="utf-8") == environment


@pytest.mark.parametrize("unrelated", ["INGEST_DATABASE_URL=invalid", "PLATFORM_UPLOAD_PORT=invalid"])
def test_unrelated_platform_configuration_does_not_block_workspace_resolution(workspace_project, unrelated):
    (workspace_project / ".env").write_text(
        f"HRS_CODEX_ROOT=local/evidence\n{unrelated}\n", encoding="utf-8"
    )

    assert workspace.resolve_workspace() == workspace_project / "local/evidence"
    assert not (workspace_project / "local").exists()


@pytest.mark.parametrize(
    ("dotenv", "process", "expected"),
    [
        ({}, {}, "default-evidence"),
        ({"HRS_CODEX_ROOT": "local/native"}, {}, "local/native"),
        ({"PLATFORM_CODEX_ROOT": "", "HRS_CODEX_ROOT": "local/native"}, {}, "local/native"),
        ({"PLATFORM_CODEX_ROOT": "", "HRS_CODEX_ROOT": ""}, {}, "default-evidence"),
        (
            {"HRS_CODEX_ROOT": "local/native", "PLATFORM_CODEX_ROOT": "local/platform"},
            {},
            "local/platform",
        ),
        (
            {"HRS_CODEX_ROOT": "local/native", "PLATFORM_CODEX_ROOT": "local/platform"},
            {"HRS_CODEX_ROOT": "process-native"},
            "outside/process-native",
        ),
        (
            {"HRS_CODEX_ROOT": "local/native", "PLATFORM_CODEX_ROOT": "local/platform"},
            {"PLATFORM_CODEX_ROOT": "process-platform"},
            "process-platform",
        ),
        (
            {"HRS_CODEX_ROOT": "local/native", "PLATFORM_CODEX_ROOT": "local/platform"},
            {"PLATFORM_CODEX_ROOT": "process-platform", "HRS_CODEX_ROOT": "process-native"},
            "process-platform",
        ),
    ],
)
def test_api_and_mcp_share_workspace_source_precedence(workspace_project, monkeypatch, dotenv, process, expected):
    environment = "INGEST_DEV_DB_PASSWORD=test\nINGEST_S3_ACCESS_KEY=test\nINGEST_S3_SECRET_KEY=test\n"
    environment += "".join(f"{key}={value}\n" for key, value in dotenv.items())
    (workspace_project / ".env").write_text(environment, encoding="utf-8")
    for key, value in process.items():
        monkeypatch.setenv(key, value)

    settings = Settings.load(workspace_project)
    assert workspace.resolve_workspace() == workspace_project / expected
    assert workspace.resolve_workspace(settings.codex_root) == workspace_project / expected
    assert workspace.resolve_workspace("explicit-cli") == workspace_project / "outside/explicit-cli"
    explicit_api = Settings.model_validate({**settings.model_dump(), "codex_root": "explicit-api"})
    assert workspace.resolve_workspace(explicit_api.codex_root) == workspace_project / "explicit-api"
    assert not (workspace_project / expected).exists()


def test_default_root_is_project_relative_and_identity_survives_restart(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"root": "evidence"}), encoding="utf-8")
    monkeypatch.setattr(workspace, "PROJECT", tmp_path)
    monkeypatch.setattr(workspace, "CONFIG", config)
    monkeypatch.delenv("HRS_CODEX_ROOT", raising=False)
    monkeypatch.delenv("PLATFORM_CODEX_ROOT", raising=False)
    nested = tmp_path / "elsewhere"
    nested.mkdir()
    monkeypatch.chdir(nested)
    root = workspace.resolve_workspace()
    assert root == tmp_path / "evidence"
    first = workspace.identity(Store(root))
    assert workspace.identity(Store(root)) == first
    assert first["is_default"]
    assert workspace.identity(Store(nested))["workspace_id"] != first["workspace_id"]
    monkeypatch.setenv("HRS_CODEX_ROOT", str(nested))
    assert workspace.resolve_workspace() == nested
    assert workspace.resolve_workspace(root) == root


def test_reading_existing_identity_does_not_wait_for_document_processing(tmp_path):
    store = Store(tmp_path)
    original = workspace.identity(store)
    reader = Store(store.root)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with store.lock:
            result = pool.submit(workspace.identity, reader).result(timeout=0.5)
    assert result == original


def test_concurrent_identity_creation_retains_one_workspace_id(tmp_path):
    store = Store(tmp_path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        values = list(pool.map(lambda _: workspace.identity(Store(store.root)), range(8)))
    assert len({value["workspace_id"] for value in values}) == 1


def test_existing_loopback_workbench_is_reused_without_spawning_an_executor(tmp_path, monkeypatch):
    store = Store(tmp_path)
    with (
        create_review_server(store, 0) as server,
        Runner(store, execute=lambda *a, **k: None) as executor,
    ):
        server.executor = executor
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        record = {
            **workspace.identity(store),
            "url": server.review_url,
            "pid": 123,
            "workbench_protocol": 2,
        }
        atomic_json(store.root / ".workbench.json", record)
        monkeypatch.setattr(
            workbench_lifecycle.subprocess,
            "Popen",
            lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("duplicate workbench")),
        )
        try:
            assert workbench_lifecycle.ensure_workbench(store) == record
            legacy = {k: v for k, v in record.items() if k != "workbench_protocol"}
            atomic_json(store.root / ".workbench.json", legacy)
            assert workbench_lifecycle.ensure_workbench(store) == legacy
            # A registry cannot make an unrelated workspace or dead executor healthy.
            atomic_json(store.root / ".workbench.json", {**record, "workspace_id": "wrong"})
            assert workspace.running_workbench(store) is None
            atomic_json(store.root / ".workbench.json", record)
            executor.stop()
            assert workspace.running_workbench(store) is None
            atomic_json(
                store.root / ".workbench.json",
                {**record, "url": "https://example.com/?token=never-send"},
            )
            assert workspace.running_workbench(store) is None
        finally:
            server.shutdown()
            thread.join(timeout=5)
