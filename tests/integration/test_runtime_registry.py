"""Custom-port API instances register only after admission and drain on failure."""

import json
import socket
import threading
import time
from contextlib import asynccontextmanager

import pytest
import uvicorn
from fastapi import FastAPI

from hrs import app as launcher
from hrs import runtime
from hrs.api import server as serving
from hrs.config import Settings, diagnostics_root


def wait_until(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            pytest.fail("API lifecycle did not reach the expected state")
        time.sleep(0.01)


@pytest.fixture
def application(tmp_path):
    root = tmp_path / "native"
    root.mkdir()
    workspace = {"schema_version": 1, "workspace_id": "temporary-real-workspace"}
    (root / ".workspace.json").write_text(json.dumps(workspace), "utf-8")
    settings = Settings(
        project_root=tmp_path,
        cache_root=tmp_path / "cache",
        codex_root=root,
        database_url="postgresql+psycopg://unused@127.0.0.1/unused",
        s3_endpoint="http://127.0.0.1:1",
        s3_bucket="unused",
        s3_access_key="unused",
        s3_secret_key="unused",
        retrieval_device="cpu",
    )
    events = []

    @asynccontextmanager
    async def lifespan(app):
        events.append("started")
        try:
            yield
        finally:
            events.append("stopped")

    app = FastAPI(lifespan=lifespan)
    app.state.settings = settings
    app.state.shutdown_requested = threading.Event()

    @app.get("/api/v2/runtime")
    def current():
        return {"backend": "codex", "workspace_id": workspace["workspace_id"], "root": str(root)}

    return app, settings, events


def running_server(app):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = serving.Server(
        uvicorn.Config(app, log_config=None, access_log=False, timeout_graceful_shutdown=1)
    )
    errors = []

    def run():
        try:
            server.run(sockets=[listener])
        except BaseException as error:  # noqa: BLE001 -- return owned thread failures to assertions
            errors.append(error)

    thread = threading.Thread(target=run, daemon=True)
    return server, listener, port, thread, errors


def test_api_admission_and_custom_port_receipt_keep_status_tied_to_the_running_workspace(
    application, monkeypatch, capsys
):
    app, settings, events = application
    server, listener, port, thread, errors = running_server(app)
    previous = runtime.urlopen

    def local_request(url, **options):
        if url.startswith(f"http://127.0.0.1:{port}/"):
            return previous(url, **options)
        raise OSError("Unowned discovery candidate isolated by this test")

    monkeypatch.setattr(runtime, "urlopen", local_request)
    try:
        with runtime.lifecycle_lock(settings.project_root):
            thread.start()
            time.sleep(0.15)
            assert events == [] and not server.started
            assert runtime.live_servers(settings.project_root) == []
        wait_until(lambda: server.started)
        wait_until(lambda: bool(runtime.live_servers(settings.project_root)))
        receipt = runtime.live_servers(settings.project_root)[0]
        assert receipt["url"] == f"http://127.0.0.1:{port}"
        assert receipt["workspace_id"] == "temporary-real-workspace"
        configured = settings.project_root / "other-native"
        configured.mkdir()
        (configured / ".workspace.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "workspace_id": "configured-different-workspace",
                }
            ),
            "utf-8",
        )
        selected = settings.model_copy(update={"codex_root": configured})
        monkeypatch.setattr(Settings, "load", lambda root: selected)
        monkeypatch.setattr(launcher, "ROOT", settings.project_root)
        monkeypatch.setattr(
            launcher, "CONTROL", diagnostics_root(settings.project_root)
        )
        launcher.command("status")
        status = json.loads(capsys.readouterr().out)
        assert status["ui"] and status["runtime_available"] and status["workspace_matches"] is False
        assert status["workspace_id"] == "configured-different-workspace"
        actual = next(row for row in status["servers"] if row["available"])
        assert actual["runtime"]["workspace_id"] == "temporary-real-workspace"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
    assert not errors and not thread.is_alive()
    assert events == ["started", "stopped"]
    assert runtime.live_servers(settings.project_root) == []


def test_receipt_write_failure_drains_started_application_resources(application, monkeypatch):
    app, settings, events = application
    server, listener, port, thread, errors = running_server(app)

    def fail_receipt(*args):
        raise OSError("Receipt storage fixture unavailable")

    monkeypatch.setattr(serving, "register_server", fail_receipt)
    thread.start()
    thread.join(timeout=5)
    listener.close()
    assert not thread.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], OSError)
    assert events == ["started", "stopped"]
    assert app.state.shutdown_requested.is_set()
    assert runtime.live_servers(settings.project_root) == []
    with socket.socket() as probe:
        assert probe.connect_ex(("127.0.0.1", port)) != 0
