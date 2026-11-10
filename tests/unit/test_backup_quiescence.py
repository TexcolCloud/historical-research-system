"""Backup admission follows the selected project's actual services and upload endpoint."""

import subprocess
import time
from types import SimpleNamespace

import pytest

from hrs import backups, runtime
from hrs.config import Settings
from hrs.search import service


@pytest.fixture
def admission(tmp_path, monkeypatch):
    settings = Settings(
        project_root=tmp_path,
        cache_root=tmp_path / "cache",
        codex_root=tmp_path / "native",
        database_url="postgresql+psycopg://unused@127.0.0.1/unused",
        s3_endpoint="http://127.0.0.1:1",
        s3_bucket="unused",
        s3_access_key="unused",
        s3_secret_key="unused",
        tus_control_url="http://127.0.0.1:18179/uploads/",
    )
    store = SimpleNamespace(root=settings.codex_root, checkpoints=SimpleNamespace(ledger=None))
    state = {
        "servers": [],
        "discovered": [],
        "brokers": [],
        "workbench": None,
        "containers": [],
        "open_endpoints": set(),
        "probed": [],
    }
    monkeypatch.setattr(runtime, "live_servers", lambda project: state["servers"])
    monkeypatch.setattr(runtime, "discover_servers", lambda project: state["discovered"])
    monkeypatch.setattr(service, "live_brokers", lambda project: state["brokers"])
    monkeypatch.setattr(backups, "running_workbench", lambda selected: state["workbench"])
    monkeypatch.setattr(
        backups, "load_environment", lambda project: {"COMPOSE_PROJECT_NAME": "selected-project"}
    )
    monkeypatch.setattr(
        backups.subprocess,
        "run",
        lambda *args, **options: subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout="\n".join(state["containers"]),
            stderr="",
        ),
    )

    class Probe:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def settimeout(self, timeout):
            pass

        def connect_ex(self, endpoint):
            state["probed"].append(endpoint)
            return 0 if endpoint in state["open_endpoints"] else 111

    monkeypatch.setattr(backups.socket, "socket", Probe)
    return settings, store, state


def test_live_custom_port_api_blocks_backup_even_when_runtime_route_is_unavailable(admission):
    settings, store, state = admission
    state["servers"] = [{"pid": 123, "url": "http://127.0.0.1:18243", "root": str(store.root)}]
    state["discovered"] = [{"url": "http://127.0.0.1:18243", "available": False}]
    with pytest.raises(RuntimeError, match="API instances"):
        backups.require_quiesced(settings, store)
    assert state["probed"] == []


@pytest.mark.parametrize("relative", ["native", "another-workspace"])
def test_discovered_legacy_api_in_this_project_blocks_backup(admission, relative):
    settings, store, state = admission
    state["discovered"] = [
        {
            "url": "http://127.0.0.1:18351",
            "available": True,
            "runtime": {"root": str(settings.project_root / relative)},
        }
    ]
    with pytest.raises(RuntimeError, match="API instances"):
        backups.require_quiesced(settings, store)


def test_an_unrelated_project_api_and_container_do_not_block_the_selected_backup(admission):
    settings, store, state = admission
    state["discovered"] = [
        {
            "url": "http://127.0.0.1:18170",
            "available": True,
            "runtime": {"root": str(settings.project_root.parent / "unrelated-project/native")},
        }
    ]
    state["containers"] = ["unrelated-project-tusd-1"]
    backups.require_quiesced(settings, store)
    assert state["probed"] == [("127.0.0.1", 18179)]


def test_configured_custom_upload_port_is_checked_instead_of_the_default(admission):
    settings, store, state = admission
    state["open_endpoints"] = {("127.0.0.1", 18179)}
    with pytest.raises(RuntimeError, match="configured upload service"):
        backups.require_quiesced(settings, store)
    assert state["probed"] == [("127.0.0.1", 18179)]


def test_implicit_https_upload_port_uses_443(admission):
    settings, store, state = admission
    settings = settings.model_copy(update={"tus_control_url": "https://127.0.0.1/uploads/"})
    state["open_endpoints"] = {("127.0.0.1", 443)}
    with pytest.raises(RuntimeError, match="configured upload service"):
        backups.require_quiesced(settings, store)
    assert state["probed"] == [("127.0.0.1", 443)]


def test_selected_upload_container_is_stopped_before_any_snapshot(admission):
    settings, store, state = admission
    state["containers"] = ["selected-project-tusd-1"]
    with pytest.raises(RuntimeError, match="Stop uploads"):
        backups.require_quiesced(settings, store)


@pytest.mark.parametrize("owner", ["brokers", "workbench"])
def test_live_native_or_retrieval_owner_blocks_snapshot(admission, owner):
    settings, store, state = admission
    state[owner] = [{"pid": 123}] if owner == "brokers" else {"executor_running": True}
    with pytest.raises(RuntimeError):
        backups.require_quiesced(settings, store)


@pytest.mark.parametrize("active", [False, True])
def test_only_unexpired_native_worker_sessions_hold_backup_admission(admission, active, monkeypatch):
    from hrs.processing.store import Store

    settings, store, _ = admission
    store = Store(settings.codex_root)
    sessions = [{"state": "busy", "expires_at": time.time() + (60 if active else -60)}]
    monkeypatch.setattr(store.checkpoints.ledger, "sessions", lambda: sessions)
    if active:
        with pytest.raises(RuntimeError, match="worker sessions"):
            backups.require_quiesced(settings, store)
    else:
        backups.require_quiesced(settings, store)


def test_research_worker_without_a_registered_server_blocks_platform_backup(admission):
    from hrs.processing.research_workflow import ResearchWorkflow
    from hrs.processing.store import Store
    from tests.unit.research.test_workflow import snapshot

    settings, _, _ = admission
    store = Store(settings.codex_root)
    workflow = ResearchWorkflow(store)
    run = workflow.enqueue(snapshot())
    workflow.claim(run["run_id"], "synthetic-author", stage="author")
    with pytest.raises(RuntimeError, match="Research workers"):
        backups.require_quiesced(settings, store)
    workflow.pause(run["run_id"])
    backups.require_quiesced(settings, store)


def test_external_upload_status_cannot_be_silently_assumed_quiescent(admission):
    settings, store, state = admission
    settings = settings.model_copy(update={"tus_control_url": "https://uploads.example/uploads/"})
    with pytest.raises(RuntimeError, match="stopped loopback"):
        backups.require_quiesced(settings, store)
    assert state["probed"] == []
