"""Supplement a completed mixed run with separately timed public hybrid queries.

Three existing queries run three warm rounds after one cold query. These are
sequential diagnostics without background work, not additional quality samples
or a replacement for the 60-request mixed distribution.
"""

import argparse
import json
import sqlite3
import time

from engineering_support import Engineering, digest, load
from run_performance import distribution
from run_quality import MODULE, TraceClient

from document_retrieval.records import atomic_json, utc_now
from document_retrieval.settings import Settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--performance-run", required=True)
    parser.add_argument("--performance-summary", default="performance.json")
    parser.add_argument("--existing-service-run")
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    previous_root = MODULE / "evaluation/runs" / args.performance_run
    previous_summary = load(previous_root / args.performance_summary)
    assert previous_summary["state"] in ("passed", "passed_completion_with_recovery")
    previous = load(previous_root / "run.json")
    selected = [r for r in previous["selected_requests"] if r["mode"] == "hybrid"][:3]
    assert len(selected) == 3
    root = MODULE / "evaluation/runs" / args.run_id
    root.mkdir(parents=True, exist_ok=False)
    (root / "service.toml").write_bytes((previous_root / "service.toml").read_bytes())
    port = Settings.load(root / "service.toml").port
    fixture = "state/engineering-protocol-20260908-v2/fixture.json"
    old = Engineering(args.existing_service_run or args.performance_run, fixture, port=port)
    atomic_json(root / "previous-service-stop.json", old.stop(reason="Start inference timing run"))
    old.client.close()
    run = Engineering(args.run_id, fixture, port=port)
    startup_started = time.monotonic()
    status = run.start()
    startup_seconds = time.monotonic() - startup_started
    assert status["active_generation"] == previous["active_generation_at_start"]["generation_id"]
    atomic_json(
        root / "run.json",
        {
            "classification": "sequential_inference_timing_supplement",
            "started_at": utc_now(),
            "performance_run": args.performance_run,
            "performance_summary": args.performance_summary,
            "performance_sha256": digest(previous_root / args.performance_summary),
            "selected_requests": selected,
            "config_sha256": digest(root / "service.toml"),
            "helper_sha256": digest(MODULE / "evaluation/tools/fault_server.py"),
            "driver_sha256": digest(__file__),
            "source_sha256": {
                str(p.relative_to(MODULE)): digest(p)
                for p in sorted((MODULE / "src").rglob("*.py"))
            },
            "new_quality_tasks": 0,
            "concurrent_clients": 1,
            "background_work": False,
            "startup_seconds_without_first_models": startup_seconds,
        },
    )
    budget = {"max_items": 6, "max_excerpt_tokens": 128, "max_response_tokens": 2000}
    rows = []
    try:
        for number, entry in enumerate([selected[0], *selected, *selected, *selected]):
            client = TraceClient(run.url, root / f"request-{number:02d}" / "http")
            started = time.monotonic()
            receipt = client.post(
                "searches",
                {
                    "query": entry["query"],
                    "mode": "hybrid",
                    "scope": entry["scope"],
                    "purpose": "card_input",
                    "budget": budget,
                    "workload": "interactive",
                },
                f"{args.run_id}:{number}",
            )
            result = client.completed(receipt, budget)
            search_seconds = time.monotonic() - started
            assert result["items"]
            assert result["stages"]["vector"]["executed"]
            assert result["stages"]["rerank"]["executed"]
            job_id = receipt.get("job_id")
            if job_id is None:
                db = (run.settings.state_root / "control.sqlite3").resolve()
                connection = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)
                matching = connection.execute(
                    "SELECT id FROM jobs WHERE type='search' AND json_extract(result,'$.list_id')=?",
                    (result["list_id"],),
                ).fetchall()
                connection.close()
                assert len(matching) == 1, matching
                job_id = matching[0][0]
            row = {
                "number": number,
                "task_id": entry["id"],
                "cold": number == 0,
                "job_id": job_id,
                "job_identity_source": "receipt"
                if "job_id" in receipt
                else "persisted_result_list_id",
                "search_seconds": search_seconds,
                "result": result,
            }
            atomic_json(root / f"request-{number:02d}" / "result.json", row)
            rows.append(row)
            client.close()
            print(json.dumps({k: v for k, v in row.items() if k != "result"}), flush=True)
        events = [json.loads(s) for s in (run.faults / "scheduler.jsonl").read_text().splitlines()]
        warm_jobs = {r["job_id"] for r in rows if not r["cold"]}
        measures = {}
        for method in ("embed", "rerank"):
            matching = [
                e
                for e in events
                if e["event"] == "model_end" and e["method"] == method and e["job_id"] in warm_jobs
            ]
            assert {e["job_id"] for e in matching} == warm_jobs, matching
            measures[method] = {
                **distribution(
                    [sum(e["seconds"] for e in matching if e["job_id"] == job) for job in warm_jobs]
                ),
                "model_calls": len(matching),
                "aggregation": "Sum all model batches belonging to each original search job.",
            }
        atomic_json(
            root / "inference-timing.json",
            {
                "classification": "machine_measured_model_call_wall_time",
                "state": "passed",
                "warm_requests": 9,
                "warm_rounds": 3,
                "cold_requests": 1,
                "seconds": measures,
                "samples": [{k: v for k, v in r.items() if k != "result"} for r in rows],
                "limits": "Sequential supplement, not mixed-load inference percentiles. Model-call wall time includes tokenization, model execution and result transfer; it is not isolated GPU kernel time. Three repeated existing queries do not enlarge quality or capacity coverage.",
            },
        )
    finally:
        run.client.close()


if __name__ == "__main__":
    main()
