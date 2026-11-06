"""BGE process and HTTP contracts without a GPU, model weights or listening server."""

import io
import json
import threading
from uuid import uuid4

import pytest

from hrs.search import broker as module


class Pipe:
    def __init__(self):
        self.sent = []
        self.closed = False
        self.before_poll = None

    def send(self, body):
        assert not self.closed
        self.sent.append(body)

    def poll(self, timeout):
        if self.before_poll:
            callback, self.before_poll = self.before_poll, None
            callback()
            return False
        return True

    def recv(self):
        body = self.sent[-1]
        result = {"vectors": [[0.1, 0.2]]} if body["operation"] == "embed" else {"scores": [0.8]}
        return {"result": result, "seconds": 0.01}

    def close(self):
        self.closed = True


class Process:
    def __init__(self, **options):
        self.options = options
        self.alive = False
        self.pid = None
        self.actions = []
        self.stubborn = False

    def start(self):
        self.alive = True
        self.pid = 12345
        self.actions.append("start")

    def is_alive(self):
        return self.alive

    def terminate(self):
        self.actions.append("terminate")
        if not self.stubborn:
            self.alive = False

    def join(self, timeout):
        self.actions.append(("join", timeout))

    def kill(self):
        self.actions.append("kill")
        self.alive = False

    def close(self):
        self.actions.append("close")


class Context:
    def __init__(self):
        self.parent, self.child = Pipe(), Pipe()
        self.processes = []

    def Pipe(self):
        return self.parent, self.child

    def Process(self, **options):
        process = Process(**options)
        self.processes.append(process)
        return process


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "load_environment", lambda root: {})
    monkeypatch.setattr(module, "free_gib", lambda: 20)
    context = Context()
    modes = []

    def get_context(mode):
        modes.append(mode)
        return context

    monkeypatch.setattr(module.multiprocessing, "get_context", get_context)
    return module.RetrievalBroker(tmp_path), context, modes


@pytest.mark.parametrize(
    "body",
    [
        {"operation": "vision", "texts": ["source"]},
        {"operation": "embed", "texts": []},
        {"operation": "embed", "texts": ["source"] * 65},
        {"operation": "embed", "texts": [object()]},
        {"operation": "embed", "texts": ["x" * 40001]},
        {"operation": "rerank", "texts": ["source"], "query": ""},
    ],
)
def test_invalid_batches_never_start_a_worker(isolated, body):
    broker, context, modes = isolated
    with pytest.raises(ValueError):
        broker.retrieve(body)
    assert modes == []
    assert context.processes == []
    assert broker.process is None


def test_embed_and_rerank_reuse_one_owned_spawned_bge_worker(isolated):
    broker, context, modes = isolated
    assert broker.retrieve({"operation": "embed", "texts": ["source"], "query": True}) == {
        "vectors": [[0.1, 0.2]],
    }
    assert broker.retrieve({"operation": "rerank", "texts": ["source"], "query": "question"}) == {
        "scores": [0.8],
    }
    assert modes == ["spawn"]
    process = context.processes[0]
    assert process.options == {
        "target": module.retrieval_worker,
        "args": (context.child, broker.root / "models/document-retrieval"),
        "daemon": True,
    }
    assert context.child.closed
    assert [item["operation"] for item in context.parent.sent] == ["embed", "rerank"]
    assert broker.active is None
    events = [
        json.loads(line) for line in (broker.folder / "events.jsonl").read_text().splitlines()
    ]
    assert [event["operation"] for event in events] == ["embed", "rerank"]
    broker.unload()


def test_spawn_failure_retains_the_original_error_and_closes_both_pipes(isolated, monkeypatch):
    broker, context, _ = isolated
    failure = OSError("spawn unavailable")

    def fail_start(process):
        process.actions.append("start")
        raise failure

    monkeypatch.setattr(Process, "start", fail_start)
    with pytest.raises(OSError) as raised:
        broker.retrieve({"operation": "embed", "texts": ["source"]})
    assert raised.value is failure
    assert context.processes[0].actions == ["start", "close"]
    assert context.parent.closed and context.child.closed
    assert broker.process is broker.pipe is broker.active is None


