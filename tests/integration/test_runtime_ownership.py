"""Owned real subprocesses must stop even while their inference call is blocked."""

import json
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import psutil
import pytest

from hrs import app as launcher
from hrs.config import Settings, diagnostics_root
from hrs.search import service


def wait_until(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            pytest.fail("Owned runtime did not reach its expected state")
        time.sleep(0.05)


def alive(pid):
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


@pytest.fixture
def isolated_runtime(tmp_path, monkeypatch):
    """The actual worker protocol stays intact; only model computation is replaced."""
    helpers = tmp_path / "test-model-runtime"
    helpers.mkdir()
    (helpers / "model_stub.py").write_text(
        """import os, time
from pathlib import Path
class LocalModels:
    def __init__(self, settings): pass
    def embed(self, texts, **options):
        Path(os.environ["HRS_TEST_EMBED_STARTED"]).touch()
        deadline = time.monotonic() + 30
        while not Path(os.environ["HRS_TEST_EMBED_RELEASE"]).exists():
            if time.monotonic() > deadline: raise RuntimeError("Test computation was not cancelled")
            time.sleep(0.05)
        return [[0.1, 0.2] for text in texts]
    def identity(self): return {"fixture": True}
    def unload(self): Path(os.environ["HRS_TEST_UNLOADED"]).touch()
""",
        "utf-8",
    )
    (helpers / "sitecustomize.py").write_text(
        """import subprocess, sys, types
from model_stub import LocalModels
module = types.ModuleType("hrs.search.models")
module.LocalModels = LocalModels
sys.modules["hrs.search.models"] = module
previous = subprocess.check_output
def output(command, *args, **options):
    if command[0] == "nvidia-smi": return "24576"
    return previous(command, *args, **options)
subprocess.check_output = output
""",
        "utf-8",
    )
    monkeypatch.setenv("PYTHONPATH", str(helpers))
    monkeypatch.setenv("HRS_TEST_EMBED_STARTED", str(tmp_path / "started"))
    monkeypatch.setenv("HRS_TEST_EMBED_RELEASE", str(tmp_path / "release"))
    monkeypatch.setenv("HRS_TEST_UNLOADED", str(tmp_path / "unloaded"))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    settings = Settings(
        project_root=tmp_path,
        cache_root=tmp_path / "cache",
        codex_root=tmp_path / "native",
        database_url="postgresql+psycopg://unused@127.0.0.1/unused",
        s3_endpoint="http://127.0.0.1:1",
        s3_bucket="unused",
        s3_access_key="unused",
        s3_secret_key="unused",
        retrieval_endpoint=f"http://127.0.0.1:{port}/retrieval",
    )
    return settings


def test_normal_owned_shutdown_reaps_busy_worker_and_preserves_unrelated_process(isolated_runtime):
    settings = isolated_runtime
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    request = None
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with service.retrieval_service(settings) as running:
                broker_pid = running["pid"]
                assert service.live_brokers(settings.project_root)[0]["pid"] == broker_pid
                request = pool.submit(
                    httpx.post,
                    settings.retrieval_endpoint,
                    json={"operation": "embed", "texts": ["source"]},
                    timeout=15,
                )
                wait_until(lambda: (settings.project_root / "started").exists())
                worker_pid = service.broker_health(settings.retrieval_endpoint)["worker_pid"]
                assert alive(broker_pid) and alive(worker_pid)
            wait_until(lambda: not alive(worker_pid))
            assert not alive(broker_pid)
            assert unrelated.poll() is None
            try:
                response = request.result(timeout=5)
                assert response.status_code == 503
            except httpx.HTTPError:
                pass
        assert service.live_brokers(settings.project_root) == []
        assert not list(diagnostics_root(settings.project_root).glob("*.stop"))
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=5)


def test_app_assembly_failure_releases_the_actual_managed_broker(isolated_runtime, monkeypatch):
    import hrs.api.main
    import hrs.storage.bootstrap

    settings = isolated_runtime
    settings.web_dist.mkdir(parents=True)
    (settings.web_dist / "index.html").write_text("<html></html>", "utf-8")
    monkeypatch.setattr(launcher, "ROOT", settings.project_root)
    monkeypatch.setattr(launcher, "CONTROL", diagnostics_root(settings.project_root))
    monkeypatch.setattr(launcher, "listening", lambda port: False)
    monkeypatch.setattr(Settings, "load", lambda root: settings)
    monkeypatch.setattr(hrs.storage.bootstrap, "bootstrap", lambda root: None)
    started = []
    previous = service.subprocess.Popen

    def spawn(*args, **kwargs):
        process = previous(*args, **kwargs)
        started.append(process)
        return process

    def fail_app(**options):
        pid = service.broker_health(settings.retrieval_endpoint)["pid"]
        process = psutil.Process(started[0].pid)
        assert pid in {process.pid, *(child.pid for child in process.children(recursive=True))}
        raise RuntimeError("Application assembly fixture failed")

    monkeypatch.setattr(service.subprocess, "Popen", spawn)
    monkeypatch.setattr(hrs.api.main, "create_app", fail_app)
    with pytest.raises(RuntimeError, match="assembly fixture"):
        launcher.command("up", existing_services=True)
    assert len(started) == 1 and started[0].poll() is not None
    assert service.live_brokers(settings.project_root) == []


