"""Owned Codex subprocess execution, independent of CLI and HTTP adapters."""

import json
import os
import shutil
import signal
import subprocess
import threading
from datetime import UTC, datetime
from pathlib import Path

from hrs_codex.store import Store

PROJECT = Path(__file__).resolve().parents[4]


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
    from hrs_codex.runtime_context import command

    arguments, prompt, runtime = command(executable, store.root, document_id, logs, model, PROJECT)
    (logs / "request.json").write_text(
        json.dumps(
            {"document_id": document_id, "prompt": prompt, "model": model},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    process, failure = None, None
    try:
        with (
            (logs / "events.jsonl").open("w", encoding="utf-8") as events,
            (logs / "stderr.log").open("w", encoding="utf-8") as errors,
        ):
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
    except BaseException as error:
        failure = {"type": type(error).__name__, "message": str(error)}
        if process is not None:
            stop_owned_process(process)
        raise
    finally:
        receipt = {
            "document_id": document_id,
            "returncode": process.returncode if process else None,
            "logs": str(logs),
            "status": store.status(document_id),
            "error": failure,
        }
        (logs / "receipt.json").write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    if receipt["returncode"]:
        raise RuntimeError(
            f"Codex exited {receipt['returncode']}; evidence retained. Inspect {logs}"
        )
    return receipt
