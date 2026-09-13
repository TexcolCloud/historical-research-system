"""Measure one actual public read; preserve all returned bytes as input diagnostics."""

import argparse
import time
from pathlib import Path

import httpx

from research_cards.records import atomic_json, now, read_json, sha256

parser = argparse.ArgumentParser()
parser.add_argument("--state", type=Path, required=True)
parser.add_argument("--task", required=True)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
responses = [read_json(p) for p in (args.state / "tasks" / args.task / "calls").glob("*-response.json")]
retention = next(row["response"] for row in responses if isinstance(row.get("response"), dict) and row["response"].get("retention_id"))
body = {"selection": {"kind": "retention", "retention_id": retention["retention_id"]},
    "purpose": "card_input", "layer": "full_scope",
    "budget": {"max_items": 50, "max_excerpt_tokens": 4096, "max_response_tokens": 16000}}
args.output.mkdir(parents=True, exist_ok=True)
atomic_json(args.output / "request.json", body)
start = time.monotonic()
try:
    with httpx.Client(timeout=600) as client:
        response = client.post("http://127.0.0.1:18130/api/v1/reads", json=body)
    result = response.json()
    atomic_json(args.output / "response.json", result)
    summary = {"at": now(), "elapsed_seconds": time.monotonic() - start, "status": response.status_code,
        "response_bytes": len(response.content), "returned_units": len(result.get("units", [])),
        "has_cursor": bool(result.get("read_cursor")), "code": result.get("code"),
        "response_file_sha256": sha256((args.output / "response.json").read_bytes()),
        "diagnostic_only": True, "model_calls": 0, "card_output_reused": False}
except httpx.HTTPError as error:
    summary = {"at": now(), "elapsed_seconds": time.monotonic() - start,
        "error_class": type(error).__name__, "diagnostic_only": True, "model_calls": 0}
atomic_json(args.output / "summary.json", summary)
print(summary)
