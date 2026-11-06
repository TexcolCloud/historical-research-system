"""Coordinator checks use a process stub; they make no model requests."""

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from hrs.codex import cli, execution
from hrs.processing.store import Store


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
    monkeypatch.setattr(execution, "PROJECT", tmp_path / "repository with spaces")
    monkeypatch.setattr(execution.shutil, "which", lambda name: "codex executable with spaces")
    processes = []

    def launch(arguments, **options):
        process = ProcessStub(arguments, **options)
        processes.append(process)
        return process

    monkeypatch.setattr(execution.subprocess, "Popen", launch)
    receipt = execution.run_codex(store, document_id, model=model)
    process = processes[0]
    assert process.arguments[0] == "codex executable with spaces"
    assert process.arguments[-1] == "-"
    assert process.arguments[process.arguments.index("--cd") + 1] == str(store.root / "runtime")
    assert process.arguments[process.arguments.index("--add-dir") + 1] == str(store.root)
    assert process.options["cwd"] == store.root / "runtime"
    assert process.options["stdin"] == subprocess.PIPE
    assert "features.shell_tool=false" in process.arguments
    assert "native Codex subagents" in process.prompt
    assert document_id in process.prompt
    assert any(
        "historical_research.command=" in argument
        and sys.executable.replace("\\", "\\\\") in argument
        for argument in process.arguments
    )
    mcp_arguments = next(
        argument.split("=", 1)[1]
        for argument in process.arguments
        if argument.startswith("mcp_servers.historical_research.args=")
    )
    assert json.loads(mcp_arguments) == ["-m", "hrs", "--root", str(store.root), "mcp"]
    if model is None:
        assert process.arguments[process.arguments.index("--model") + 1] == "gpt-6.1-sol"
    else:
        assert process.arguments[process.arguments.index("--model") + 1] == model
    logs = Path(receipt["logs"])
    assert json.loads((logs / "request.json").read_text(encoding="utf-8"))["model"] == model
    assert json.loads((logs / "receipt.json").read_text(encoding="utf-8"))["returncode"] == 0
    assert (logs / "events.jsonl").read_text(encoding="utf-8").startswith('{"type":')


def test_runtime_loads_project_skill_without_requiring_a_worker_shell(tmp_path, monkeypatch):
    store, document_id = prepared_store(tmp_path)
    project = tmp_path / "project"
    skill = project / ".agents/skills/historical-research/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("Synthetic skill content for runtime bootstrap", encoding="utf-8")
    monkeypatch.setattr(execution, "PROJECT", project)
    monkeypatch.setattr(execution.shutil, "which", lambda name: "codex")
    launched = []

    def launch(args, **options):
        process = ProcessStub(args, **options)
        launched.append(process)
        return process

    monkeypatch.setattr(execution.subprocess, "Popen", launch)
    execution.run_codex(store, document_id)
    # Runtime now gets narrow stage packets; importing a development skill is retired.
    assert skill.read_text("utf-8") not in launched[0].prompt
    assert "do not import or enqueue yourself again" in launched[0].prompt.lower()
    assert "--ignore-user-config" in launched[0].arguments
    assert "dispatch_work" in launched[0].prompt
    assert len(launched[0].prompt) < 4000


