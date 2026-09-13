"""Build every fixed representative source after the finite calibration is locked."""

import argparse
import hashlib
import json
import threading
from pathlib import Path

from compare_models import resource_sampler, start_service, stop_owned_service, wait_completed
from run_quality import MODULE

from document_retrieval.client import RetrievalClient
from document_retrieval.records import atomic_json, utc_now


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--existing-service-pid", type=int, required=True)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=MODULE / "evaluation/datasets/retrieval-representative-20260908-v6",
    )
    parser.add_argument("--include-dataset", type=Path, action="append", default=[])
    args = parser.parse_args()
    root = MODULE / "evaluation/runs" / args.run_id
    root.mkdir(parents=True, exist_ok=False)
    selection = json.loads(args.selection.read_text("utf-8"))
    assert selection["locked_before_holdout"] is True
    lock = json.loads((args.dataset / "lock.json").read_text("utf-8"))
    task_path = args.dataset / "tasks.json"
    assert hashlib.sha256(task_path.read_bytes()).hexdigest() == lock["files"]["tasks.json"]
    tasks = json.loads(task_path.read_text("utf-8"))
    additional_datasets = []
    for dataset in args.include_dataset:
        extra_lock = json.loads((dataset / "lock.json").read_text("utf-8"))
        extra_tasks = dataset / "tasks.json"
        assert (
            hashlib.sha256(extra_tasks.read_bytes()).hexdigest()
            == extra_lock["files"]["tasks.json"]
        )
        tasks.extend(json.loads(extra_tasks.read_text("utf-8")))
        additional_datasets.append(
            {
                "dataset_id": extra_lock["dataset_id"],
                "tasks_sha256": extra_lock["files"]["tasks.json"],
            }
        )
    snapshots = sorted(
        {
            source["snapshot_id"]
            for task in tasks
            for group in task["source_scopes"]
            for source in group["scope"]["sources"]
        }
    )
    scope = {"kind": "selected", "sources": [{"snapshot_id": s} for s in snapshots]}
    atomic_json(
        root / "run.json",
        {
            "run_id": args.run_id,
            "started_at": utc_now(),
            "classification": "actual_complete_representative_corpus_build",
            "dataset_id": lock["dataset_id"],
            "additional_datasets": additional_datasets,
            "selection": str(args.selection.resolve()),
            "selection_sha256": hashlib.sha256(args.selection.read_bytes()).hexdigest(),
            "config": str(args.config.resolve()),
            "config_sha256": hashlib.sha256(args.config.read_bytes()).hexdigest(),
            "source_sha256": {
                str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in (MODULE / "src").rglob("*.py")
            },
            "scope": scope,
            "reference_files_accessed": False,
            "capacity_acceptance": "paused_by_user",
        },
    )
    stop_owned_service(args.existing_service_pid)
    pid, status = start_service(args.config.resolve(), root)
    stopped = threading.Event()
    observer = threading.Thread(target=resource_sampler, args=(pid, root, stopped), daemon=True)
    observer.start()
    client = RetrievalClient(timeout=300)
    try:
        receipt = client.post(
            "management/index-jobs",
            {
                "type": "rebuild",
                "scope": scope,
                "configuration_ref": status["configuration_ref"],
                "activate_when_ready": True,
            },
            args.run_id + ":index",
        )
        atomic_json(root / "index-request.json", receipt)
        completed = wait_completed(client, receipt["job_id"], root)
        after = client.get("management/status")
        generation = client.get("management/generations/" + after["active_generation"])
        assert generation["scope"] == scope and generation["baseline_complete"]
        atomic_json(
            root / "completed.json",
            {
                "at": utc_now(),
                "service_pid": pid,
                "job": completed,
                "generation": generation,
                "status": after,
                "fixed_sources": len(snapshots),
                "quality_acceptance": "pending",
            },
        )
        print(
            json.dumps({"fixed_sources": len(snapshots), "state": "built", "pid": pid}), flush=True
        )
    finally:
        stopped.set()
        observer.join(10)
        client.close()


if __name__ == "__main__":
    main()
