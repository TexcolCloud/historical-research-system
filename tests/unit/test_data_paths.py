"""New runtime data is external; existing workspace and process identities stay discoverable."""

import json
import os
import shutil
from pathlib import Path

import psutil
import pytest
from PIL import Image

from hrs import app as launcher
from hrs import config, runtime
from hrs.config import Settings
from hrs.processing import workspace
from hrs.processing.application import prepare
from hrs.processing.checkpoints import Checkpoints
from hrs.processing.store import Store
from hrs.search import broker


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "checkout"
    root.mkdir()
    for name in os.environ:
        if name.startswith(("PLATFORM_", "INGEST_")) or name in {"HRS_CODEX_ROOT", "HRS_DATA_ROOT"}:
            monkeypatch.delenv(name)
    user = tmp_path / "user-data"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "user-home"))
    monkeypatch.setenv("LOCALAPPDATA", str(user))
    monkeypatch.setenv("XDG_DATA_HOME", str(user))
    (root / ".env").write_text(
        "INGEST_DEV_DB_PASSWORD=test\nINGEST_S3_ACCESS_KEY=test\nINGEST_S3_SECRET_KEY=test\n",
        "utf-8",
    )
    shared = root / "workspace.json"
    shared.write_text('{"root": null}', "utf-8")
    monkeypatch.setattr(workspace, "PROJECT", root)
    monkeypatch.setattr(workspace, "CONFIG", shared)
    return root


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_default_data_root_is_external_project_scoped_and_independent_of_cwd(
    project, tmp_path, monkeypatch, platform
):
    monkeypatch.setattr(config.sys, "platform", platform)
    root = config.data_root(project)
    user = tmp_path / ("user-home/.local/share" if platform == "win32" else "user-data")
    assert root.parent == user / "HistoricalResearchSystem"
    assert not root.is_relative_to(project)
    assert not root.exists()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert config.data_root(project) == root
    assert config.data_root(project.parent / "another-checkout") != root
    assert not root.exists()


def test_windows_data_root_is_stable_across_app_container_localappdata(project, tmp_path, monkeypatch):
    monkeypatch.setattr(config.sys, "platform", "win32")
    selected = config.data_root(project)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "package/LocalCache/Local"))

    assert config.data_root(project) == selected
    assert selected.parent == Path.home() / ".local/share/HistoricalResearchSystem"
    assert not selected.exists()


def test_linux_without_xdg_data_home_uses_the_same_user_home_layout(project, monkeypatch):
    monkeypatch.setattr(config.sys, "platform", "linux")
    monkeypatch.delenv("XDG_DATA_HOME")

    selected = config.data_root(project)

    assert selected.parent == Path.home() / ".local/share/HistoricalResearchSystem"
    assert not selected.exists()


def test_new_api_and_mcp_defaults_share_external_runtime_root(project):
    settings = Settings.load(project)
    root = config.data_root(project)
    assert settings.cache_root == root / "cache"
    assert workspace.resolve_workspace() == root / "native"
    assert workspace.resolve_workspace(settings.codex_root) == root / "native"
    assert settings.models_root == project / "models/document-retrieval"
    assert not root.exists()


def test_data_root_resolves_the_complete_redirected_path(project, monkeypatch):
    logical = config.data_root(project)
    physical = project.parent / "msix-data"
    original_resolve = Path.resolve

    def redirected_resolve(path, *args, **kwargs):
        if path == logical:
            return physical
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", redirected_resolve)

    assert config.data_root(project) == physical
    assert not logical.exists()
    assert not physical.exists()


def test_default_workspace_resolves_the_final_native_path(project, monkeypatch):
    data = config.data_root(project)
    logical = data / "native"
    physical = project.parent / "msix-native"
    original_resolve = Path.resolve
    monkeypatch.setattr(workspace, "data_root", lambda _: data)

    def redirected_resolve(path, *args, **kwargs):
        if path == logical:
            return physical
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", redirected_resolve)

    assert workspace.resolve_workspace() == physical
    assert not logical.exists()
    assert not physical.exists()


