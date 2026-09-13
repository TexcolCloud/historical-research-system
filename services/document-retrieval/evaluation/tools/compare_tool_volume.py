"""Compare actual tool bodies with one full reading per identical source set."""

import argparse
import hashlib
import json
from pathlib import Path

from run_quality import MODULE

from document_retrieval.records import atomic_json, utc_now


def load(path):
    return json.loads(path.read_text("utf-8"))


def evidence(path):
    return {"path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--retrieval-run", required=True)
    parser.add_argument("--full-run", required=True)
    parser.add_argument("--score", default="mechanical-score-v1.json")
    parser.add_argument("--version", default="v1")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=MODULE / "evaluation/datasets/retrieval-representative-20260908-v6",
    )
    args = parser.parse_args()
    retrieval = MODULE / "evaluation/runs" / args.retrieval_run
    baseline = MODULE / "evaluation/runs" / args.full_run
    assert (retrieval / "completed.json").exists() and (baseline / "completed.json").exists()
    target = retrieval / ("tool-volume-comparison-" + args.version + ".json")
    assert not target.exists(), "Preserve earlier comparisons; choose a new version"
    retrieval_manifest, full_manifest = load(retrieval / "run.json"), load(baseline / "run.json")
    lock = load(args.dataset / "lock.json")
    assert retrieval_manifest["dataset_id"] == full_manifest["dataset_id"] == lock["dataset_id"]
    assert retrieval_manifest["split"] == full_manifest["split"]
    reference_path = args.dataset / "references.json"
    assert evidence(reference_path)["sha256"] == lock["files"]["references.json"]
    references = load(reference_path)["tasks"]
    score_path = retrieval / args.score
    score = load(score_path)
    actual = {t["id"]: t for t in score["tasks"]}
    sessions, per_task = [], []
    full_results = sorted(baseline.glob("full-*/result.json"))
    for path in full_results:
        full = load(path)
        task_ids = full["task_ids"]
        assert all(identity in actual for identity in task_ids)
        measured = sum(actual[identity]["tool_text_proxy_tokens"] for identity in task_ids)
        paired_complete = full["complete_reading"] and all(
            actual[identity]["complete_known_evidence"] for identity in task_ids
        )
        sessions.append(
            {
                "session_id": full["session_id"],
                "task_ids": task_ids,
                "snapshot_ids": full["snapshot_ids"],
                "retrieval_proxy_tokens": measured,
                "full_read_proxy_tokens_once": full["tool_text_proxy_tokens"],
                "difference_retrieval_minus_full": measured - full["tool_text_proxy_tokens"],
                "full_read_complete": full["complete_reading"],
                "all_known_task_evidence_complete": paired_complete,
                "failed_task_ids": [
                    identity
                    for identity in task_ids
                    if not actual[identity]["complete_known_evidence"]
                ],
                "full_result": evidence(path),
            }
        )
        for identity in task_ids:
            task = actual[identity]
            per_task.append(
                {
                    "task_id": identity,
                    "session_id": full["session_id"],
                    "retrieval_proxy_tokens": task["tool_text_proxy_tokens"],
                    "standalone_full_scope_proxy_tokens": full["tool_text_proxy_tokens"],
                    "standalone_difference": task["tool_text_proxy_tokens"]
                    - full["tool_text_proxy_tokens"],
                    "complete_known_evidence": task["complete_known_evidence"],
                    "paired_complete": full["complete_reading"] and task["complete_known_evidence"],
                    "additive": False,
                }
            )
    assert {p["task_id"] for p in per_task} == set(actual)
    assert len(per_task) == len(actual)

    def totals(selected):
        short = sum(s["retrieval_proxy_tokens"] for s in selected)
        whole = sum(s["full_read_proxy_tokens_once"] for s in selected)
        return {
            "sessions": len(selected),
            "retrieval_proxy_tokens": short,
            "full_read_proxy_tokens_once": whole,
            "difference_retrieval_minus_full": short - whole,
            "reduction_fraction": 1 - short / whole if whole else None,
        }

    long_ids = [
        r["id"] for r in references if r["id"] in actual and "token_comparison" in r["tags"]
    ]
    assert len(long_ids) >= 3, "The long-material subset must have been registered before retrieval"
    long_sessions = [s for s in sessions if set(s["task_ids"]) & set(long_ids)]
    long_retrieval = sum(actual[identity]["tool_text_proxy_tokens"] for identity in long_ids)
    long_full = sum(s["full_read_proxy_tokens_once"] for s in long_sessions)
    long_complete = all(
        actual[identity]["complete_known_evidence"] for identity in long_ids
    ) and all(s["full_read_complete"] for s in long_sessions)
    atomic_json(
        target,
        {
            "classification": "actual_tool_text_proxy_comparison",
            "at": utc_now(),
            "dataset_id": lock["dataset_id"],
            "retrieval_run": args.retrieval_run,
            "full_read_run": args.full_run,
            "score": evidence(score_path),
            "reference_declaration": evidence(reference_path),
            "measurement": "Full serialized UTF-8 tool responses, including fields, citations, waits, errors, retention and release; independently checked BGE proxy counter.",
            "session_rule": "Identical fixed source sets share one full reading. All actually executed retrieval sequences are counted, including repeated work. Per-task standalone differences are non-additive.",
            "all_task_sessions": totals(sessions),
            "fully_successful_sessions": totals(
                [s for s in sessions if s["all_known_task_evidence_complete"]]
            ),
            "tasks": len(actual),
            "failed_tasks": [
                identity for identity, t in actual.items() if not t["complete_known_evidence"]
            ],
            "long_material_goal": {
                "predeclared_task_ids": long_ids,
                "source_sessions": [s["session_id"] for s in long_sessions],
                "retrieval_proxy_tokens": long_retrieval,
                "full_read_proxy_tokens_once": long_full,
                "all_evidence_complete": long_complete,
                "reduced": long_retrieval < long_full,
                "state": "passed" if long_complete and long_retrieval < long_full else "failed",
            },
            "sessions": sessions,
            "per_task_standalone_comparisons": per_task,
            "limitations": [
                "Complete card-generation was not executed. A run may record active GPT tool decisions, but the host model's billed input and output were not measured by this tool-response comparison.",
                "Prompt/tool definitions, host wrapping, repeated conversation billing, caching, and image tokens are unknown and are not estimated by these totals.",
                "Only purpose-qualified source ranges enter either side; original PDF page count is not substituted for actual readable text.",
                "Known evidence completeness uses the saved mechanical anchor/context score; final candidate relevance and machine acceptance require the separate GPT review.",
            ],
        },
    )
    print(
        json.dumps(
            {
                "target": str(target),
                "all_task_sessions": totals(sessions),
                "long_complete": long_complete,
                "long_retrieval": long_retrieval,
                "long_full_once": long_full,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
