"""Real loopback requests exercise Uvicorn's request-before-lifespan shutdown order."""

import socket
import threading
import time
from contextlib import ExitStack
from types import SimpleNamespace

import httpx
import uvicorn

from hrs.api import main
from hrs.api.routes import events
from hrs.api.server import Server
from hrs.config import Settings
from hrs.library import processing_bridge


def test_open_sse_connections_drain_before_application_resources_stop(tmp_path, monkeypatch, caplog):
    settings = Settings(
        database_url="postgresql+psycopg://unused:unused@127.0.0.1/unused",
        s3_endpoint="http://127.0.0.1:1",
        s3_bucket="unused",
        s3_access_key="unused",
        s3_secret_key="unused",
        project_root=tmp_path,
        cache_root=tmp_path / "cache",
    )
    stopped = []

    def dispose():
        assert app.state.shutdown_requested.is_set()
        stopped.append("engine")

    class Bridge:
        def __init__(self, *args):
            pass

        def start(self):
            pass

        def stop(self):
            assert app.state.shutdown_requested.is_set()
            stopped.append("bridge")

    def read_events(*args):
        assert "engine" not in stopped
        return []

    monkeypatch.setattr(main, "engine_for", lambda _: SimpleNamespace(dispose=dispose))
    monkeypatch.setattr(processing_bridge, "CodexBridge", Bridge)
    monkeypatch.setattr(events, "read_events", read_events)
    app = main.create_app(settings)
    assert not app.state.shutdown_requested.is_set()
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = Server(
        uvicorn.Config(app, timeout_graceful_shutdown=0.3, log_config=None, access_log=False)
    )
    errors = []

    def serve():
        try:
            server.run(sockets=[listener])
        except BaseException as error:  # noqa: BLE001 -- return thread errors to the test
            errors.append(error)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started, errors
        with ExitStack() as clients:
            readers = []
            for _ in range(2):
                response = clients.enter_context(
                    httpx.stream("GET", f"http://127.0.0.1:{port}/api/v2/events", timeout=3)
                )
                assert response.status_code == 200
                reader = response.iter_text()
                assert next(reader) == ": keepalive\n\n"
                readers.append(reader)
            server.should_exit = True
            thread.join(timeout=3)
            assert not thread.is_alive()
            assert all(list(reader) == [] for reader in readers)
        assert not errors
        assert stopped == ["bridge", "engine"]
        assert "timeout graceful shutdown exceeded" not in caplog.text
        assert "CancelledError" not in caplog.text
    finally:
        server.should_exit = True
        thread.join(timeout=3)
        listener.close()
