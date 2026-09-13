"""Evaluation-only pauses around real durable operations; never fabricates readiness.

The production HTTP application, ingestion client, OpenSearch and model implementations
are unchanged. A local control file selects one fault or a simulated clock offset.
An external driver must actually terminate this process for a kill/restart experiment.
"""

import argparse
import asyncio
import hashlib
import json
import os
import threading
import time
from pathlib import Path

import uvicorn
from fastapi import Request, Response

from document_retrieval.api import create_app
from document_retrieval.errors import Problem
from document_retrieval.records import atomic_json, utc_now
from document_retrieval.runtime import Retrieval
from document_retrieval.settings import Settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--control", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    log_lock = threading.Lock()

    def control():
        if not args.control.exists():
            return {}
        return json.loads(args.control.read_text("utf-8"))

    def log(event):
        with log_lock, (args.output / "scheduler.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"at": utc_now(), "pid": os.getpid(), **event}) + "\n")
            stream.flush()

    def fault(point, job):
        arm = control().get("armed", {})
        if arm.get("point") != point:
            return
        if arm.get("require_events") and not job.get("page", {}).get("items"):
            return
        if any(job.get(k) != arm[k] for k in ("job_id", "type", "phase") if k in arm):
            return
        if any(
            job.get("payload", {}).get(k, 0) < value
            for k, value in arm.get("payload_min", {}).items()
        ):
            return
        marker = args.output / (arm["id"] + ".json")
        if marker.exists():
            return
        atomic_json(
            marker,
            {
                "classification": "synthetic_fault_on_real_storage",
                "at": utc_now(),
                "pid": os.getpid(),
                "point": point,
                "arm": arm,
                "claimed_job": job,
                "persisted_job": runtime.store.job(job["job_id"]) if job.get("job_id") else None,
                "status": runtime.management_status(),
            },
        )
        log({"event": "fault_reached", "point": point, "marker": str(marker)})
        if arm.get("mode", "pause") == "error":
            raise Problem(
                arm.get("error_code", "evaluation_injected_failure"),
                "Recorded evaluation fault.",
                status=503,
            )
        while control().get("armed", {}).get("id") == arm["id"]:
            time.sleep(0.05)

    settings = Settings.load(args.config)
    runtime = Retrieval(settings, fault=fault)
    runtime.store.clock = lambda: time.time() + control().get("clock_offset_seconds", 0)
    original_step = runtime.step
    current_job = {}

    def step(job):
        current_job.clear()
        current_job.update(job)
        event = {k: job.get(k) for k in ("job_id", "type", "phase", "workload", "attempt_id")}
        with runtime.store.connect() as connection:
            event["interactive_streak_after_claim"] = runtime.store.meta(
                "interactive_streak", 0, connection=connection
            )
            event["available_after_claim"] = {
                row[0]: row[1]
                for row in connection.execute(
                    "SELECT workload,count(*) FROM jobs WHERE state='queued' AND available<=? GROUP BY workload",
                    (runtime.store.clock(),),
                )
            }
        log({"event": "start", **event})
        fault("before_step", job)
        start = time.monotonic()
        try:
            original_step(job)
            fault("after_step", job)
        finally:
            log({"event": "end", "seconds": time.monotonic() - start, **event})
            current_job.clear()

    runtime.step = step
    original_receive = runtime.store.receive_events

    def receive(page):
        result = original_receive(page)
        fault("events_persisted_before_reconciliation", {"type": "event_receive", "page": page})
        return result

    runtime.store.receive_events = receive
    original_usage = runtime.source.usage

    def usage(*args, **kwargs):
        fault("before_upstream_usage", {"type": "upstream_usage"})
        return original_usage(*args, **kwargs)

    runtime.source.usage = usage
    for method in ("embed", "rerank"):
        original_method = getattr(runtime.models, method)

        def model_call(*values, original_method=original_method, method=method, **kwargs):
            fault("before_model_" + method, dict(current_job))
            started = time.monotonic()
            try:
                return original_method(*values, **kwargs)
            finally:
                log(
                    {
                        "event": "model_end",
                        "method": method,
                        "seconds": time.monotonic() - started,
                        "job_id": current_job.get("job_id"),
                        "workload": current_job.get("workload"),
                    }
                )

        setattr(runtime.models, method, model_call)
    atomic_json(
        args.output / (f"process-{os.getpid()}.json"),
        {
            "pid": os.getpid(),
            "started_at": utc_now(),
            "settings": settings.public(),
            "faults_are_evaluation_only": True,
            "storage_and_models_are_actual": True,
        },
    )
    app = create_app(settings, runtime=runtime)

    @app.middleware("http")
    async def pause_before_delivery(request: Request, call_next):
        response = await call_next(request)
        if control().get("armed", {}).get("point") != "response_committed_before_delivery":
            return response
        if request.method != "POST" or not (
            request.url.path.endswith("/searches") or request.url.path.endswith("/result")
        ):
            return response
        raw = b"".join([chunk async for chunk in response.body_iterator])
        body = json.loads(raw)
        if body.get("list_id"):
            await asyncio.to_thread(
                fault,
                "response_committed_before_delivery",
                {
                    "type": "http_response",
                    "list_id": body["list_id"],
                    "response_sha256": hashlib.sha256(raw).hexdigest(),
                    "request_path": request.url.path,
                    "response_status": response.status_code,
                },
            )
        return Response(raw, status_code=response.status_code, headers=dict(response.headers))

    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