@pytest.mark.parametrize("relative", [False, True])
def test_spawn_uses_an_explicit_existing_model_location(isolated, tmp_path, monkeypatch, relative):
    _, context, _ = isolated
    selected = tmp_path / "pinned model weights"
    selected.mkdir()
    outside = tmp_path / "another cwd"
    outside.mkdir()
    monkeypatch.chdir(outside)
    configured = str(selected.relative_to(tmp_path) if relative else selected)
    monkeypatch.setattr(
        module, "load_environment", lambda root: {"PLATFORM_MODELS_ROOT": configured}
    )
    broker = module.RetrievalBroker(tmp_path)
    broker.retrieve({"operation": "embed", "texts": ["source"]})
    assert context.processes[0].options["args"][1] == selected
    broker.unload()


def test_cancellation_and_shutdown_never_touch_an_unrelated_process(isolated):
    broker, context, _ = isolated
    unrelated = Process()
    unrelated.start()
    request_id, task_id, other_request = str(uuid4()), str(uuid4()), str(uuid4())

    def cancel_during_wait():
        process = context.processes[0]
        broker.cancel([other_request])
        assert process.is_alive()
        broker.cancel([request_id])

    context.parent.before_poll = cancel_during_wait
    with pytest.raises(RuntimeError, match="cancelled"):
        broker.retrieve(
            {
                "operation": "embed",
                "texts": ["source"],
                "task_id": task_id,
                "request_id": request_id,
            }
        )
    owned = context.processes[0]
    assert owned.actions == ["start", "terminate", ("join", 20), "close"]
    assert context.parent.closed
    assert broker.process is broker.pipe is broker.active is None
    assert unrelated.is_alive() and unrelated.actions == ["start"]
    broker.closing.set()
    broker.unload()
    with pytest.raises(RuntimeError, match="cancelled"):
        broker.retrieve({"operation": "embed", "texts": ["source"]})
    assert len(context.processes) == 1


def test_shutdown_escalates_only_its_owned_worker_and_is_idempotent(isolated):
    broker, context, _ = isolated
    broker.retrieve({"operation": "embed", "texts": ["source"]})
    owned = context.processes[0]
    owned.stubborn = True
    broker.closing.set()
    broker.unload()
    broker.unload()
    assert owned.actions == [
        "start",
        ("join", 2),
        "terminate",
        ("join", 20),
        "kill",
        ("join", 10),
        "close",
    ]
    assert context.parent.closed


def test_http_exposes_only_bge_health_retrieval_and_cancellation(isolated, monkeypatch):
    broker, context, _ = isolated
    monkeypatch.setattr(module, "RetrievalBroker", lambda: broker)
    handlers, replies, servers = {}, [], []
    monkeypatch.setattr(
        module.signal, "signal", lambda number, callback: handlers.setdefault(number, callback)
    )

    class Server:
        def __init__(self, address, handler):
            assert address == ("127.0.0.1", 18160)
            self.handler, self.stopped, self.closed = handler, threading.Event(), False
            servers.append(self)

        def request(self, method, path, body=None):
            request = self.handler.__new__(self.handler)
            request.path = path
            raw = json.dumps(body or {}).encode()
            request.headers = {"Content-Length": str(len(raw))}
            request.rfile = io.BytesIO(raw)
            request.reply = lambda status, value: replies.append((path, status, value))
            getattr(request, "do_" + method)()

        def serve_forever(self):
            self.request("GET", "/health")
            self.request("POST", "/retrieval", {"operation": "embed", "texts": ["source"]})
            self.request("POST", "/cancel-retrieval", {"request_ids": [str(uuid4())]})
            self.request("POST", "/cancel-tasks", {"task_ids": [str(uuid4())]})
            self.request("POST", "/v1/chat/completions")
            self.request("POST", "/ocr")
            self.request("GET", "/models")
            handlers[module.signal.SIGTERM]()
            assert self.stopped.wait(1)

        def shutdown(self):
            self.stopped.set()

        def server_close(self):
            self.closed = True

    monkeypatch.setattr(module, "ThreadingHTTPServer", Server)
    module.main()
    assert [(path, status) for path, status, _ in replies] == [
        ("/health", 200),
        ("/retrieval", 200),
        ("/cancel-retrieval", 200),
        ("/cancel-tasks", 200),
        ("/v1/chat/completions", 404),
        ("/ocr", 404),
        ("/models", 404),
    ]
    assert replies[0][2]["model"] == "BGE-M3"
    assert broker.closing.is_set() and servers[0].closed
    assert context.processes[0].actions[-1] == "close"
