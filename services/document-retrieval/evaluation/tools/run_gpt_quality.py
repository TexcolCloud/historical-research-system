"""Execute recorded active-GPT decisions through the bounded public tool client.

This runner loads tasks, never references or upstream data. Decisions arrive as an
immutable JSON instruction file after the active GPT has read actual tool output.
Identical source sets share retained inputs and already received text in one session.
"""

import argparse
import hashlib
import json
import time
from pathlib import Path

from run_quality import MODULE, TraceClient

from document_retrieval.client import AgentTools, RetrievalClient
from document_retrieval.errors import Problem
from document_retrieval.records import atomic_json, fingerprint, utc_now

POLICY = """The active Codex GPT reads each task and actual public responses, then
records its next query, expansion or stop decision with an explicit reason. Use only
visible evidence references and cursors. Do not access reference answers, hidden
candidates, fixed-input text or upstream databases to choose hits. Preserve unresolved
needs at the two-search/four-expansion/six-content-call limit. Questions with identical
fixed source sets share a session: retain once, reuse actually received evidence, and
release after its last task. Reuse is not a new permission observation. Each task's
first batch is its own first query response. A task with no query has no new first
batch. Every executed HTTP JSON response is counted once, including administrative
responses. Media bytes and visual use are separate. The same GPT built references
and reviews candidates; this is not an independent blind review. Host prompt, wrapper,
conversation replay and image token counts are unavailable, not zero. Machine
completion is not user acceptance or sealed gold approval.
"""


def load(path):
    return json.loads(path.read_text("utf-8"))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def session_key(task):
    return fingerprint(
        sorted(
            source["snapshot_id"]
            for group in task["source_scopes"]
            for source in group["scope"]["sources"]
        )
    )


def completed(client, value, budget):
    if not value.get("job_id"):
        return value
    identity = value["job_id"]
    end = time.monotonic() + 900
    client.action = "result_wait"
    while time.monotonic() < end:
        value = client.request(
            "POST",
            f"jobs/{identity}/result",
            body={"budget": budget},
            params={"wait_seconds": 30},
        )
        if not value.get("job_id"):
            return value
    raise Problem("evaluation_wait_timeout", "Preserve the original job identity.", status=504)


