"""Execute one explicitly selected calibration configuration, without held-out queries."""

import argparse
import json
import subprocess
import sys
import threading

from compare_models import resource_sampler, start_service, stop_owned_service, wait_completed
from run_quality import MODULE

from document_retrieval.client import RetrievalClient
from document_retrieval.records import atomic_json, utc_now
from document_retrieval.settings import Settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--existing-service-pid", type=int, required=True)
    parser.add_argument("--embedding", default="BAAI/bge-m3")
    parser.add_argument("--reranker", default="BAAI/bge-reranker-v2-m3")
    parser.add_argument("--chunks", nargs=3, type=int, default=[384, 768, 64])
    parser.add_argument("--budget", nargs=3, type=int, default=[128, 2000, 4000])
    parser.add_argument("--reason", required=True)
    args = parser.parse_args()
    root = MODULE / "evaluation/runs" / args.run_id
    root.mkdir(parents=True, exist_ok=False)
    settings = Settings.load(MODULE / "config/evaluation.local.toml")
    base = json.loads(
        (MODULE / "evaluation/runs/cal-bge-default-20260908-v3/run.json").read_text("utf-8")
    )
    scope = base["active_generation_at_start"]["scope"]
    values = {
        key: getattr(settings, key)
        for key in (
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
    values.update(
        auto_sync=False,
        embedding_model=args.embedding,
        reranking_model=args.reranker,
        chunk_target=args.chunks[0],
        chunk_max=args.chunks[1],
        chunk_overlap=args.chunks[2],
    )
    config = root / "service.toml"
    config.write_text(
        "\n".join(
            f"{key} = {str(value).lower() if isinstance(value, bool) else value if isinstance(value, int) else json.dumps(str(value))}"
            for key, value in values.items()
        )
        + "\n",
        encoding="utf-8",
    )
    atomic_json(
        root / "experiment.json",
        {
            "classification": "one_registered_calibration_candidate",
            "started_at": utc_now(),
            "reason": args.reason,
            "dataset_id": base["dataset_id"],
            "heldout_results_seen": False,
            "scope": scope,
            "values": Settings.load(config).public(),
            "budget": args.budget,
        },
    )
    stop_owned_service(args.existing_service_pid)
    pid, status = start_service(config, root)
    stop = threading.Event()
    observer = threading.Thread(target=resource_sampler, args=(pid, root, stop), daemon=True)
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
        wait_completed(client, receipt["job_id"], root)
        quality_id = args.run_id + "-quality"
        command = [
            sys.executable,
            "-X",
            "utf8",
            "evaluation/tools/run_quality.py",
            "--run-id",
            quality_id,
            "--excerpt",
            str(args.budget[0]),
            "--response",
            str(args.budget[1]),
            "--read",
            str(args.budget[2]),
        ]
        subprocess.run(command, cwd=MODULE, check=True)
        subprocess.run(
            [
                sys.executable,
                "-X",
                "utf8",
                "evaluation/tools/score_quality.py",
                "--run-id",
                quality_id,
            ],
            cwd=MODULE,
            check=True,
        )
        atomic_json(
            root / "completed.json",
            {
                "at": utc_now(),
                "service_pid": pid,
                "quality_run_id": quality_id,
                "selection": "pending_active_GPT_review",
            },
        )
    finally:
        stop.set()
        observer.join(timeout=10)
        client.close()


if __name__ == "__main__":
    main()
