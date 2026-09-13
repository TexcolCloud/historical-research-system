"""Recover a timed-out result wait without submitting or counting a new search."""

import argparse
import json
import time
from pathlib import Path

from run_gpt_quality import completed, digest, load
from run_quality import MODULE, TraceClient

from document_retrieval.records import atomic_json, utc_now


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--url", default="http://127.0.0.1:18130")
    args = parser.parse_args()
    root = MODULE / "evaluation/runs" / args.run_id
    manifest = load(root / "run.json")
    assert manifest["source_sha256"] == {str(p): digest(p) for p in (MODULE / "src").rglob("*.py")}
    task_path = MODULE / "evaluation/datasets" / manifest["dataset_id"] / "tasks.json"
    assert digest(task_path) == manifest["tasks_sha256"]
    task = next(t for t in load(task_path) if t["id"] == args.task_id)
    folder = root / args.task_id
    assert not (folder / "result.json").exists()
    state_path = folder / "state.json"
    state = load(state_path)
    assert not state["searches"] and state["new_searches"] == 1
    assert len(task["source_scopes"]) == 1
    assert state["errors"] == [
        {
            "type": "Problem",
            "detail": "The retrieval service is unavailable; preserve and reuse the operation key.",
        }
    ]
    recovery_path = folder / "existing-search-recovery.json"
    assert not recovery_path.exists()
    atomic_json(folder / "state-before-search-recovery.json", state)
    client = TraceClient(args.url, folder / "http")
    client.rows = [load(p) for p in sorted((folder / "http").glob("*.json"))]
    client.sequence = len(client.rows)
    initial = next(r for r in client.rows if r["url"].endswith("/searches"))
    receipt = json.loads(initial["response_text"])
    submitted = json.loads(initial["request_text"])
    assert receipt["job_id"] and initial["status"] == 202
    began = time.monotonic()
    client.action = "recovery_existing_job_status"
    status = client.get("jobs/" + receipt["job_id"])
    assert status["scope_ref"] == receipt["scope_ref"]
    response = completed(client, status, submitted["budget"])
    assert response["scope_ref"] == receipt["scope_ref"]
    assert response["list_id"] == status["result_ref"]["list_id"]
    state["searches"].append(
        {"family": task["source_scopes"][0]["family"], "query": submitted["query"], **response}
    )
    proof = {
        "classification": "existing_search_job_completed",
        "reviewer": "active_Codex_GPT",
        "at": utc_now(),
        "job_id": receipt["job_id"],
        "scope_ref": response["scope_ref"],
        "list_id": response["list_id"],
        "resolved_error_indices": [0],
        "new_content_calls": 0,
        "basis": "The original accepted job completed. Only its status and result were read; no search was resubmitted. The earlier timeout remains in errors.",
        "before_state_sha256": digest(folder / "state-before-search-recovery.json"),
        "original_receipt_sha256": initial["sha256"],
        "recovered_response_sha256": client.rows[-1]["sha256"],
        "helper_sha256": digest(Path(__file__)),
    }
    atomic_json(recovery_path, proof)
    state.setdefault("recoveries", []).append(
        {"path": str(recovery_path.resolve()), "sha256": digest(recovery_path), **proof}
    )
    state["decisions"].append(
        {
            "task_id": args.task_id,
            "action": "resume_existing_search",
            "basis": proof["basis"],
            "at": utc_now(),
            "reviewer": "active_Codex_GPT",
        }
    )
    state["seconds"] += time.monotonic() - began
    state["http_responses"] = len(client.rows)
    state["tool_text_proxy_tokens"] = sum(r["independent_proxy_tokens"] for r in client.rows)
    state["tool_utf8_bytes"] = sum(len(r["response_text"].encode("utf-8")) for r in client.rows)
    atomic_json(state_path, state)
    print(json.dumps(response, ensure_ascii=False))
    client.close()


if __name__ == "__main__":
    main()