def test_new_checkpoint_root_is_canonical_after_directory_creation(project, monkeypatch):
    logical = project.parent / "virtual-native"
    physical = project.parent / "physical-native"
    original_resolve = Path.resolve
    original_mkdir = Path.mkdir

    def redirected_resolve(path, *args, **kwargs):
        if path == logical and physical.exists():
            return physical
        return original_resolve(path, *args, **kwargs)

    def redirected_mkdir(path, *args, **kwargs):
        return original_mkdir(physical if path == logical else path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", redirected_resolve)
    monkeypatch.setattr(Path, "mkdir", redirected_mkdir)

    selected = Checkpoints(logical)

    assert selected.root == physical
    assert (physical / "ledger.sqlite3").is_file()


def test_data_override_from_dotenv_is_project_relative_and_process_wins(project, monkeypatch):
    with (project / ".env").open("a", encoding="utf-8") as stream:
        stream.write("HRS_DATA_ROOT=../local-data\n")
    monkeypatch.chdir(project.parent)
    assert config.data_root(project) == project.parent / "local-data"
    monkeypatch.setenv("HRS_DATA_ROOT", "process-data")
    assert config.data_root(project) == project / "process-data"
    assert workspace.resolve_workspace() == project / "process-data/native"


def test_auto_workspace_preserves_existing_legacy_identity_and_explicit_shared_path(project):
    retained = project / "state/codex"
    retained.mkdir(parents=True)
    (retained / ".workspace.json").write_text('{"workspace_id":"retained"}', "utf-8")
    assert workspace.resolve_workspace() == retained
    workspace.CONFIG.write_text('{"root":"custom-native"}', "utf-8")
    assert workspace.resolve_workspace() == project / "custom-native"


def test_auto_workspace_preserves_prepared_documents_without_an_identity_file(project):
    retained = project / "state/codex"
    source = project.parent / "original.png"
    Image.new("RGB", (30, 40), "white").save(source)
    document = prepare(Store(retained), str(source))
    assert not (retained / ".workspace.json").exists()
    assert workspace.resolve_workspace() == retained
    selected = Store(workspace.resolve_workspace())
    assert selected.status(document["id"])["source"] == document["source"]
    selected.verify_evidence(selected.status(document["id"]))
    assert not (retained / ".workspace.json").exists()


def test_auto_workspace_preserves_read_only_legacy_json_documents_without_identity(project):
    source = project.parent / "original.png"
    Image.new("RGB", (30, 40), "white").save(source)
    staging = Store(project.parent / "staging-native")
    document = prepare(staging, str(source))
    retained = project / "state/codex"
    destination = retained / "documents" / document["id"]
    shutil.copytree(staging.document_directory(document["id"]), destination)
    (destination / "document.json").write_text(json.dumps(document), "utf-8")
    assert not (retained / ".workspace.json").exists()
    assert not (retained / "ledger.sqlite3").exists()
    assert workspace.resolve_workspace() == retained
    selected = Store(workspace.resolve_workspace())
    assert selected.checkpoints.ledger is None
    selected.verify_evidence(selected.status(document["id"]))
    assert not (retained / "ledger.sqlite3").exists()


def test_empty_legacy_directory_does_not_override_the_new_data_home(project):
    (project / "state/codex").mkdir(parents=True)
    assert workspace.resolve_workspace() == config.data_root(project) / "native"


def test_runtime_registry_writes_outside_checkout_and_reads_legacy_owners(project):
    native = project.parent / "native"
    native.mkdir()
    (native / ".workspace.json").write_text('{"workspace_id":"same-identity"}', "utf-8")
    settings = Settings.load(project).model_copy(update={"codex_root": native})
    current = runtime.register_server(settings, "http://127.0.0.1:18331")
    assert current.parent == config.data_root(project) / "diagnostics/servers"
    legacy = project / ".cache/platform-diagnostics/servers/legacy.json"
    legacy.parent.mkdir(parents=True)
    record = json.loads(current.read_text("utf-8"))
    record["url"] = "http://127.0.0.1:18332"
    legacy.write_text(json.dumps(record), "utf-8")
    assert {row["url"] for row in runtime.live_servers(project)} == {
        "http://127.0.0.1:18331", "http://127.0.0.1:18332"
    }


def test_stop_still_reaches_legacy_launcher_after_default_location_changes(project, monkeypatch):
    legacy = project / ".cache/platform-diagnostics"
    legacy.mkdir(parents=True)
    stop_file = "a" * 32 + ".stop"
    (legacy / "codex-platform.json").write_text(
        json.dumps({"pid": os.getpid(), "stop_file": stop_file}), "utf-8"
    )
    monkeypatch.setattr(runtime, "live_servers", lambda _: [{"pid": os.getpid()}])
    monkeypatch.setattr(launcher, "ROOT", project)
    monkeypatch.setattr(launcher, "CONTROL", config.data_root(project) / "diagnostics")
    launcher.command("stop")
    assert (legacy / stop_file).is_file()
    assert not launcher.CONTROL.exists()


def test_stop_does_not_recreate_control_files_for_stale_legacy_launchers(project, monkeypatch):
    legacy = project / ".cache/platform-diagnostics"
    legacy.mkdir(parents=True)
    stop_file = "b" * 32 + ".stop"
    (legacy / "codex-platform.json").write_text(
        json.dumps({"pid": os.getpid(), "stop_file": stop_file}), "utf-8"
    )
    monkeypatch.setattr(runtime, "live_servers", lambda _: [])
    monkeypatch.setattr(launcher, "ROOT", project)
    monkeypatch.setattr(launcher, "CONTROL", config.diagnostics_root(project))
    launcher.command("stop")
    assert not (legacy / stop_file).exists()
    assert not launcher.CONTROL.exists()


def test_stop_checks_the_new_launcher_process_creation_identity(project, monkeypatch):
    selected = config.diagnostics_root(project)
    selected.mkdir(parents=True)
    stop_file = "c" * 32 + ".stop"
    record = {
        "pid": os.getpid(),
        "process_create_time": 0,
        "stop_file": stop_file,
    }
    path = selected / "codex-platform.json"
    path.write_text(json.dumps(record), "utf-8")
    monkeypatch.setattr(launcher, "ROOT", project)
    monkeypatch.setattr(launcher, "CONTROL", selected)
    launcher.command("stop")
    assert not (selected / stop_file).exists()
    record["process_create_time"] = psutil.Process(os.getpid()).create_time()
    path.write_text(json.dumps(record), "utf-8")
    launcher.command("stop")
    assert (selected / stop_file).is_file()


def test_status_does_not_create_a_new_workspace_or_diagnostics_directory(project, monkeypatch, capsys):
    monkeypatch.setenv("PLATFORM_RETRIEVAL_DEVICE", "cpu")
    monkeypatch.setattr(launcher, "ROOT", project)
    monkeypatch.setattr(launcher, "CONTROL", config.diagnostics_root(project))
    monkeypatch.setattr(runtime, "discover_servers", lambda _: [])
    launcher.command("status")
    assert json.loads(capsys.readouterr().out)["configured_root"] == str(
        config.data_root(project) / "native"
    )
    assert not config.data_root(project).exists()


def test_broker_events_and_gpu_lock_use_external_runtime_root(project):
    selected = broker.RetrievalBroker(project)
    assert selected.folder == config.data_root(project) / "retrieval"
    selected.event({"event": "fixture"})
    assert (selected.folder / "events.jsonl").is_file()
    assert not (project / "state").exists()