def test_runner_nonzero_exit_keeps_request_events_and_failure_receipt(tmp_path, monkeypatch):
    store, document_id = prepared_store(tmp_path)
    monkeypatch.setattr(execution.shutil, "which", lambda name: "codex")
    monkeypatch.setattr(
        execution.subprocess,
        "Popen",
        lambda args, **options: ProcessStub(args, returncode=7, **options),
    )
    with pytest.raises(RuntimeError, match="Codex exited 7"):
        execution.run_codex(store, document_id)
    logs = next((store.root / "runs" / document_id).iterdir())
    receipt = json.loads((logs / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["returncode"] == 7
    assert receipt["status"]["pages"][0]["state"] == "awaiting_ocr"
    assert (logs / "request.json").exists()
    assert (logs / "events.jsonl").exists()


def test_runner_launch_failure_still_has_receipt(tmp_path, monkeypatch):
    store, document_id = prepared_store(tmp_path)
    monkeypatch.setattr(execution.shutil, "which", lambda name: "codex")

    def fail(*args, **kwargs):
        raise OSError("launch failed")

    monkeypatch.setattr(execution.subprocess, "Popen", fail)
    with pytest.raises(OSError, match="launch failed"):
        execution.run_codex(store, document_id)
    logs = next((store.root / "runs" / document_id).iterdir())
    receipt = json.loads((logs / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["returncode"] is None
    assert receipt["error"] == {"type": "OSError", "message": "launch failed"}


def test_runner_interrupt_cleans_owned_process_and_keeps_receipt(tmp_path, monkeypatch):
    store, document_id = prepared_store(tmp_path)
    monkeypatch.setattr(execution.shutil, "which", lambda name: "codex")
    monkeypatch.setattr(
        execution.subprocess,
        "Popen",
        lambda args, **options: ProcessStub(args, error=KeyboardInterrupt(), **options),
    )
    stopped = []
    monkeypatch.setattr(
        execution, "stop_owned_process", lambda process: stopped.append(process.pid)
    )
    with pytest.raises(KeyboardInterrupt):
        execution.run_codex(store, document_id)
    assert stopped == [12345]
    logs = next((store.root / "runs" / document_id).iterdir())
    assert (
        json.loads((logs / "receipt.json").read_text(encoding="utf-8"))["error"]["type"]
        == "KeyboardInterrupt"
    )


def test_run_pending_is_exit_two_not_success(tmp_path, monkeypatch, capsys):
    store, doc_id = prepared_store(tmp_path)
    monkeypatch.setattr(sys, "argv", ["hrs-codex", "--root", str(store.root), "run", doc_id])

    def pending(s, document_id, **kwargs):
        for stage, worker, result in [
            ("ocr", "synthetic-ocr", {"markdown": "Synthetic unclear page"}),
            (
                "review",
                "synthetic-review",
                {"verdict": "pending", "issues": ["Synthetic source uncertainty"]},
            ),
        ]:
            task = s.claim_task(document_id, stage, worker)
            s.submit_task(
                document_id,
                task["task_id"],
                task["lease_token"],
                {"input_sha256": task["input_sha256"], **result},
            )

    monkeypatch.setattr(cli, "run_codex", pending)
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2
    assert json.loads(capsys.readouterr().out)["status"]["state"] == "pending"


def test_review_cli_opens_selected_workspace_without_starting_codex(tmp_path, monkeypatch):
    from hrs.codex import workbench as human_review

    calls = []
    monkeypatch.setattr(human_review, "serve", lambda store, port: calls.append((store.root, port)))
    monkeypatch.setattr(
        sys, "argv", ["hrs-codex", "--root", str(tmp_path), "review", "--port", "0"]
    )
    cli.main()
    assert calls == [(tmp_path.resolve(), 0)]


@pytest.mark.parametrize("available", [True, False])
def test_doctor_reports_missing_auth_or_cli(tmp_path, monkeypatch, capsys, available):
    monkeypatch.setattr(sys, "argv", ["hrs-codex", "--root", str(tmp_path), "doctor"])
    monkeypatch.setattr(execution.shutil, "which", lambda name: "codex" if available else None)
    monkeypatch.setattr(
        execution.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=1)
    )
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 1
    assert json.loads(capsys.readouterr().out)["logged_in"] is False


def test_cli_reports_bad_source_without_traceback(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        sys,
        "argv",
        ["hrs-codex", "--root", str(tmp_path), "prepare", str(tmp_path / "missing.pdf")],
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

    process = SimpleNamespace(
        pid=12345, poll=lambda: None, wait=wait, kill=lambda: calls.append("kill")
    )
    monkeypatch.setattr(
        execution.subprocess, "run", lambda args, **kwargs: calls.append((args, kwargs))
    )
    monkeypatch.setattr(
        execution.os, "killpg", lambda pid, signum: calls.append((pid, signum)), raising=False
    )
    execution.stop_owned_process(process)
    assert all(0 < timeout <= 10 for timeout in waits)
    if execution.os.name == "nt":
        assert calls[0][0] == ["taskkill", "/PID", "12345", "/T", "/F"]
        assert calls[0][1]["timeout"] == 15
        assert "kill" in calls
    else:
        assert calls[:2] == [(12345, execution.signal.SIGTERM), (12345, execution.signal.SIGKILL)]
