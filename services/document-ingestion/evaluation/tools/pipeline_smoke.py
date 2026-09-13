"""A consumer's complete HTTP workflow, also executable against Linux containers."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

from document_ingestion.client import ManagementClient
from document_ingestion.packing import pack_directory


def run(origin, output):
    output.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests"))
    from test_imports import engineering_standard_files

    files, _ = engineering_standard_files()
    directory = output / "工程来源"
    for relative, raw in files.items():
        target = directory / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    archive = output / "工程包.zip"
    packed = pack_directory(directory, directory / "original.png", archive)
    container = os.environ.get("INGEST_SMOKE_CONTAINER")
    if container:
        folder = "/data/工程来源-" + uuid4().hex
        subprocess.run(
            ["docker", "cp", str(directory), container + ":" + folder],
            check=True,
            capture_output=True,
        )
        cli = ["docker", "exec", container, "history-ingest"]
        packed_result = subprocess.run(
            [
                *cli,
                "pack",
                folder,
                "--original",
                folder + "/original.png",
                "--output",
                folder + ".zip",
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        packed = json.loads(packed_result.stdout)
        command = [
            *cli,
            "--url",
            "http://127.0.0.1:8766",
            "submit",
            "--package",
            folder + ".zip",
            "--receipt",
            folder + ".receipt.json",
        ]
    else:
        command = [
            sys.executable,
            "-m",
            "document_ingestion.cli",
            "--url",
            origin,
            "submit",
            "--package",
            str(archive),
            "--receipt",
            str(output / "submit.json"),
        ]
    result = subprocess.run(
        [*command, "--display-name", "工程验收来源", "--wait"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    submitted = json.loads(result.stdout)
    client = ManagementClient(origin)
    # The thin CLI result holds the completed durable import job.
    receipt = submitted["import"]["receipt"]
    partition = client.wait_job(receipt["partition_job_id"], 60)
    assert partition["execution_state"] == "completed", partition
    candidate = partition["result"]
    assert candidate["candidate_count"] == 1
    manifest = client.get(f"/api/v1/drafts/{candidate['draft_id']}/manifest")
    draft = client.post("/api/v1/draft-manifests", manifest, "linux-roundtrip")
    preview = client.post(
        f"/api/v1/drafts/{draft['draft_id']}/previews",
        {"draft_revision_id": draft["current_revision_id"]},
        "linux-preview",
    )
    assert all(group["state"] == "ready" for group in preview["groups"]), preview
    applied = client.post(
        f"/api/v1/drafts/{draft['draft_id']}/applications",
        {
            "draft_revision_id": draft["current_revision_id"],
            "preview_id": preview["preview_id"],
            "preview_fingerprint": preview["fingerprint"],
            "selected_group_ids": [g["group_id"] for g in preview["groups"]],
            "reason": "Engineering automation; no human source approval",
        },
        "linux-apply",
    )
    application_job = client.wait_job(applied["job"]["job_id"], 60)
    assert application_job["result"]["outcome"] == "succeeded", application_job
    snapshot_id = receipt["snapshot_id"]
    segments = client.get(f"/api/v1/snapshots/{snapshot_id}/segments")
    first = segments["items"][0]
    assert hashlib.sha256(first["text"].encode()).hexdigest() == first["text_sha256"]
    source = {key: first[key] for key in ("snapshot_id", "source_span_ref", "start", "end")}
    source["expected_text_sha256"] = first["text_sha256"]
    usage = client.post(
        "/api/v1/usage-checks",
        {"purpose": "card_input", "requested_fields": ["text"], "references": [source]},
        "unused-read-only",
    )
    baseline_request = client.post(
        "/api/v1/research-baselines", {"scope": {"kind": "all_current"}}, "linux-baseline"
    )
    baseline_job = client.wait_job(baseline_request["job_id"], 60)
    assert baseline_job["execution_state"] == "completed", baseline_job
    baseline = client.get(f"/api/v1/research-baselines/{baseline_request['baseline_id']}")
    members = client.get(f"/api/v1/research-baselines/{baseline_request['baseline_id']}/items")
    events = client.get("/api/v1/changes?after=0")
    assert members["items"] and events["items"]
    report = {
        "classification": "engineering_http_full_pipeline",
        "package": packed,
        "submitted": submitted,
        "partition": partition,
        "application": application_job,
        "snapshot_id": snapshot_id,
        "segments": segments,
        "usage": usage,
        "baseline": baseline,
        "members": members,
        "events": events,
        "human_review": False,
        "sealed_gold": False,
    }
    (output / "pipeline.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def resume(origin, output):
    report = json.loads((output / "pipeline.json").read_text(encoding="utf-8"))
    client = ManagementClient(origin)
    assert client.get(f"/api/v1/snapshots/{report['snapshot_id']}/segments") == report["segments"]
    assert (
        client.get(f"/api/v1/research-baselines/{report['baseline']['baseline_id']}/items")
        == report["members"]
    )
    assert client.get("/api/v1/changes?after=0") == report["events"]
    report["restart_readback"] = "passed"
    (output / "pipeline.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:58766")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    result = (resume if args.resume else run)(args.url, args.output)
    print(json.dumps({"outcome": "passed", "snapshot_id": result["snapshot_id"]}))
