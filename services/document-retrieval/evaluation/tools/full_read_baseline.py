"""Actually read every purpose-qualified source range once per fixed-scope session.

Tasks sharing the same source set share one complete reading. This baseline can see
all fixed input text, but never opens retrieval references or chooses correct answers.
It measures actual batched tool responses; no generative model call is claimed.
"""

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path

from run_quality import MODULE, TraceClient

from document_retrieval.client import AgentTools
from document_retrieval.records import atomic_json, fingerprint, utc_now
from document_retrieval.upstream import field_allowed


def key(anchor):
    return tuple(anchor[k] for k in ("snapshot_id", "source_span_ref", "start", "end"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset",
        type=Path,
        default=MODULE / "evaluation/datasets/retrieval-representative-20260908-v6",
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--split", choices=("calibration", "holdout"), default="holdout")
    parser.add_argument("--url", default="http://127.0.0.1:18130")
    parser.add_argument(
        "--caller-task-prefix", help="Use the compared run's session label verbatim"
    )
    args = parser.parse_args()
    root = MODULE / "evaluation/runs" / args.run_id
    assert not root.exists(), "Full-read measurements are immutable; use a new run ID"
    root.mkdir(parents=True)
    lock = json.loads((args.dataset / "lock.json").read_text("utf-8"))
    data = {}
    for name in ("corpus.json", "tasks.json"):
        path = args.dataset / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == lock["files"][name]
        data[name] = json.loads(path.read_text("utf-8"))
    fixed_by_snapshot = {}
    for family in data["corpus.json"]:
        path = Path(family["fixed_inputs_path"])
        for fixed in json.loads(path.read_text("utf-8")):
            fixed_by_snapshot[fixed["snapshot"]["snapshot_id"]] = fixed
    sessions = defaultdict(list)
    for task in data["tasks.json"]:
        if task["split"] == args.split:
            ids = tuple(
                sorted(
                    {s["snapshot_id"] for g in task["source_scopes"] for s in g["scope"]["sources"]}
                )
            )
            sessions[ids].append(task["id"])
    manifest = {
        "dataset_id": lock["dataset_id"],
        "run_id": args.run_id,
        "split": args.split,
        "started_at": utc_now(),
        "session_rule": "one complete qualified reading per identical fixed source set",
        "generative_model": None,
        "model_prompt_tokens": "unknown_not_a_model_execution",
        "measurement": "actual serialized tool text with independently checked BGE proxy tokens",
        "references_accessed": False,
        "driver_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "source_sha256": {
            str(p): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (MODULE / "src").rglob("*.py")
        },
        "caller_task_prefix": args.caller_task_prefix,
        "caller_label_rule": "same prefix plus :s- and 12-character fixed-source fingerprint"
        if args.caller_task_prefix
        else "legacy full-read run label",
    }
    atomic_json(root / "run.json", manifest)
    summaries = []
    budget = {
        "max_items": 50,
        "max_excerpt_tokens": 4096,
        "max_response_tokens": 64000,
        "counter_ref": "bge-m3-proxy-v1",
    }
    for ids, task_ids in sessions.items():
        session_id = "full-" + fingerprint(ids)[:16]
        folder = root / session_id
        folder.mkdir()
        client = TraceClient(args.url, folder / "http")
        agent = AgentTools(client)
        result = {
            "session_id": session_id,
            "task_ids": task_ids,
            "snapshot_ids": ids,
            "retentions": [],
            "reads": [],
            "errors": [],
        }
        wanted = []
        for identity in ids:
            fixed = fixed_by_snapshot[identity]
            permitted = {
                key(item["reference"])
                for batch in fixed["evaluation_usage"]["card_input"]
                for item in batch["items"]
                if field_allowed(item, ["text", "provenance"])
            }
            for source in fixed["sources"]:
                if key(source) in permitted:
                    wanted.append(
                        {
                            k: source[k]
                            for k in (
                                "snapshot_id",
                                "source_span_ref",
                                "start",
                                "end",
                                "offset_unit",
                                "text_sha256",
                            )
                        }
                    )

        def invoke(name, body, action, operation_key=None, *, client=client, agent=agent):
            client.action = action
            raw = agent.invoke(name, body, operation_key=operation_key)
            assert raw == client.last_raw
            return json.loads(raw)

        try:
            for identity in ids:
                result["retentions"].append(
                    invoke(
                        "retain_inputs",
                        {
                            "kind": "create",
                            "scope": {"kind": "snapshot", "snapshot_id": identity},
                            "caller_task_ref": (
                                args.caller_task_prefix + ":s-" + fingerprint(ids)[:12]
                                if args.caller_task_prefix
                                else args.run_id + ":" + session_id
                            ),
                        },
                        "retention",
                        args.run_id + ":" + session_id + ":retain:" + identity,
                    )
                )
            for offset in range(0, len(wanted), 200):
                body = {
                    "selection": {
                        "kind": "anchors",
                        "source_anchors": wanted[offset : offset + 200],
                    },
                    "purpose": "card_input",
                    "layer": "passage",
                    "budget": budget,
                }
                seen_cursors = set()
                while True:
                    reading = invoke("read_evidence", body, "full_read")
                    result["reads"].append(reading)
                    cursor = reading.get("read_cursor")
                    if not cursor:
                        break
                    assert cursor not in seen_cursors, "No-progress read cursor"
                    seen_cursors.add(cursor)
                    body = {
                        "selection": {"kind": "continue", "read_cursor": cursor},
                        "budget": budget,
                    }
        except Exception as error:  # noqa: BLE001 -- preserve an incomplete actual baseline.
            result["errors"].append({"type": type(error).__name__, "detail": str(error)})
        finally:
            for retained in result["retentions"]:
                try:
                    invoke(
                        "release_inputs",
                        {"retention_id": retained["retention_id"]},
                        "release",
                        args.run_id + ":" + session_id + ":release:" + retained["retention_id"],
                    )
                except Exception as error:  # noqa: BLE001
                    result["errors"].append({"phase": "release", "detail": str(error)})
            units = [u for r in result["reads"] for u in r.get("units", []) if "text" in u]
            from score_quality import covered

            missing = [a for a in wanted if not covered(a, [u["source_anchor"] for u in units])]
            result.update(
                {
                    "completed_at": utc_now(),
                    "required_qualified_ranges": len(wanted),
                    "required_ranges_sha256": fingerprint(wanted),
                    "missing_ranges": missing,
                    "complete_reading": not missing and not result["errors"],
                    "returned_unicode_code_points": sum(len(u["text"]) for u in units),
                    "tool_text_proxy_tokens": sum(
                        r["independent_proxy_tokens"] for r in client.rows
                    ),
                    "http_responses": len(client.rows),
                }
            )
            atomic_json(folder / "result.json", result)
            client.close()
        summary = {
            k: result[k]
            for k in (
                "session_id",
                "task_ids",
                "complete_reading",
                "required_qualified_ranges",
                "returned_unicode_code_points",
                "tool_text_proxy_tokens",
                "http_responses",
                "errors",
            )
        }
        summaries.append(summary)
        print(json.dumps(summary, ensure_ascii=False), flush=True)
        atomic_json(root / "progress.json", summaries)
    atomic_json(
        root / "completed.json",
        {
            "sessions": len(summaries),
            "at": utc_now(),
            "all_complete": all(s["complete_reading"] for s in summaries),
        },
    )


if __name__ == "__main__":
    main()
