"""Shared real-process helpers for labelled synthetic protocol acceptance."""

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import psutil
from run_quality import MODULE, TraceClient

from document_retrieval.control import ControlStore
from document_retrieval.errors import Problem
from document_retrieval.records import atomic_json, new_id, utc_now
from document_retrieval.settings import Settings


def load(path):
    return json.loads(Path(path).read_text("utf-8"))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class Engineering:
    def __init__(self, run_id, fixture, *, port=18131):
        self.root = MODULE / "evaluation/runs" / run_id
        self.root.mkdir(parents=True, exist_ok=True)
        self.fixture_path = Path(fixture).resolve()
        self.fixture = load(fixture)
        self.parts = {p["id"]: p for p in self.fixture["parts"]}
        self.run_id, self.url = run_id, f"http://127.0.0.1:{port}"
        self.control = self.root / "fault-control.json"
        self.config = self.root / "service.toml"
        self.faults = self.root / "faults"
        self.faults.mkdir(exist_ok=True)
        if not self.control.exists():
            atomic_json(self.control, {})
        if not self.config.exists():
            values = {
                "state_root": str(self.root / "control-state"),
                "models_root": str(
                    Settings.load(MODULE / "config/evaluation.local.toml").models_root
                ),
                "ingestion_url": "http://127.0.0.1:18125/api/v1",
                "opensearch_url": "http://127.0.0.1:19260",
                "index_prefix": "retrieval-engineering-v1",
                "device": "cuda:0",
                "port": port,
                "auto_sync": False,
                "embedding_batch": 1,
                "reranking_batch": 4,
            }
            self.config.write_text(
                "\n".join(
                    f"{key} = {str(value).lower() if isinstance(value, bool) else value if isinstance(value, int) else json.dumps(value)}"
                    for key, value in values.items()
                )
                + "\n",
                encoding="utf-8",
            )
            atomic_json(
                self.root / "run.json",
                {
                    "classification": "synthetic_protocol_actual_storage_and_models",
                    "started_at": utc_now(),
                    "fixture": str(self.fixture_path),
                    "fixture_sha256": digest(fixture),
                    "historical_quality_sample": False,
                    "process_faults_require_external_termination": True,
                },
            )
        self.settings = Settings.load(self.config)
        self.scope = {
            "kind": "selected",
            "sources": [{"snapshot_id": s} for s in self.fixture["reading_snapshots"]],
        }
        self.main = {"kind": "snapshot", "snapshot_id": self.fixture["reading_snapshots"][0]}
        self.other = {"kind": "snapshot", "snapshot_id": self.fixture["reading_snapshots"][1]}
        self.current = {
            "kind": "target",
            "target_type": "occurrence",
            "target_id": self.fixture["allocated_ids"]["occurrence-one"],
        }
        self.trace_folder = self.root / "http" / new_id("session")
        self.client = TraceClient(self.url, self.trace_folder)
        self.budget = {"max_items": 6, "max_excerpt_tokens": 192, "max_response_tokens": 6000}

    def save(self, name, body):
        atomic_json(self.root / (name + ".json"), body)
        return body

    def start(self):
        try:
            status = self.client.get("management/status")
            record = load(self.root / "active-process.json")
            p = psutil.Process(record["service_pid"])
            assert (
                str(self.config) in p.cmdline()
                and "evaluation/tools/fault_server.py" in p.cmdline()
            )
            return status
        except (Problem, psutil.Error, FileNotFoundError):
            pass
        stream = (self.root / "service.log").open("ab")
        launcher = subprocess.Popen(
            [
                sys.executable,
                "-X",
                "utf8",
                "evaluation/tools/fault_server.py",
                "--config",
                str(self.config),
                "--control",
                str(self.control),
                "--output",
                str(self.faults),
            ],
            cwd=MODULE,
            stdout=stream,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        stream.close()
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            assert launcher.poll() is None, "Fault service exited; inspect service.log"
            try:
                status = self.client.get("management/status")
                processes = [
                    psutil.Process(launcher.pid),
                    *psutil.Process(launcher.pid).children(recursive=True),
                ]
                matched = [
                    p
                    for p in processes
                    if str(self.config) in p.cmdline()
                    and Path(p.cwd()).resolve() == MODULE.resolve()
                    and any(
                        connection.status == psutil.CONN_LISTEN
                        and connection.laddr.port == self.settings.port
                        for connection in p.net_connections(kind="tcp")
                    )
                ]
                assert len(matched) == 1
                self.save(
                    "active-process",
                    {
                        "service_pid": matched[0].pid,
                        "launcher_pid": launcher.pid,
                        "command": matched[0].cmdline(),
                        "at": utc_now(),
                    },
                )
                return status
            except Problem:
                time.sleep(2)
        raise TimeoutError("Fault service startup timed out")

    def stop(self, *, reason):
        record = load(self.root / "active-process.json")
        p = psutil.Process(record["service_pid"])
        assert Path(p.cwd()).resolve() == MODULE.resolve()
        assert str(self.config) in p.cmdline() and "evaluation/tools/fault_server.py" in p.cmdline()
        before = {"pid": p.pid, "command": p.cmdline(), "at": utc_now(), "reason": reason}
        p.kill()
        p.wait(60)
        return {
            **before,
            "actual_terminated": not psutil.pid_exists(p.pid),
            "completed_at": utc_now(),
        }

    def arm(self, identity, point, **filters):
        value = load(self.control)
        value["armed"] = {"id": identity, "point": point, **filters}
        atomic_json(self.control, value)

    def disarm(self):
        value = load(self.control)
        value.pop("armed", None)
        atomic_json(self.control, value)

    def clock(self, offset):
        value = load(self.control)
        value["clock_offset_seconds"] = offset
        atomic_json(self.control, value)

    def marker(self, identity, timeout=600):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            path = self.faults / (identity + ".json")
            if path.exists():
                return load(path)
            time.sleep(0.2)
        raise TimeoutError(identity)

    def store(self):
        return ControlStore(
            self.settings.state_root / "control.sqlite3",
            clock=lambda: time.time() + load(self.control).get("clock_offset_seconds", 0),
        )

    def wait(self, job_id, timeout=1200, *, allow_failed=False):
        deadline, delay = time.monotonic() + timeout, 0.5
        while time.monotonic() < deadline:
            job = self.client.get("jobs/" + job_id)
            if job["execution_state"] in ("completed", "failed", "cancelled"):
                assert allow_failed or job["execution_state"] == "completed", job
                return job
            time.sleep(delay)
            delay = min(delay * 1.5, 5)
        raise TimeoutError(job_id)

    def index(self, label, *, scope=None, kind="rebuild", activate=True, wait=True):
        receipt = self.client.post(
            "management/index-jobs",
            {
                "type": kind,
                "scope": scope or self.scope,
                "activate_when_ready": activate,
            },
            self.run_id + ":" + label,
        )
        self.save(label + "-receipt", receipt)
        return self.wait(receipt["job_id"]) if wait else receipt

    def search(
        self, query, *, mode="exact_quote", scope=None, purpose="card_input", key=None, **extra
    ):
        selection = scope or self.main
        body = {
            "query": query,
            "mode": mode,
            "scope": selection,
            "purpose": purpose,
            "budget": self.budget,
            "intent": "supplemental" if selection == self.scope else "within_source",
            **extra,
        }
        return self.client.completed(
            self.client.post("searches", body, key or new_id("engineering")), body["budget"]
        )

    def read(self, item, *, layer="passage", budget=None, purpose="card_input"):
        return self.client.post(
            "reads",
            {
                "selection": {"kind": "evidence", "evidence_ref": item["evidence_ref"]},
                "layer": layer,
                "purpose": purpose,
                "budget": budget or {**self.budget, "max_excerpt_tokens": 4096},
            },
        )

    def expect_problem(self, operation, codes):
        try:
            operation()
        except Problem as error:
            assert error.code in codes, {
                "code": error.code,
                "expected": codes,
                "detail": error.detail,
            }
            return {"code": error.code, "status": error.status}
        raise AssertionError("Expected a protocol error")

    def upstream_apply(self, label, operations, snapshots=None):
        """Public draft/preview/apply only; never reads the ingestion database."""
        folder = self.root / "upstream" / label
        folder.mkdir(parents=True, exist_ok=True)

        def request(method, path, body=None, suffix=""):
            response = httpx.request(
                method,
                "http://127.0.0.1:18125/api/v1/" + path,
                json=body,
                headers={"Idempotency-Key": self.run_id + ":" + label + ":" + suffix},
                timeout=300,
            )
            value = response.json()
            atomic_json(
                folder / (new_id("http") + ".json"),
                {
                    "method": method,
                    "path": path,
                    "request": body,
                    "status": response.status_code,
                    "response": value,
                },
            )
            assert not response.is_error, value
            return value

        body = {
            "kind": "catalogue",
            "scope": {"target_type": "catalogue"},
            "base_snapshot_ids": snapshots or [self.fixture["carrier_snapshot"]],
            "reason": "Labelled synthetic retrieval protocol experiment.",
            "operations": operations,
        }
        draft = request("POST", "drafts", body, "draft")
        preview = request(
            "POST",
            f"drafts/{draft['draft_id']}/previews",
            {"draft_revision_id": draft["current_revision_id"]},
            "preview",
        )
        assert all(g["state"] == "ready" for g in preview["groups"]), preview
        applied = request(
            "POST",
            f"drafts/{draft['draft_id']}/applications",
            {
                "draft_revision_id": draft["current_revision_id"],
                "preview_id": preview["preview_id"],
                "preview_fingerprint": preview["fingerprint"],
                "selected_group_ids": [g["group_id"] for g in preview["groups"]],
                "reason": "Run the labelled synthetic protocol check.",
            },
            "apply",
        )
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            result = request("GET", "jobs/" + applied["job"]["job_id"])
            if result["execution_state"] in ("completed", "failed", "cancelled"):
                assert (
                    result["execution_state"] == "completed"
                    and result["result"]["outcome"] == "succeeded"
                ), result
                return {
                    "result": result,
                    "allocated": {
                        k: v for g in preview["groups"] for k, v in g["allocated_ids"].items()
                    },
                }
            time.sleep(1)
        raise TimeoutError("Upstream application")

    def finish(self, name, evidence):
        result = self.save(
            name,
            {
                "state": "passed",
                "classification": "synthetic_actual_protocol",
                "completed_at": utc_now(),
                "evidence": evidence,
            },
        )
        print(json.dumps({"group": name, "state": result["state"]}), flush=True)
        return result
