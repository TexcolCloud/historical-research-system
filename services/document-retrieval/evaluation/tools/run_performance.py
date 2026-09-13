"""Three fixed warm rounds overlapping a real-source background rebuild.

This measures public retrieval sequences and actual scheduler steps, without
adding independent quality tasks or treating proxy tokens as model billing.
"""

import argparse
import concurrent.futures
import json
import statistics
import threading
import time
from pathlib import Path

from compare_models import resource_sampler, stop_owned_service
from engineering_support import Engineering, digest, load
from run_quality import MODULE, TraceClient

from document_retrieval.records import atomic_json, utc_now
from document_retrieval.settings import Settings


def distribution(values):
    ordered = sorted(values)
    return {
        "n": len(ordered),
        "p50": statistics.median(ordered),
        "p95": ordered[min(len(ordered) - 1, __import__("math").ceil(len(ordered) * 0.95) - 1)],
        "min": min(ordered),
        "max": max(ordered),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--existing-service-pid", type=int, required=True)
    parser.add_argument("--dataset", default="retrieval-representative-20260908-v6")
    parser.add_argument("--budget", nargs=3, type=int, default=[192, 3000, 6000])
    args = parser.parse_args()
    root = MODULE / "evaluation/runs" / args.run_id
    root.mkdir(parents=True, exist_ok=False)
    settings = Settings.load(args.config)
    (root / "service.toml").write_bytes(args.config.read_bytes())
    tasks_path = MODULE / "evaluation/datasets" / args.dataset / "tasks.json"
    tasks = load(tasks_path)[::3]
    assert len(tasks) == 20
    run = Engineering(
        args.run_id, "state/engineering-protocol-20260908-v2/fixture.json", port=settings.port
    )
    stop_owned_service(args.existing_service_pid)
    start = time.monotonic()
    status = run.start()
    startup_seconds = time.monotonic() - start
    generation = run.store().get("generation", status["active_generation"])
    assert generation["baseline_complete"]
    assert generation["configuration_ref"] == status["configuration_ref"]
    scope = generation["scope"]
    assert scope["kind"] == "selected" and len(scope["sources"]) >= 20
    selected = [
        {
            "id": t["id"],
            "mode": t["mode"],
            "scope": t["source_scopes"][0]["scope"],
            "query": t["source_scopes"][0].get("query", t["query"]),
        }
        for t in tasks
    ]
    atomic_json(
        root / "run.json",
        {
            "classification": "real_source_public_sequence_performance",
            "started_at": utc_now(),
            "config_sha256": digest(args.config),
            "dataset_tasks_sha256": digest(tasks_path),
            "task_selection": "Every third task in the immutable 60-task ordering; 20 repeated three times.",
            "selected_requests": selected,
            "rounds": 3,
            "interactive_requests": 60,
            "active_generation_at_start": generation,
            "new_quality_tasks": 0,
            "counter": "bge-m3-proxy-v1, full serialized HTTP bodies",
            "host_model_or_image_billing": "not measured",
            "capacity": "paused",
        },
    )
    stop_sampling = threading.Event()
    observer = threading.Thread(
        target=resource_sampler,
        args=(load(root / "active-process.json")["service_pid"], root, stop_sampling),
        daemon=True,
    )
    observer.start()
    search_budget = {
        "max_items": 6,
        "max_excerpt_tokens": args.budget[0],
        "max_response_tokens": args.budget[1],
    }
    read_budget = {
        "max_items": 6,
        "max_excerpt_tokens": 4096,
        "max_response_tokens": args.budget[2],
    }

    def execute(entry, round_number, position):
        folder = root / f"round-{round_number}" / f"request-{position:02d}"
        client = TraceClient(run.url, folder / "http")
        began = time.monotonic()
        request_at = utc_now()
        value = client.post(
            "searches",
            {
                "query": entry["query"],
                "mode": entry["mode"],
                "scope": entry["scope"],
                "purpose": "card_input",
                "budget": search_budget,
                "workload": "interactive",
            },
            f"{args.run_id}:{round_number}:{position}",
        )
        response_at = time.monotonic()
        result = client.completed(value, search_budget)
        search_done = time.monotonic()
        assert result["items"], result
        reading = client.post(
            "reads",
            {
                "selection": {
                    "kind": "evidence",
                    "evidence_ref": result["items"][0]["evidence_ref"],
                },
                "layer": "passage",
                "purpose": "card_input",
                "budget": read_budget,
            },
        )
        assert reading["units"], reading
        ended = time.monotonic()
        row = {
            "task_id": entry["id"],
            "round": round_number,
            "position": position,
            "request_at": request_at,
            "finished_at": utc_now(),
            "admission_response_seconds": response_at - began,
            "search_sequence_seconds": search_done - began,
            "read_seconds": ended - search_done,
            "total_seconds": ended - began,
            "initial_receipt": value,
            "search": result,
            "read": reading,
            "tool_text_proxy_tokens": sum(t["independent_proxy_tokens"] for t in client.rows),
        }
        atomic_json(folder / "result.json", row)
        client.close()
        print(
            json.dumps(
                {"round": round_number, "position": position, "seconds": row["total_seconds"]}
            ),
            flush=True,
        )
        return row

    try:
        cold = execute(next(t for t in selected if t["mode"] == "hybrid"), 0, 0)
        run.arm("performance-queue-seed", "before_step", type="index", phase="await_builds")
        rebuild_start = time.monotonic()
        rebuild = run.index("performance-real-rebuild", scope=scope, activate=False, wait=False)
        marker = run.marker("performance-queue-seed")
        results = []
        for round_number in range(1, 4):
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                futures = [
                    pool.submit(execute, item, round_number, position)
                    for position, item in enumerate(selected)
                ]
                if round_number == 1:
                    deadline = time.monotonic() + 60
                    while time.monotonic() < deadline:
                        queued = [
                            j
                            for j in run.store().jobs(limit=10000)
                            if j["type"] == "search" and j["execution_state"] == "queued"
                        ]
                        if len(queued) >= 4:
                            break
                        time.sleep(0.1)
                    else:
                        raise TimeoutError("Four actual interactive requests did not queue")
                    run.disarm()
                results.extend(f.result() for f in futures)
        finished = run.wait(rebuild["job_id"], timeout=3600)
        background_seconds = time.monotonic() - rebuild_start
        logs = [
            json.loads(line)
            for line in (root / "faults/scheduler.jsonl").read_text("utf-8").splitlines()
        ]
        warm_start = min(row["request_at"] for row in results)
        warm_end = max(row["finished_at"] for row in results)
        steps = [
            event
            for event in logs
            if event["event"] == "start" and warm_start <= event["at"] <= warm_end
        ]
        saturated = [
            event
            for event in steps
            if event["available_after_claim"].get("interactive", 0)
            and event["available_after_claim"].get("background", 0)
        ]
        background = [event for event in steps if event["workload"] == "background"]
        assert len(results) == 60 and background and saturated
        assert any(event["interactive_streak_after_claim"] == 4 for event in saturated)
        assert any(event["workload"] == "background" for event in saturated)
        timings = {}
        for event in logs:
            if event["event"] == "end" and warm_start <= event["at"] <= warm_end:
                timings.setdefault(event["type"] + ":" + event["phase"], []).append(
                    event["seconds"]
                )
        atomic_json(
            root / "performance.json",
            {
                "classification": "machine_measured_public_tool_sequences",
                "state": "passed",
                "startup_seconds_without_first_models": startup_seconds,
                "first_hybrid_request_including_model_load": cold,
                "warm_requests": 60,
                "rounds": 3,
                "concurrent_clients": 4,
                "search_sequence_seconds": distribution(
                    [r["search_sequence_seconds"] for r in results]
                ),
                "read_seconds": distribution([r["read_seconds"] for r in results]),
                "total_seconds": distribution([r["total_seconds"] for r in results]),
                "per_round": {
                    str(i): distribution([r["total_seconds"] for r in results if r["round"] == i])
                    for i in range(1, 4)
                },
                "step_seconds": {k: distribution(v) for k, v in timings.items()},
                "background_rebuild": {
                    "receipt": rebuild,
                    "completed": finished,
                    "seconds": background_seconds,
                    "steps_during_interactive_requests": len(background),
                },
                "queue_seed": marker,
                "saturated_scheduler_observations": saturated,
                "scheduler_policy": "Four interactive step starts then an eligible background step; exact start trace and queue snapshots retained. Background progresses while requests continue.",
                "samples": [
                    {k: v for k, v in row.items() if k not in ("initial_receipt", "search", "read")}
                    for row in results
                ],
                "limits": "Shared Windows host, no predefined latency ceiling. Proxy response volume is not model billing or a complete card-generation measurement. Repeated tasks do not expand quality sample size.",
            },
        )
    finally:
        run.disarm()
        stop_sampling.set()
        observer.join(timeout=10)
        run.client.close()


if __name__ == "__main__":
    main()
