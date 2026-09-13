"""Resume unexecuted warm rounds and recover an original accepted search.

The failed driver's trace remains intact. A recovered request has no invented
original client start time; persisted server job timing covers every request.
"""

import argparse
import concurrent.futures
import json
import sqlite3
import threading
import time
from datetime import UTC, datetime

from compare_models import resource_sampler
from engineering_support import digest, load
from run_performance import distribution
from run_quality import MODULE, TraceClient

from document_retrieval.records import atomic_json, utc_now
from document_retrieval.settings import Settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    root = MODULE / "evaluation/runs" / args.run_id
    continued = root / "continued"
    continued.mkdir(exist_ok=False)
    original = load(root / "run.json")
    failure = load(root / "original-driver-failure.json")
    assert failure["state"] == "original_driver_failed"
    settings = Settings.load(root / "service.toml")
    url = f"http://127.0.0.1:{settings.port}"
    client = TraceClient(url, continued / "management-http")
    status = client.get("management/status")
    assert status["active_generation"] == original["active_generation_at_start"]["generation_id"]
    core = load(MODULE / "evaluation/development/engineering-impact-20260909-v1.json")
    assert all(digest(MODULE / p) == h for p, h in core["source_sha256"].items())
    atomic_json(
        continued / "run.json",
        {
            "classification": "continuation_after_actual_client_wait_timeout",
            "started_at": utc_now(),
            "original_run_sha256": digest(root / "run.json"),
            "original_failure_sha256": digest(root / "original-driver-failure.json"),
            "driver_sha256": digest(__file__),
            "source_sha256": core["source_sha256"],
            "configuration_changed": False,
            "background_job_already_completed": True,
            "new_quality_tasks": 0,
        },
    )
    search_budget = {"max_items": 6, "max_excerpt_tokens": 128, "max_response_tokens": 2000}
    read_budget = {"max_items": 6, "max_excerpt_tokens": 4096, "max_response_tokens": 4000}

    def execute(entry, round_number, position, original_receipt=None):
        folder = continued / f"round-{round_number}" / f"request-{position:02d}"
        trace = TraceClient(url, folder / "http")
        began, began_at = time.monotonic(), utc_now()
        atomic_json(folder / "started.json", {"at": began_at, "recovering": bool(original_receipt)})
        if original_receipt is None:
            receipt = trace.post(
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
        else:
            receipt = json.loads(original_receipt["response_text"])
        admitted = time.monotonic()
        search = trace.completed(receipt, search_budget)
        searched = time.monotonic()
        assert search["items"]
        if original_receipt:
            expected = failure["current_public_job"]
            assert receipt["job_id"] == expected["job_id"]
            assert search["list_id"] == expected["result_ref"]["list_id"]
            assert search["scope_ref"] == expected["scope_ref"]
            assert not any(t["url"].endswith("/searches") for t in trace.rows)
        reading = trace.post(
            "reads",
            {
                "selection": {
                    "kind": "evidence",
                    "evidence_ref": search["items"][0]["evidence_ref"],
                },
                "layer": "passage",
                "purpose": "card_input",
                "budget": read_budget,
            },
        )
        assert reading["units"]
        ended = time.monotonic()
        row = {
            "task_id": entry["id"],
            "round": round_number,
            "position": position,
            "request_at": None if original_receipt else began_at,
            "finished_at": utc_now(),
            "recovered_original_job": bool(original_receipt),
            "admission_response_seconds": None if original_receipt else admitted - began,
            "search_sequence_seconds": None if original_receipt else searched - began,
            "read_seconds": ended - searched,
            "total_seconds": None if original_receipt else ended - began,
            "initial_receipt": receipt,
            "search": search,
            "read": reading,
            "tool_text_proxy_tokens": sum(t["independent_proxy_tokens"] for t in trace.rows),
        }
        if original_receipt:
            row["recovery"] = {
                "first_receipt_at": original_receipt["recorded_at"],
                "recovery_started_at": began_at,
                "recovery_sequence_seconds": ended - began,
                "original_client_start_not_recorded": True,
                "historical_timeout_retained": True,
                "gap_before_recovery_is_not_service_latency": True,
            }
        atomic_json(folder / "result.json", row)
        trace.close()
        print(
            json.dumps(
                {
                    k: row[k]
                    for k in ("round", "position", "recovered_original_job", "total_seconds")
                }
            ),
            flush=True,
        )
        return row

    stop = threading.Event()
    sampler = threading.Thread(
        target=resource_sampler,
        args=(load(root / "active-process.json")["service_pid"], continued, stop),
        daemon=True,
    )
    sampler.start()
    try:
        rows = []
        for number in (1, 2, 3):
            pending = []
            for position, entry in enumerate(original["selected_requests"]):
                folder = root / f"round-{number}" / f"request-{position:02d}"
                if (folder / "result.json").exists():
                    rows.append(load(folder / "result.json"))
                elif (folder / "http/http-00001.json").exists():
                    first = load(folder / "http/http-00001.json")
                    assert first["status"] == 202
                    rows.append(execute(entry, number, position, first))
                else:
                    pending.append((entry, number, position))
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                rows.extend(pool.map(lambda values: execute(*values), pending))
        job_ids = {r["initial_receipt"]["job_id"] for r in rows}
        assert len(rows) == len(job_ids) == 60
        marker = load(root / "faults/performance-queue-seed.json")
        background_id = marker["claimed_job"]["job_id"]
        background = client.get("jobs/" + background_id)
        assert background["execution_state"] == "completed"
        db = (settings.state_root / "control.sqlite3").resolve()
        connection = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        recorded = [
            dict(r)
            for r in connection.execute(
                "SELECT id,type,state,phase,created,updated,result FROM jobs"
            )
            if r["id"] in job_ids | {background_id}
        ]
        connection.close()
        assert len(recorded) == 61 and all(r["state"] == "completed" for r in recorded)
        atomic_json(continued / "persisted-job-timing.json", recorded)
        jobs = {r["id"]: r for r in recorded}
        bg = jobs[background_id]
        events = [
            json.loads(s) for s in (root / "faults/scheduler.jsonl").read_text("utf-8").splitlines()
        ]
        elapsed, queued, per_phase = [], [], {}
        for identity in job_ids:
            job = jobs[identity]
            duration = job["updated"] - job["created"]
            steps = [e for e in events if e.get("job_id") == identity and e["event"] == "end"]
            assert steps
            work = sum(e["seconds"] for e in steps)
            elapsed.append(duration)
            queued.append(max(0, duration - work))
            for phase in {e["phase"] for e in steps}:
                per_phase.setdefault(phase, []).append(
                    sum(e["seconds"] for e in steps if e["phase"] == phase)
                )
        ordinary = [r for r in rows if not r.get("recovered_original_job")]
        saturated = [
            e
            for e in events
            if e["event"] == "start"
            and e["available_after_claim"].get("interactive", 0)
            and e["available_after_claim"].get("background", 0)
        ]
        assert any(e["interactive_streak_after_claim"] == 4 for e in saturated)
        assert any(e["workload"] == "background" for e in saturated)
        summary = {
            "classification": "machine_measured_performance_with_explicit_recovery",
            "state": "passed_completion_with_recovery",
            "original_driver_completed": False,
            "original_failure": "original-driver-failure.json",
            "warm_requests": 60,
            "rounds": 3,
            "concurrent_clients": 4,
            "ordinary_client_completions": len(ordinary),
            "client_wait_timeouts": 1,
            "recovered_original_jobs": 1,
            "server_job_seconds_all_60": distribution(elapsed),
            "queue_and_dispatch_overhead_seconds_estimate": distribution(queued),
            "phase_wall_seconds_per_job": {k: distribution(v) for k, v in per_phase.items()},
            "ordinary_client_search_seconds_59": distribution(
                [r["search_sequence_seconds"] for r in ordinary]
            ),
            "ordinary_client_total_seconds_59": distribution(
                [r["total_seconds"] for r in ordinary]
            ),
            "read_seconds_all_60": distribution([r["read_seconds"] for r in rows]),
            "per_round": {
                str(i): {
                    "completed": 20,
                    "ordinary_client_total": distribution(
                        [r["total_seconds"] for r in ordinary if r["round"] == i]
                    ),
                    "server_job_seconds": distribution(
                        [
                            jobs[r["initial_receipt"]["job_id"]]["updated"]
                            - jobs[r["initial_receipt"]["job_id"]]["created"]
                            for r in rows
                            if r["round"] == i
                        ]
                    ),
                }
                for i in (1, 2, 3)
            },
            "background_rebuild": {
                "job_id": background_id,
                "completed": background,
                "accepted_to_completion_seconds": bg["updated"] - bg["created"],
                "completed_at": datetime.fromtimestamp(bg["updated"], UTC).isoformat(),
                "warm_requests_admitted_before_background_completed": sum(
                    jobs[i]["created"] < bg["updated"] for i in job_ids
                ),
                "warm_jobs_completed_before_background_completed": sum(
                    jobs[i]["updated"] < bg["updated"] for i in job_ids
                ),
            },
            "saturated_scheduler_observations": saturated,
            "samples": [{k: v for k, v in r.items() if k not in ("search", "read")} for r in rows],
            "limits": "One actual 900-second wait timeout remains a failed first attempt. Its original accepted job completed and was recovered without another search. Server timing covers all 60; normal client percentiles condition on the other 59. Queue estimates include dispatch/control overhead. Rounds 2 and 3 ran after background completion and an explicit diagnosis gap. Original and continuation resource sampling have a gap. No latency ceiling or capacity claim; repeated requests do not enlarge quality coverage.",
        }
        atomic_json(root / "performance-recovery-v2.json", summary)
    finally:
        stop.set()
        sampler.join(timeout=10)
        client.close()


if __name__ == "__main__":
    main()
