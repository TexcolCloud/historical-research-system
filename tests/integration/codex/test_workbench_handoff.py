"""Native entry points reuse only an identity-verified local reading platform."""

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

from hrs.codex import workbench, workbench_lifecycle
from hrs.processing.store import Store
from hrs.processing.workspace import identity


@contextmanager
def platform(store, *, status=201):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            calls.append(
                (self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            )
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {"book_id": "existing-book", "url": "/#/books/existing-book?tab=progress"}
                ).encode()
            )

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield (
                {
                    "url": f"http://127.0.0.1:{server.server_port}",
                    "available": True,
                    "runtime": identity(store),
                },
                calls,
            )
        finally:
            server.shutdown()
            thread.join(5)


def no_spawn(monkeypatch):
    monkeypatch.setattr(
        workbench_lifecycle.subprocess,
        "Popen",
        lambda *a, **k: pytest.fail("duplicate standalone executor"),
    )


def test_open_native_document_attaches_once_then_selects_its_requested_review_page(
    tmp_path, monkeypatch
):
    store = Store(tmp_path / "native")
    document = "a" * 24
    with platform(store) as (server, calls):
        monkeypatch.setattr(
            workbench_lifecycle, "discover_servers", lambda _: [server], raising=False
        )
        no_spawn(monkeypatch)
        result = workbench_lifecycle.ensure_workbench(store, document_id=document, page=7)
        assert result["surface"] == "platform"
        parsed = urlparse(result["url"])
        assert parsed.port == urlparse(server["url"]).port
        assert parsed.fragment.startswith("/books/existing-book?")
        assert parse_qs(parsed.fragment.split("?", 1)[1]) == {
            "tab": ["review"],
            "task": ["content"],
            "reviewPage": ["7"],
        }
        assert calls == [
            (
                "/api/v2/native-workbench",
                {"workspace_id": identity(store)["workspace_id"], "document_id": document},
            )
        ]
        assert not (store.root / ".workbench.json").exists()


def test_directory_handoff_targets_the_same_entry_in_the_online_and_offline_interfaces(
    tmp_path, monkeypatch
):
    store = Store(tmp_path)
    with platform(store) as (server, _):
        monkeypatch.setattr(workbench_lifecycle, "discover_servers", lambda _: [server])
        no_spawn(monkeypatch)
        online = workbench_lifecycle.ensure_workbench(
            store, document_id="a" * 24, entry_id="entry-one"
        )
        assert parse_qs(urlparse(online["url"]).fragment.split("?", 1)[1]) == {
            "tab": ["review"],
            "task": ["navigation"],
            "entry": ["entry-one"],
        }
    monkeypatch.setattr(workbench_lifecycle, "discover_servers", lambda _: [])
    monkeypatch.setattr(
        workbench_lifecycle,
        "running_workbench",
        lambda _: {"url": "http://127.0.0.1:1/?token=local&view=tasks"},
    )
    offline = workbench_lifecycle.ensure_workbench(
        store, document_id="a" * 24, entry_id="entry-one"
    )
    assert parse_qs(urlparse(offline["url"]).query) == {
        "token": ["local"],
        "view": ["navigation"],
        "document": ["a" * 24],
        "entry": ["entry-one"],
    }


@pytest.mark.parametrize("mismatch", ["root", "workspace_id", "available"])
def test_other_workspace_platform_is_not_used_even_with_a_live_local_port(
    tmp_path, monkeypatch, mismatch
):
    store = Store(tmp_path / "native")
    existing = {
        "url": "http://127.0.0.1:1/?token=local",
        "workspace_id": identity(store)["workspace_id"],
    }
    with platform(store) as (server, calls):
        if mismatch == "root":
            server["runtime"]["root"] = str(tmp_path / "copy-with-same-id")
        elif mismatch == "workspace_id":
            server["runtime"]["workspace_id"] = "b" * 32
        else:
            server["available"] = False
        monkeypatch.setattr(
            workbench_lifecycle, "discover_servers", lambda _: [server], raising=False
        )
        monkeypatch.setattr(workbench_lifecycle, "running_workbench", lambda _: existing)
        no_spawn(monkeypatch)
        assert workbench_lifecycle.ensure_workbench(store) == existing
        assert calls == []


def test_platform_rejection_cannot_be_bypassed_by_a_second_local_interface(tmp_path, monkeypatch):
    store = Store(tmp_path)
    with platform(store, status=409) as (server, _):
        monkeypatch.setattr(
            workbench_lifecycle, "discover_servers", lambda _: [server], raising=False
        )
        no_spawn(monkeypatch)
        with pytest.raises(ValueError, match="409"):
            workbench_lifecycle.ensure_workbench(store, document_id="a" * 24)


def test_standalone_registers_bound_server_and_runner_under_the_backup_admission_lock(
    tmp_path, monkeypatch
):
    store = Store(tmp_path)
    locked = False

    @contextmanager
    def admission(*args):
        nonlocal locked
        locked = True
        try:
            yield
        finally:
            locked = False

    class Server:
        review_url = "http://127.0.0.1:1/?token=synthetic"

        def __enter__(self):
            assert locked, "HTTP bind must be admitted before a backup can begin"
            return self

        def __exit__(self, *args):
            pass

        def serve_forever(self):
            assert not locked
            assert (store.root / ".workbench.json").exists()

    @contextmanager
    def runner(*args):
        assert locked, "runner startup must share backup admission"
        yield object()

    import hrs.codex.execution
    import hrs.runtime

    monkeypatch.setattr(hrs.runtime, "lifecycle_lock", admission)
    monkeypatch.setattr(workbench, "create_review_server", lambda *a: Server())
    monkeypatch.setattr(hrs.codex.execution, "create_runner", runner)
    workbench.serve(store, 0)
    assert not (store.root / ".workbench.json").exists()
