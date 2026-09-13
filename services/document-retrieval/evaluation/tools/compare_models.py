"""Finite sequential calibration with real registered models and separate generations."""

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil

from document_retrieval.client import RetrievalClient
from document_retrieval.records import atomic_json, utc_now
from document_retrieval.settings import Settings

MODULE = Path(__file__).resolve().parents[2]


def stop_owned_service(pid):
    process = psutil.Process(pid)
    command = process.cmdline()
    assert Path(process.cwd()).resolve() == MODULE.resolve(), command
    assert "serve" in command and any(
        Path(part).name == "document-retrieval.exe" or part == "document_retrieval.cli"
        for part in command
    ), command
    process.terminate()
    process.wait(timeout=60)


def start_service(config, folder):
    stream = (folder / "service.log").open("ab")
    process = subprocess.Popen(
        [sys.executable, "-m", "document_retrieval.cli", "--config", str(config), "serve"],
        cwd=MODULE,
        stdout=stream,
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    stream.close()
    deadline = time.monotonic() + 300
    client = RetrievalClient(timeout=10)
    while time.monotonic() < deadline:
        assert process.poll() is None, (folder / "service.log").read_text("utf-8", errors="replace")
        try:
            status = client.get("management/status")
            candidates = [
                psutil.Process(process.pid),
                *psutil.Process(process.pid).children(recursive=True),
            ]
            actual = [
                p
                for p in candidates
                if "document_retrieval.cli" in p.cmdline()
                and Path(p.cwd()).resolve() == MODULE.resolve()
            ]
            assert len(actual) == 1, [(p.pid, p.cmdline()) for p in candidates]
            atomic_json(
                folder / "process.json",
                {
                    "launcher_pid": process.pid,
                    "service_pid": actual[0].pid,
                    "service_command": actual[0].cmdline(),
                    "started_at": utc_now(),
                },
            )
            client.close()
            return actual[0].pid, status
        except Exception:  # noqa: BLE001 -- startup has no stable HTTP endpoint yet.
            time.sleep(2)
    raise TimeoutError("Service did not become available; retain its process and log")


def resource_sampler(pid, folder, stop):
    with (folder / "resources.jsonl").open("a", encoding="utf-8") as stream:
        while not stop.is_set():
            row = {
                "at": utc_now(),
                "service_pid": pid,
                "system_ram": psutil.virtual_memory()._asdict(),
            }
            try:
                row["service_memory"] = psutil.Process(pid).memory_info()._asdict()
                row["whole_device_gpu_csv"] = subprocess.check_output(
                    [
                        "nvidia-smi",
                        "--query-gpu=name,memory.used,memory.total,utilization.gpu",
                        "--format=csv,noheader,nounits",
                    ],
                    text=True,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                ).strip()
            except (psutil.Error, OSError, subprocess.SubprocessError) as error:
                row["observation_error"] = type(error).__name__
            stream.write(json.dumps(row) + "\n")
            stream.flush()
            stop.wait(2)


def wait_completed(client, identity, folder):
    delay = 1
    started = time.monotonic()
    observations = []
    while True:
        job = client.get("jobs/" + identity)
        observations.append({"at": utc_now(), "job": job})
        atomic_json(folder / "index-progress.json", observations)
        if job["execution_state"] in ("completed", "failed", "cancelled"):
            assert job["execution_state"] == "completed", job
            return job
        assert time.monotonic() - started < 1800, job
        time.sleep(delay)
        delay = min(delay * 2, 15)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--existing-service-pid", type=int, required=True)
    parser.add_argument("--base-run", default="cal-bge-default-20260908-v3")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    root = MODULE / "evaluation/runs" / args.run_id
    assert not root.exists() or args.resume, "Use a new comparison run ID or explicitly resume"
    root.mkdir(parents=True, exist_ok=args.resume)
    base = json.loads((MODULE / "evaluation/runs" / args.base_run / "run.json").read_text("utf-8"))
    assert (MODULE / "evaluation/runs" / args.base_run / "completed.json").exists()
    scope = base["active_generation_at_start"]["scope"]
    settings = Settings.load(MODULE / "config/evaluation.local.toml")
    pid = args.existing_service_pid
    manifest = {
        "classification": "finite_calibration_comparison",
        "dataset_id": base["dataset_id"],
        "base_run": args.base_run,
        "scope": scope,
        "started_at": utc_now(),
        "shared_memory_environment": "Whole-system RAM and GPU observations include other local applications; no summation with process memory.",
        "variants": [],
    }
    if args.resume:
        manifest = json.loads((root / "comparison.json").read_text("utf-8"))
        assert (
            manifest["base_run"] == args.base_run and manifest["dataset_id"] == base["dataset_id"]
        )
    for label, embedding, reranker, disabled in (
        ("no-rerank", "BAAI/bge-m3", "BAAI/bge-reranker-v2-m3", True),
        ("qwen-rerank", "BAAI/bge-m3", "Qwen/Qwen3-Reranker-0.6B", False),
        ("qwen-embedding", "Qwen/Qwen3-Embedding-0.6B", "BAAI/bge-reranker-v2-m3", False),
    ):
        if any(r["label"] == label and r["state"] == "executed" for r in manifest["variants"]):
            continue
        folder = root / label
        folder.mkdir(exist_ok=args.resume)
        run_id = args.run_id + "-" + label
        record = {
            "label": label,
            "run_id": run_id,
            "embedding": embedding,
            "reranker": reranker,
            "rerank_disabled": disabled,
            "started_at": utc_now(),
        }
        stop_owned_service(pid)
        config = folder / "service.toml"
        values = {
            k: getattr(settings, k)
            for k in (
                "ingestion_url",
                "opensearch_url",
                "state_root",
                "models_root",
                "device",
                "host",
                "port",
                "index_prefix",
            )
        }
        values.update(auto_sync=False, embedding_model=embedding, reranking_model=reranker)
        config.write_text(
            "\n".join(
                f"{k} = {str(v).lower() if isinstance(v, bool) else v if isinstance(v, int) else json.dumps(str(v))}"
                for k, v in values.items()
            )
            + "\n",
            encoding="utf-8",
        )
        pid, status = start_service(config, folder)
        record.update(
            service_pid=pid,
            config_path=str(config),
            configuration_ref=status["configuration_ref"],
        )
        stop = threading.Event()
        observer = threading.Thread(target=resource_sampler, args=(pid, folder, stop), daemon=True)
        observer.start()
        client = RetrievalClient(timeout=300)
        try:
            if not disabled:
                body = {
                    "type": "rebuild",
                    "scope": scope,
                    "configuration_ref": record["configuration_ref"],
                    "activate_when_ready": True,
                }
                receipt = client.post(
                    "management/index-jobs", body, args.run_id + ":" + label + ":rebuild"
                )
                atomic_json(folder / "index-request.json", {"request": body, "receipt": receipt})
                wait_completed(client, receipt["job_id"], folder)
            status = client.get("management/status")
            generation = client.get("management/generations/" + status["active_generation"])
            assert (
                generation["configuration_ref"] == status["configuration_ref"]
                and generation["scope"] == scope
            )
            atomic_json(folder / "active.json", {"status": status, "generation": generation})
            command = [
                sys.executable,
                "evaluation/tools/run_quality.py",
                "--run-id",
                run_id,
                "--dataset",
                str(MODULE / "evaluation/datasets" / base["dataset_id"]),
            ]
            if disabled:
                command.append("--no-rerank")
            subprocess.run(command, cwd=MODULE, check=True)
            subprocess.run(
                [
                    sys.executable,
                    "evaluation/tools/score_quality.py",
                    "--run-id",
                    run_id,
                    "--dataset",
                    str(MODULE / "evaluation/datasets" / base["dataset_id"]),
                ],
                cwd=MODULE,
                check=True,
            )
            record.update(
                state="executed",
                completed_at=utc_now(),
                active_generation=generation["generation_id"],
            )
        except Exception as error:
            record.update(state="failed", exception=type(error).__name__, detail=str(error))
            atomic_json(folder / "failure.json", record)
            raise
        finally:
            stop.set()
            observer.join(timeout=10)
            client.close()
            manifest["variants"].append(record)
            atomic_json(root / "comparison.json", manifest)
        print(json.dumps(record), flush=True)
    atomic_json(
        root / "completed.json",
        {"at": utc_now(), "last_service_pid": pid, "selection": "pending_active_GPT_review"},
    )


if __name__ == "__main__":
    main()
