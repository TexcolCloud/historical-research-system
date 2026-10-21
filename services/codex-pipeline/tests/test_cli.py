"""Coordinator checks use a process stub; they make no model requests."""

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from hrs_codex import cli
from hrs_codex.store import Store


def prepared_store(tmp_path):
    source = tmp_path / "source.png"
    source.write_bytes(b"retained original")
    store = Store(tmp_path / "external evidence with spaces")
    document = store.register(source, [{"page": 1, "image": str(source)}])
    return store, document["id"]


class ProcessStub:
    def __init__(self, arguments, *, returncode=0, error=None, **options):
        self.arguments, self.options = arguments, options
        self.returncode, self.error, self.pid = returncode, error, 12345
        self.prompt = None

    def communicate(self, *, input):
        self.prompt = input
        self.options["stdout"].write('{"type":"thread.started"}\n')
        if self.error:
            raise self.error
        return None, None


@pytest.mark.parametrize("model", [None, "user-selected-model"])
def test_runner_native_command_paths_model_inheritance_and_receipt(tmp_path, monkeypatch, model):
    store, document_id = prepared_store(tmp_path)
    monkeypatch.setattr(cli, "PROJECT", tmp_path / "repository with spaces")
    monkeypatch.setattr(cli.shutil, "which", lambda name: "codex executable with spaces")
    processes = []

    def launch(arguments, **options):
        process = ProcessStub(arguments, **options)
        processes.append(process)
        return process

    monkeypatch.setattr(cli.subprocess, "Popen", launch)
    receipt = cli.run_codex(store, document_id, model=model)
    process = processes[0]
    assert process.arguments[0] == "codex executable with spaces"
    assert process.arguments[-1] == "-"
    assert process.arguments[process.arguments.index("--cd") + 1] == str(cli.PROJECT)
    assert process.arguments[process.arguments.index("--add-dir") + 1] == str(store.root)
    assert process.options["cwd"] == cli.PROJECT
    assert process.options["stdin"] == subprocess.PIPE
    assert "native Codex subagents" in process.prompt
    assert document_id in process.prompt
    assert any(
        "historical_research.command=" in argument and sys.executable.replace("\\", "\\\\") in argument
        for argument in process.arguments
    )
    if model is None:
        assert "--model" not in process.arguments
    else:
        assert process.arguments[process.arguments.index("--model") + 1] == model
    logs = Path(receipt["logs"])
    assert json.loads((logs / "request.json").read_text(encoding="utf-8"))["model"] == model
    assert json.loads((logs / "receipt.json").read_text(encoding="utf-8"))["returncode"] == 0
    assert (logs / "events.jsonl").read_text(encoding="utf-8").startswith('{"type":')


def test_runner_nonzero_exit_keeps_request_events_and_failure_receipt(tmp_path, monkeypatch):
    store, document_id = prepared_store(tmp_path)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "codex")
    monkeypatch.setattr(
        cli.subprocess, "Popen", lambda args, **options: ProcessStub(args, returncode=7, **options)
    )
    with pytest.raises(RuntimeError, match="Codex exited 7"):
        cli.run_codex(store, document_id)
    logs = next((store.root / "runs" / document_id).iterdir())
    receipt = json.loads((logs / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["returncode"] == 7
    assert receipt["status"]["pages"][0]["state"] == "awaiting_ocr"
    assert (logs / "request.json").exists()
    assert (logs / "events.jsonl").exists()


def test_runner_launch_failure_still_has_receipt(tmp_path, monkeypatch):
    store, document_id = prepared_store(tmp_path)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "codex")

    def fail(*args, **kwargs):
        raise OSError("launch failed")

    monkeypatch.setattr(cli.subprocess, "Popen", fail)
    with pytest.raises(OSError, match="launch failed"):
        cli.run_codex(store, document_id)
    logs = next((store.root / "runs" / document_id).iterdir())
    receipt = json.loads((logs / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["returncode"] is None
    assert receipt["error"] == {"type": "OSError", "message": "launch failed"}


def test_runner_interrupt_cleans_owned_process_and_keeps_receipt(tmp_path, monkeypatch):
    store, document_id = prepared_store(tmp_path)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "codex")
    monkeypatch.setattr(
        cli.subprocess,
        "Popen",
        lambda args, **options: ProcessStub(args, error=KeyboardInterrupt(), **options),
    )
    stopped = []
    monkeypatch.setattr(cli, "stop_owned_process", lambda process: stopped.append(process.pid))
    with pytest.raises(KeyboardInterrupt):
        cli.run_codex(store, document_id)
    assert stopped == [12345]
    logs = next((store.root / "runs" / document_id).iterdir())
    assert (
        json.loads((logs / "receipt.json").read_text(encoding="utf-8"))["error"]["type"]
        == "KeyboardInterrupt"
    )


def test_run_pending_is_exit_two_not_success(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["hrs-codex", "--root", str(tmp_path), "run", "document-id"])
    monkeypatch.setattr(cli, "run_codex", lambda *args, **kwargs: {"status": {"state": "pending"}})
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2
    assert json.loads(capsys.readouterr().out)["status"]["state"] == "pending"


@pytest.mark.parametrize("available", [True, False])
def test_doctor_reports_missing_auth_or_cli(tmp_path, monkeypatch, capsys, available):
    monkeypatch.setattr(sys, "argv", ["hrs-codex", "--root", str(tmp_path), "doctor"])
    monkeypatch.setattr(cli.shutil, "which", lambda name: "codex" if available else None)
    monkeypatch.setattr(cli.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=1))
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 1
    assert json.loads(capsys.readouterr().out)["logged_in"] is False


def test_cli_reports_bad_source_without_traceback(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        sys, "argv", ["hrs-codex", "--root", str(tmp_path), "prepare", str(tmp_path / "missing.pdf")]
    )
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 1
    streams = capsys.readouterr()
    assert not streams.out
    assert json.loads(streams.err)["error"] == "FileNotFoundError"


def test_interruption_cleanup_is_bounded_and_targets_only_owned_tree(monkeypatch):
    calls, waits = [], []

    def wait(*, timeout):
        waits.append(timeout)
        if len(waits) == 1:
            raise subprocess.TimeoutExpired("owned-codex", timeout)
        return 0

    process = SimpleNamespace(pid=12345, poll=lambda: None, wait=wait, kill=lambda: calls.append("kill"))
    monkeypatch.setattr(cli.subprocess, "run", lambda args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.setattr(cli.os, "killpg", lambda pid, signum: calls.append((pid, signum)), raising=False)
    cli.stop_owned_process(process)
    assert all(0 < timeout <= 10 for timeout in waits)
    if cli.os.name == "nt":
        assert calls[0][0] == ["taskkill", "/PID", "12345", "/T", "/F"]
        assert calls[0][1]["timeout"] == 15
        assert "kill" in calls
    else:
        assert calls[:2] == [(12345, cli.signal.SIGTERM), (12345, cli.signal.SIGKILL)]