def run_action(command, args, tasks, root):
    task = next(t for t in tasks if t["id"] == command["task_id"])
    folder = root / task["id"]
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "state.json"
    session_path = root / "sessions" / (session_key(task) + ".json")
    assert not (folder / "result.json").exists(), "A completed task is immutable"
    client = TraceClient(args.url, folder / "http")
    client.rows = [load(p) for p in sorted((folder / "http").glob("*.json"))]
    client.sequence = len(client.rows)
    prior_count = len(client.rows)
    agent = AgentTools(client)
    start = time.monotonic()
    search_budget = {
        "max_items": 6,
        "max_excerpt_tokens": 128,
        "max_response_tokens": 2000,
        "counter_ref": "bge-m3-proxy-v1",
    }
    read_budget = {**search_budget, "max_excerpt_tokens": 4096, "max_response_tokens": 4000}
    result = load(path) if path.exists() else None
    session = (
        load(session_path)
        if session_path.exists()
        else {"retentions": {}, "completed_tasks": [], "created_at": utc_now()}
    )
    stop = False

    def invoke(name, body, action):
        client.action = action
        raw = agent.invoke(
            name,
            body,
            operation_key=f"{args.run_id}:{task['id']}:{client.sequence}:{action}",
        )
        assert raw == client.last_raw, "Agent tool text must equal actual HTTP response bytes"
        return json.loads(raw)

    try:
        if command["action"] == "begin":
            assert result is None
            reused = [
                load(root / identity / "result.json") for identity in session["completed_tasks"]
            ]
            result = {
                "task_id": task["id"],
                "started_at": utc_now(),
                "seconds": 0,
                "driver": "active_Codex_GPT_public_tool_decisions_v1",
                "searches": [],
                "reads": [
                    r for prior in reused for r in prior["reads"] if not r.get("session_reuse")
                ],
                "retentions": [],
                "media": [],
                "decisions": [],
                "errors": [],
                "new_searches": 0,
                "expansions": 0,
                "session_id": session_key(task),
                "session_reuse": [
                    {
                        "task_id": prior["task_id"],
                        "result_sha256": digest(root / prior["task_id"] / "result.json"),
                    }
                    for prior in reused
                ],
                "reuse_is_new_permission_observation": False,
            }
            result["reads"] = [{**r, "session_reuse": True} for r in result["reads"]]
            print(
                json.dumps(
                    {"task": task, "reused_tasks": result["session_reuse"]}, ensure_ascii=False
                )
            )
            for group in task["source_scopes"]:
                if group["family"] not in session["retentions"]:
                    retained = invoke(
                        "retain_inputs",
                        {
                            "kind": "create",
                            "scope": group["scope"],
                            "caller_task_ref": args.run_id + ":s-" + session_key(task)[:12],
                        },
                        "retention",
                    )
                    session["retentions"][group["family"]] = retained
                result["retentions"].append(session["retentions"][group["family"]])
        else:
            assert result is not None, "Begin the task before taking a content action"
            assert command.get("basis"), "Record a reason grounded in the question/public output"
            action = command["action"]
            if action == "search":
                assert result["new_searches"] < 2
                group = next(g for g in task["source_scopes"] if g["family"] == command["family"])
                query = command.get("query", group.get("query", task["query"]))
                result["new_searches"] += 1
                initial = invoke(
                    "search_evidence",
                    {
                        "kind": "search",
                        "query": query,
                        "mode": task["mode"],
                        "purpose": task["purpose"],
                        "scope": group["scope"],
                        "intent": "supplemental"
                        if len(group["scope"]["sources"]) > 1
                        else "within_source",
                        "budget": search_budget,
                        "retention_id": session["retentions"][group["family"]]["retention_id"],
                    },
                    "search",
                )
                response = completed(client, initial, search_budget)
                result["searches"].append({"family": group["family"], "query": query, **response})
            elif action in ("read", "continue", "list_continue"):
                assert result["expansions"] < 4
                visible = [
                    result,
                    *[load(root / t / "result.json") for t in session["completed_tasks"]],
                ]
                if action == "list_continue":
                    listing = next(
                        s
                        for r in visible
                        for s in r["searches"]
                        if s.get("next_cursor") == command["cursor"]
                    )
                    result["expansions"] += 1
                    response = invoke(
                        "search_evidence",
                        {
                            "kind": "continue",
                            "list_id": listing["list_id"],
                            "cursor": command["cursor"],
                            "budget": search_budget,
                        },
                        "list_continue",
                    )
                    result["searches"].append(
                        {"family": listing["family"], "continued": True, **response}
                    )
                else:
                    if action == "continue":
                        assert any(
                            r.get("read_cursor") == command["cursor"]
                            for v in visible
                            for r in v["reads"]
                        )
                        selection = {"kind": "continue", "read_cursor": command["cursor"]}
                        body = {"selection": selection, "budget": read_budget}
                        layer = "continued"
                    else:
                        reference = command["evidence_ref"]
                        assert any(
                            i["evidence_ref"] == reference
                            for v in visible
                            for s in v["searches"]
                            for i in s.get("items", [])
                        )
                        layer = command.get("layer", "neighbors")
                        assert layer in ("passage", "neighbors", "related", "media", "full_scope")
                        body = {
                            "selection": {"kind": "evidence", "evidence_ref": reference},
                            "purpose": "source_reading" if layer == "media" else task["purpose"],
                            "layer": layer,
                            "budget": read_budget,
                        }
                    result["expansions"] += 1
                    response = invoke("read_evidence", body, "read:" + layer)
                    result["reads"].append({"layer": layer, **body["selection"], **response})
            elif action == "media":
                reference, asset_id = command["evidence_ref"], command["asset_id"]
                assert any(
                    r.get("evidence_ref") == reference and a["asset_id"] == asset_id
                    for r in result["reads"]
                    for u in r.get("units", [])
                    for a in u.get("assets", [])
                )
                client.action = "media_bytes"
                with client.media(reference, asset_id, purpose="source_reading") as response:
                    payload = b"".join(response.iter_bytes())
                    mime = response.headers.get("content-type", "").split(";", 1)[0]
                    media_path = folder / (
                        "returned-media-"
                        + str(len(result["media"]))
                        + (".png" if mime == "image/png" else ".bin")
                    )
                    media_path.write_bytes(payload)
                    result["media"].append(
                        {
                            "evidence_ref": reference,
                            "asset_id": asset_id,
                            "http_status": response.status_code,
                            "headers": dict(response.headers),
                            "path": str(media_path.resolve()),
                            "bytes": len(payload),
                            "sha256": digest(media_path),
                            "visual_review": "pending_active_GPT_review",
                        }
                    )
                    if response.status_code != 200:
                        result["errors"].append(
                            {"phase": "media", "http_status": response.status_code}
                        )
                    print(json.dumps(result["media"][-1], ensure_ascii=False))
            elif action == "stop":
                stop = True
                session["completed_tasks"].append(task["id"])
                peers = [t["id"] for t in tasks if session_key(t) == session_key(task)]
                if set(peers) <= set(session["completed_tasks"]):
                    for retained in session["retentions"].values():
                        invoke(
                            "release_inputs", {"retention_id": retained["retention_id"]}, "release"
                        )
                    session["released_at"] = utc_now()
            else:
                raise ValueError("Unknown action: " + action)
        result["decisions"].append({**command, "at": utc_now(), "reviewer": "active_Codex_GPT"})
    except Exception as error:
        if result is not None:
            result["errors"].append({"type": type(error).__name__, "detail": str(error)})
        raise
    finally:
        if result is not None:
            result.update(
                {
                    "seconds": result["seconds"] + time.monotonic() - start,
                    "content_calls": result["new_searches"] + result["expansions"],
                    "http_responses": len(client.rows),
                    "tool_text_proxy_tokens": sum(
                        r["independent_proxy_tokens"] for r in client.rows
                    ),
                    "tool_utf8_bytes": sum(
                        len(r["response_text"].encode("utf-8")) for r in client.rows
                    ),
                }
            )
            assert result["content_calls"] <= 6
            atomic_json(path, result)
            atomic_json(session_path, session)
            if stop:
                atomic_json(folder / "result.json", {**result, "completed_at": utc_now()})
        for row in client.rows[prior_count:]:
            print(row["response_text"], flush=True)
        client.close()
    return stop


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset",
        type=Path,
        default=MODULE / "evaluation/datasets/retrieval-representative-20260908-v6",
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--split", default="holdout", choices=("holdout", "calibration"))
    parser.add_argument("--task-ids", nargs="+")
    parser.add_argument("--instructions", type=Path, required=True)
    parser.add_argument("--url", default="http://127.0.0.1:18130")
    args = parser.parse_args()
    lock = load(args.dataset / "lock.json")
    assert digest(args.dataset / "tasks.json") == lock["files"]["tasks.json"]
    tasks = [t for t in load(args.dataset / "tasks.json") if t["split"] == args.split]
    if args.task_ids:
        assert set(args.task_ids) <= {t["id"] for t in tasks}
        tasks = [t for t in tasks if t["id"] in args.task_ids]
    root = MODULE / "evaluation/runs" / args.run_id
    root.mkdir(parents=True, exist_ok=True)
    hashes = {str(p): digest(p) for p in (MODULE / "src").rglob("*.py")}
    if (root / "run.json").exists():
        manifest = load(root / "run.json")
        assert manifest["driver_sha256"] == digest(Path(__file__))
        assert manifest["source_sha256"] == hashes
        assert manifest["selected_task_ids"] == [t["id"] for t in tasks]
    else:
        client = RetrievalClient(args.url)
        try:
            status = client.get("management/status")
            generation = client.get("management/generations/" + status["active_generation"])
        finally:
            client.close()
        atomic_json(
            root / "run.json",
            {
                "run_id": args.run_id,
                "dataset_id": lock["dataset_id"],
                "split": args.split,
                "selected_task_ids": [t["id"] for t in tasks],
                "task_scope": "explicit_diagnostic_subset" if args.task_ids else "complete_split",
                "tasks_sha256": lock["files"]["tasks.json"],
                "policy": POLICY,
                "driver_sha256": digest(Path(__file__)),
                "source_sha256": hashes,
                "generative_driver_model": "active_Codex_GPT",
                "reference_files_accessed_by_driver": False,
                "host_model_prompt_tokens": "unavailable",
                "same_GPT_reference_builder_and_judge": True,
                "independent_blind_review": False,
                "budget": {"excerpt": 128, "response": 2000, "read": 4000},
                "service_status_at_start": status,
                "active_generation_at_start": generation,
                "started_at": utc_now(),
            },
        )
    commands = load(args.instructions)
    required = {
        "begin": (),
        "stop": ("basis",),
        "search": ("basis", "family"),
        "read": ("basis", "evidence_ref", "layer"),
        "continue": ("basis", "cursor"),
        "list_continue": ("basis", "cursor"),
        "media": ("basis", "evidence_ref", "asset_id"),
    }
    for command in commands:
        assert command["task_id"] in {t["id"] for t in tasks}
        assert command["action"] in required
        assert all(command.get(key) for key in required[command["action"]]), command
    archive = root / "instructions" / (digest(args.instructions) + ".json")
    assert not archive.exists(), "Do not replay a completed instruction file"
    atomic_json(archive, commands)
    for command in commands:
        run_action(command, args, tasks, root)
    if all((root / task["id"] / "result.json").exists() for task in tasks):
        atomic_json(
            root / "completed.json",
            {
                "tasks": len(tasks),
                "completed_at": utc_now(),
                "quality_scoring": "pending_separate_reference_comparison",
            },
        )


if __name__ == "__main__":
    main()