def test_external_retrieval_never_spawns_or_stops_a_local_broker(isolated_runtime, monkeypatch):
    settings = isolated_runtime.model_copy(update={"retrieval_mode": "external"})
    monkeypatch.setattr(
        service.subprocess,
        "Popen",
        lambda *a, **k: pytest.fail("External service was started locally"),
    )
    monkeypatch.setattr(
        service, "broker_health", lambda endpoint: {"service": service.SERVICE, "pid": 123}
    )
    with service.retrieval_service(settings) as running:
        assert running == {"owned": False, "health": {"service": service.SERVICE, "pid": 123}}


def test_existing_service_connections_leave_its_owner_in_control(isolated_runtime):
    settings = isolated_runtime
    with service.retrieval_service(settings) as owner:
        with service.retrieval_service(settings) as reused:
            assert reused["owned"] is False and reused["health"]["pid"] == owner["pid"]
        external = settings.model_copy(update={"retrieval_mode": "external"})
        with service.retrieval_service(external) as connection:
            assert connection["owned"] is False and connection["health"]["pid"] == owner["pid"]
        assert alive(owner["pid"])
        assert len(service.live_brokers(settings.project_root)) == 1
    assert not alive(owner["pid"])


def test_idle_worker_obeys_shutdown_and_unloads_normally(isolated_runtime):
    settings = isolated_runtime
    (settings.project_root / "release").touch()
    with service.retrieval_service(settings):
        response = httpx.post(
            settings.retrieval_endpoint,
            json={"operation": "embed", "texts": ["source"]},
            timeout=10,
        )
        assert response.status_code == 200
        worker_pid = service.broker_health(settings.retrieval_endpoint)["worker_pid"]
    assert not alive(worker_pid)
    assert (settings.project_root / "unloaded").exists()


def test_broker_hard_crash_cannot_leave_a_busy_inference_worker(isolated_runtime):
    settings = isolated_runtime
    worker_pid = None
    with ThreadPoolExecutor(max_workers=1) as pool:
        with service.retrieval_service(settings) as running:
            request = pool.submit(
                httpx.post,
                settings.retrieval_endpoint,
                json={"operation": "embed", "texts": ["source"]},
                timeout=15,
            )
            wait_until(lambda: (settings.project_root / "started").exists())
            worker_pid = service.broker_health(settings.retrieval_endpoint)["worker_pid"]
            broker = psutil.Process(running["pid"])
            assert service.live_brokers(settings.project_root)[0]["pid"] == broker.pid
            broker.kill()
            broker.wait(timeout=5)
            wait_until(lambda: not alive(worker_pid))
            try:
                request.result(timeout=5)
            except httpx.HTTPError:
                pass
    assert service.live_brokers(settings.project_root) == []
    assert not list((diagnostics_root(settings.project_root) / "brokers").glob("*.json"))


def test_parent_death_closes_the_busy_managed_broker(isolated_runtime):
    settings = isolated_runtime
    record = settings.project_root / "parent-session.json"
    script = settings.project_root / "owned-parent.py"
    script.write_text(
        "import json,time,os\nfrom pathlib import Path\nfrom hrs.config import Settings\n"
        "from hrs.search.service import retrieval_service\n"
        f"settings=Settings.model_validate({settings.model_dump(mode='json')!r})\n"
        "with retrieval_service(settings) as running:\n"
        f" Path({str(record)!r}).write_text(json.dumps(dict(running,owner_pid=os.getpid())),'utf-8')\n"
        " time.sleep(60)\n",
        "utf-8",
    )
    # SecretStr serialization is intentionally masked; no database/object-store operation is used.
    parent = subprocess.Popen([sys.executable, str(script)])
    broker_pid = worker_pid = owner_pid = None
    try:
        wait_until(record.exists)
        receipt = json.loads(record.read_text("utf-8"))
        broker_pid, owner_pid = receipt["pid"], receipt["owner_pid"]
        owner = psutil.Process(parent.pid)
        assert owner_pid in {owner.pid, *(child.pid for child in owner.children(recursive=True))}
        with ThreadPoolExecutor(max_workers=1) as pool:
            request = pool.submit(
                httpx.post,
                settings.retrieval_endpoint,
                json={"operation": "embed", "texts": ["source"]},
                timeout=15,
            )
            wait_until(lambda: (settings.project_root / "started").exists())
            worker_pid = service.broker_health(settings.retrieval_endpoint)["worker_pid"]
            psutil.Process(owner_pid).kill()
            parent.wait(timeout=5)
            wait_until(lambda: not alive(broker_pid) and not alive(worker_pid))
            try:
                request.result(timeout=5)
            except httpx.HTTPError:
                pass
        assert service.live_brokers(settings.project_root) == []
    finally:
        if parent.poll() is None:
            parent.terminate()
            parent.wait(timeout=5)
        # Clean only identities captured from this test, even if an assertion failed.
        for pid in (worker_pid, broker_pid, owner_pid):
            if pid and alive(pid):
                process = psutil.Process(pid)
                process.kill()
                process.wait(timeout=5)
