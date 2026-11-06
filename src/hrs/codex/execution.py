"""Owned Codex subprocess execution, independent of CLI and HTTP adapters."""

import json
import os
import shutil
import signal
import subprocess
import threading
import time
from datetime import UTC, datetime

from hrs.codex.runtime_events import RuntimeEvents, RuntimeFailure
from hrs.config import project_root
from hrs.processing.store import Store

PROJECT = project_root()


def _observe_events(path, observer, finished, errors, check_stop, stop_process):
    """Surface supervisor crashes to the runner instead of silently losing supervision."""
    try:
        _tail_events(path, observer, finished, errors, check_stop, stop_process)
    except Exception as error:  # noqa: BLE001 - a thread failure must reach the durable runner
        errors.append({"type": type(error).__name__, "message": str(error), "fatal": True})
        observer.terminal_failure = observer.last_error = {
            "code": "tool_error",
            "reason": "observer_failed",
            "model": observer.model,
            "scope": "model",
            "retry_after": None,
            "event_id": f"{observer.run_id}:observer_failed",
            "message": f"Runtime observer failed ({type(error).__name__}); checkpoints retained",
            "evidence": {
                "file": str(path),
                "line": observer.line_number,
                "exception_type": type(error).__name__,
            },
        }
        # Loss of local supervision is an executor failure, not model capacity or source doubt.
        # Only this execution's process tree is stopped; committed evidence remains reusable.
        try:
            stop_process()
        except Exception as stop_error:  # noqa: BLE001 - retain shutdown failure alongside its cause
            errors.append(
                {
                    "type": type(stop_error).__name__,
                    "message": str(stop_error),
                    "fatal": True,
                    "phase": "observer_shutdown",
                }
            )


def _tail_events(path, observer, finished, errors, check_stop, stop_process):
    """Tail file-first evidence; observer failure cannot swallow the original event stream."""
    pending = ""
    next_check = time.monotonic() + 1
    with path.open(encoding="utf-8", errors="replace") as stream:
        while True:
            pending += stream.read()
            lines = pending.split("\n")
            pending = lines.pop()
            for line in lines:
                try:
                    observer.feed(line)
                except (OSError, ValueError) as error:
                    # Stale leases are expected when MCP already committed the same failure.
                    errors.append({"type": type(error).__name__, "message": str(error)})
            if finished.is_set():
                if pending:
                    try:
                        observer.feed(pending)
                    except (OSError, ValueError) as error:
                        errors.append({"type": type(error).__name__, "message": str(error)})
                return
            if time.monotonic() >= next_check:
                next_check = time.monotonic() + 1
                try:
                    reason = check_stop()
                except (OSError, ValueError) as error:
                    errors.append({"type": type(error).__name__, "message": str(error)})
                    reason = None
                if reason:
                    observer.terminal_failure = observer.last_error = reason
                    stop_process()
                    return
            finished.wait(0.05)


def stop_owned_process(process: subprocess.Popen) -> None:
    """Bound interruption cleanup to this coordinator and its descendants."""
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                check=False,
                timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired):
            process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def run_codex(
    store: Store,
    document_id: str,
    *,
    model: str | None = None,
    stop_event: threading.Event | None = None,
) -> dict:
    """Ask one Codex coordinator to delegate stages to native subagents over MCP."""
    executable = shutil.which("codex")
    if executable is None:
        raise RuntimeError("Install Codex CLI and run codex login before running agents.")
    store.status(document_id)
    logs = store.root / "runs" / document_id / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    logs.mkdir(parents=True, exist_ok=True)
    from hrs.codex.runtime_context import command

    actual_model = model or "gpt-6.1-sol"
    arguments, prompt, runtime = command(executable, store.root, document_id, logs, model, PROJECT)
    (logs / "request.json").write_text(
        json.dumps(
            {
                "document_id": document_id,
                "prompt": prompt,
                "model": model,
                "actual_model": actual_model,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    def session_model(token):
        return next(
            (
                session.get("actual_model") or session.get("model")
                for session in store.dispatch.sessions()
                if session["session_token"] == token
            ),
            None,
        )

    observer = RuntimeEvents(
        run_id=logs.name,
        model=actual_model,
        fail_worker=lambda *args, **kwargs: store.dispatch.fail_runtime(*args, **kwargs),
        session_model=session_model,
        log_directory=logs,
    )
    process, failure = None, None
    observation_errors = []

    def check_stop():
        check = getattr(store.dispatch, "runtime_stop_reason", None)
        return check(document_id) if check and process is not None else None

    def stop_process():
        if process is not None:
            stop_owned_process(process)

    try:
        with (
            (logs / "events.jsonl").open("w", encoding="utf-8") as events,
            (logs / "stderr.log").open("w", encoding="utf-8") as errors,
        ):
            finished = threading.Event()
            watcher = threading.Thread(
                target=_observe_events,
                args=(
                    logs / "events.jsonl",
                    observer,
                    finished,
                    observation_errors,
                    check_stop,
                    stop_process,
                ),
                name="codex-runtime-events",
                daemon=True,
            )
            watcher.start()
            try:
                process = subprocess.Popen(
                    [*arguments, "-"],
                    stdin=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    stdout=events,
                    stderr=errors,
                    cwd=runtime,
                    **(
                        {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
                        if os.name == "nt"
                        else {"start_new_session": True}
                    ),
                )
                if stop_event is None:
                    process.communicate(input=prompt)
                else:
                    first = True
                    while True:
                        try:
                            process.communicate(input=prompt if first else None, timeout=1)
                            break
                        except subprocess.TimeoutExpired:
                            first = False
                            if stop_event.is_set() or any(
                                (store.document_directory(document_id) / marker).exists()
                                for marker in (".cancel-requested", ".pause-requested")
                            ):
                                stop_owned_process(process)
                                raise RuntimeError("Runner stopped; committed checkpoints retained")
            finally:
                events.flush()
                errors.flush()
                finished.set()
                watcher.join(timeout=35)
            if watcher.is_alive():
                raise RuntimeError(
                    "Runtime observer did not finish; committed checkpoints retained"
                )
            if process.returncode or observer.terminal_failure:
                if observer.last_error is None:
                    observer.stderr((logs / "stderr.log").read_text("utf-8", errors="replace"))
                details = (
                    observer.terminal_failure
                    or observer.last_error
                    or {
                        "code": "tool_error",
                        "model": actual_model,
                        "scope": "model",
                        "retry_after": None,
                        "event_id": f"{logs.name}:exit:{process.returncode}",
                        "evidence": {"file": "receipt.json"},
                        "message": "Native execution exited without a classified diagnostic",
                    }
                )
                raise RuntimeFailure(
                    {
                        **details,
                        "message": f"Codex exited {process.returncode}; {details['message']}; inspect {logs}",
                        "logs": str(logs),
                    }
                )
    except BaseException as error:
        failure = getattr(error, "failure", {"type": type(error).__name__, "message": str(error)})
        if process is not None and (
            not isinstance(error, RuntimeFailure) or process.returncode is None
        ):
            stop_owned_process(process)
        raise
    finally:
        receipt = {
            "document_id": document_id,
            "returncode": process.returncode if process else None,
            "logs": str(logs),
            "actual_model": actual_model,
            "status": store.status(document_id),
            "error": failure,
            "observation_errors": observation_errors,
        }
        (logs / "receipt.json").write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return receipt


def create_runner(store: Store, **options):
    """Bind the native Codex adapter to the transport-independent durable runner."""
    from hrs.processing.runner import Runner

    return Runner(store, run_codex, **options)
