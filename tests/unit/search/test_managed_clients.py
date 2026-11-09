"""Shared host ownership depends on registered live process identities."""

import os
from types import SimpleNamespace
from uuid import uuid4

import psutil
import pytest

from hrs.search import broker as module
from hrs.search import service


@pytest.fixture
def registry(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "load_environment", lambda root: {})
    alive = {(11, 1.0), (22, 2.0)}
    monkeypatch.setattr(module, "process_alive", lambda pid, created: (pid, created) in alive)
    broker = module.RetrievalBroker(tmp_path)
    broker.managed = True
    broker.endpoint = "http://127.0.0.1:1/retrieval"

    def request(pid=11, created=1.0, **values):
        return {
            "action": "acquire",
            "client_id": uuid4().hex,
            "pid": pid,
            "created": created,
            "instance_id": broker.instance_id,
            "project_root": str(broker.root),
            "models_root": str(broker.models_root),
            "endpoint": broker.endpoint,
            **values,
        }

    return broker, alive, request


def test_each_context_has_its_own_idempotent_lease_and_last_release_closes(registry):
    broker, _, request = registry
    first, second = request(), request()
    assert broker.client(first)["clients"] == 1
    assert broker.client(first)["clients"] == 1
    assert broker.client(second)["clients"] == 2
    assert broker.client({**first, "action": "release"})["clients"] == 1
    assert broker.client({**first, "action": "release"})["clients"] == 1
    assert not broker.closing.is_set()
    assert broker.client({**second, "action": "release"})["closing"] is True
    with pytest.raises(RuntimeError, match="no longer active"):
        broker.client(request())


def test_original_owner_crash_does_not_close_a_live_borrower(registry):
    broker, alive, request = registry
    broker.client(request())
    broker.client(request(22, 2.0))
    alive.remove((11, 1.0))
    assert broker.clients_finished() == (True, False)
    assert list(broker.clients.values()) == [(22, 2.0)]
    alive.clear()
    assert broker.clients_finished() == (True, True)


def test_pid_reuse_does_not_keep_a_stale_client_alive(registry):
    broker, alive, request = registry
    broker.client(request())
    alive.remove((11, 1.0))
    alive.add((11, 9.0))
    assert broker.clients_finished() == (True, True)
    assert broker.clients == {}


def test_changed_client_identity_cannot_release_another_context(registry):
    broker, _, request = registry
    first = request()
    broker.client(first)
    with pytest.raises(ValueError, match="identity changed"):
        broker.client({**first, "action": "release", "pid": 22, "created": 2.0})
    assert not broker.closing.is_set() and len(broker.clients) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"instance_id": "old-instance"},
        {"endpoint": "http://127.0.0.1:2/retrieval"},
        {"project_root": "different-project"},
        {"models_root": "different-models"},
        {"pid": True},
        {"created": float("nan")},
        {"pid": 999},
    ],
)
def test_incompatible_or_dead_clients_never_gain_ownership(registry, change):
    broker, _, request = registry
    with pytest.raises((ValueError, RuntimeError)):
        broker.client(request(**change))
    assert broker.clients == {} and not broker.clients_started


def test_unmanaged_external_broker_does_not_accept_managed_clients(registry):
    broker, _, request = registry
    broker.managed = False
    with pytest.raises(ValueError, match="Incompatible"):
        broker.client(request())


@pytest.fixture
def configured(tmp_path, monkeypatch):
    settings = SimpleNamespace(
        project_root=tmp_path,
        models_root=tmp_path / "models",
        retrieval_device="cuda",
        retrieval_mode="managed",
        retrieval_endpoint="http://127.0.0.1:1/retrieval",
    )
    health = {
        "client_protocol": service.CLIENT_PROTOCOL,
        "managed": True,
        "instance_id": uuid4().hex,
        "project_root": str(settings.project_root),
        "models_root": str(settings.models_root),
        "endpoint": settings.retrieval_endpoint,
        "pid": os.getpid(),
        "process_create_time": psutil.Process().create_time(),
    }
    monkeypatch.setattr(service, "broker_health", lambda endpoint: health)
    monkeypatch.setattr(
        service.subprocess, "Popen", lambda *a, **k: pytest.fail("Unexpected spawn")
    )
    monkeypatch.setattr(
        service, "_kill_owned_tree", lambda *a: pytest.fail("Foreign process killed")
    )
    monkeypatch.setattr(service, "_client_request", lambda *a: pytest.fail("Invalid registration"))
    return settings, health


@pytest.mark.parametrize(
    "change",
    [
        {"client_protocol": 0},
        {"managed": False},
        {"project_root": "different-project"},
        {"models_root": "different-models"},
        {"endpoint": "http://127.0.0.1:2/retrieval"},
        {"process_create_time": 0},
        {"instance_id": None},
    ],
)
def test_occupied_incompatible_endpoint_is_never_adopted_or_killed(configured, change):
    settings, health = configured
    health.update(change)
    with pytest.raises(RuntimeError, match="incompatible"):
        with service.retrieval_service(settings):
            pytest.fail("Incompatible host was adopted")


def test_cpu_never_contacts_registers_or_starts_gpu(configured, monkeypatch):
    settings, _ = configured
    settings.retrieval_device = "cpu"
    monkeypatch.setattr(service, "broker_health", lambda *a: pytest.fail("GPU health called"))
    with service.retrieval_service(settings) as result:
        assert result is None


def test_lost_acquire_response_releases_the_same_lease_before_propagating(
    registry, configured, monkeypatch
):
    broker, alive, request = registry
    settings, health = configured
    settings.models_root = broker.models_root
    settings.retrieval_endpoint = broker.endpoint
    health.update(
        models_root=str(broker.models_root),
        endpoint=broker.endpoint,
        instance_id=broker.instance_id,
    )
    process = psutil.Process()
    alive.add((process.pid, process.create_time()))
    calls = []

    def request_with_lost_response(settings, running, client, action):
        calls.append((client["client_id"], action))
        result = broker.client(request(**client, action=action))
        if action == "acquire":
            raise OSError("The host accepted the lease but its response was lost")
        return result

    monkeypatch.setattr(service, "_client_request", request_with_lost_response)
    # The synthetic broker has no process to wait for after its last lease closes.
    monkeypatch.setattr(service, "process_alive", lambda *a: not broker.closing.is_set())
    with pytest.raises(OSError, match="response was lost"):
        with service.retrieval_service(settings):
            pytest.fail("An unacknowledged acquisition cannot serve the app")
    assert [action for _, action in calls] == ["acquire", "release"]
    assert calls[0][0] == calls[1][0]
    assert broker.clients == {} and broker.closing.is_set()


def test_process_identity_rejects_reused_pid_and_zombie(monkeypatch):
    process = SimpleNamespace(create_time=lambda: 2.0, status=lambda: psutil.STATUS_RUNNING)
    monkeypatch.setattr(service.psutil, "Process", lambda pid: process)
    assert service.process_alive(11, 2.0)
    assert not service.process_alive(11, 1.0)
    process.status = lambda: psutil.STATUS_ZOMBIE
    assert not service.process_alive(11, 2.0)
