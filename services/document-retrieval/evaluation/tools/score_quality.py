"""Post-run known-reference checks; never used to select queries or candidates.

These are mechanical evidence checks pending active-GPT candidate review. A missed
known reference is not a judgment that an unreviewed candidate is irrelevant.
"""

import argparse
import hashlib
import json
from pathlib import Path

from document_retrieval.records import atomic_json, text_hash, utc_now
from document_retrieval.text import display_text

MODULE = Path(__file__).resolve().parents[2]


def same_source(a, b):
    return (a["snapshot_id"], a["source_span_ref"]) == (b["snapshot_id"], b["source_span_ref"])


def covered(anchor, actual):
    intervals = sorted(
        (max(unit["start"], anchor["start"]), min(unit["end"], anchor["end"]))
        for unit in actual
        if same_source(anchor, unit)
        and unit["end"] > anchor["start"]
        and unit["start"] < anchor["end"]
    )
    end = anchor["start"]
    for left, right in intervals:
        if left > end:
            return False
        end = max(end, right)
    return end >= anchor["end"]


def verified_recovered_errors(folder, actual):
    """Recognize only a proven result read of the original accepted search job."""
    resolved = []
    for entry in actual.get("recoveries", []):
        proof_path = folder / "existing-search-recovery.json"
        proof = json.loads(proof_path.read_text("utf-8"))
        assert hashlib.sha256(proof_path.read_bytes()).hexdigest() == entry["sha256"]
        assert all(entry[k] == v for k, v in proof.items())
        assert proof["classification"] == "existing_search_job_completed"
        assert proof["new_content_calls"] == 0 and proof["resolved_error_indices"] == [0]
        before_path = folder / "state-before-search-recovery.json"
        assert hashlib.sha256(before_path.read_bytes()).hexdigest() == proof["before_state_sha256"]
        before = json.loads(before_path.read_text("utf-8"))
        assert not before["searches"] and before["new_searches"] == 1
        expected = {
            "type": "Problem",
            "detail": "The retrieval service is unavailable; preserve and reuse the operation key.",
        }
        assert before["errors"] == [expected] and actual["errors"][0] == expected
        traces = [
            json.loads(p.read_text("utf-8")) for p in sorted((folder / "http").glob("*.json"))
        ]
        for row in traces:
            assert text_hash(row["response_text"]) == row["sha256"]
        initial = next(r for r in traces if r["sha256"] == proof["original_receipt_sha256"])
        recovered = next(r for r in traces if r["sha256"] == proof["recovered_response_sha256"])
        assert initial["method"] == "POST" and initial["url"].endswith("/searches")
        assert initial["status"] == 202 and recovered["status"] == 200
        assert recovered["method"] == "POST"
        assert recovered["url"].split("?")[0].endswith("/jobs/" + proof["job_id"] + "/result")
        receipt, response = (
            json.loads(initial["response_text"]),
            json.loads(recovered["response_text"]),
        )
        assert receipt["job_id"] == proof["job_id"]
        assert receipt["scope_ref"] == response["scope_ref"] == proof["scope_ref"]
        assert response["list_id"] == proof["list_id"]
        assert all(actual["searches"][0][k] == v for k, v in response.items())
        between = [r for r in traces if initial["sequence"] < r["sequence"] < recovered["sequence"]]
        assert between and all(
            r["method"] == "GET" and r["url"].endswith("/jobs/" + proof["job_id"]) for r in between
        )
        status = json.loads(between[-1]["response_text"])
        assert status["execution_state"] == "completed"
        assert status["scope_ref"] == proof["scope_ref"]
        assert status["result_ref"]["list_id"] == proof["list_id"]
        assert (
            json.loads(recovered["request_text"])["budget"]
            == json.loads(initial["request_text"])["budget"]
        )
        resolved.append({"index": 0, "error": expected, "proof_sha256": entry["sha256"]})
    assert len(resolved) <= 1
    return resolved


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset",
        type=Path,
        default=MODULE / "evaluation/datasets/retrieval-representative-20260908-v6",
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--version", default="v1")
    parser.add_argument("--recover-existing-jobs", action="store_true")
    args = parser.parse_args()
    folder = MODULE / "evaluation/runs" / args.run_id
    assert (folder / "completed.json").exists(), "Do not score an incomplete run"
    target = folder / ("mechanical-score-" + args.version + ".json")
    assert not target.exists(), "Append a new score version rather than replacing evidence"
    lock = json.loads((args.dataset / "lock.json").read_text("utf-8"))
    reference_path = args.dataset / "references.json"
    assert (
        hashlib.sha256(reference_path.read_bytes()).hexdigest() == lock["files"]["references.json"]
    )
    references = json.loads(reference_path.read_text("utf-8"))["tasks"]
    corpus = json.loads((args.dataset / "corpus.json").read_text("utf-8"))
    originals = {}
    for family in corpus:
        fixed = json.loads(Path(family["fixed_inputs_path"]).read_text("utf-8"))
        raw = json.loads(
            (
                Path(family["fixed_inputs_path"]).parent / "original-review-source-archive.json"
            ).read_text("utf-8")
        )["spans"]
        by_span = {s["source_span_ref"]: s for s in raw}
        for snapshot in fixed:
            identity = snapshot["snapshot"]["snapshot_id"]
            for s in snapshot["sources"]:
                originals[(identity, s["source_span_ref"])] = by_span[s["source_span_ref"]]
    results = []
    for path in sorted(folder.glob("*/result.json")):
        actual = json.loads(path.read_text("utf-8"))
        reference = next(r for r in references if r["id"] == actual["task_id"])
        returned = [
            u for reading in actual["reads"] for u in reading.get("units", []) if "text" in u
        ]
        failures = []
        for unit in returned:
            a, text = unit["source_anchor"], unit["text"]
            source = originals.get((a["snapshot_id"], a["source_span_ref"]))
            if not source:
                failures.append({"code": "unregistered_fixed_source", "anchor": a})
                continue
            expected = source["text"][a["start"] - source["start"] : a["end"] - source["start"]]
            if not (
                expected == text
                and len(text) == a["end"] - a["start"]
                and text_hash(text) == a["text_sha256"]
            ):
                failures.append({"code": "original_anchor_mismatch", "anchor": a})
            if not all(
                field["status"] in ("allowed", "limited")
                for field in unit["usage"]["fields"]
                if field["field"] == "text"
            ):
                failures.append({"code": "returned_unqualified_text", "anchor": a})
        anchors = [u["source_anchor"] for u in returned]
        groups = []
        for group in reference["groups"]:
            matches = [
                alternative
                for alternative in group["alternatives"]
                if covered(alternative["match"], anchors)
            ]
            complete = any(
                all(covered(context, anchors) for context in alternative["required_context"])
                for alternative in matches
            )
            groups.append(
                {
                    "group_id": group["group_id"],
                    "family": group["family"],
                    "need": group["need"],
                    "known_match_returned": bool(matches),
                    "complete_context_returned": complete,
                }
            )
        first = actual["searches"][0].get("items", []) if actual["searches"] else []
        known_first = []
        for item in first:
            shown = display_text(item["excerpt"]).text
            matches = [
                g["group_id"]
                for g in reference["groups"]
                if any(
                    alt["match"]["text"] in item["excerpt"]
                    or display_text(alt["match"]["text"]).text in shown
                    for alt in g["alternatives"]
                )
            ]
            known_first.append(
                {
                    "evidence_ref": item["evidence_ref"],
                    "excerpt": item["excerpt"],
                    "citation": item["citation"],
                    "known_groups_visible": matches,
                    "judgment": "known_reference_entry"
                    if matches
                    else "requires_GPT_review_not_judged_irrelevant",
                }
            )
        media_ok = not reference["requires_media"] or any(
            m["http_status"] == 200 and m["bytes"] > 0 for m in actual["media"]
        )
        recovered_errors = (
            verified_recovered_errors(path.parent, actual) if args.recover_existing_jobs else []
        )
        unresolved_errors = [
            error
            for i, error in enumerate(actual["errors"])
            if i not in {r["index"] for r in recovered_errors}
        ]
        success = (
            bool(groups)
            and all(g["complete_context_returned"] for g in groups)
            and not failures
            and not unresolved_errors
            and media_ok
        )
        row = {
            "id": reference["id"],
            "type": reference["type"],
            "families": reference["families"],
            "tags": reference["tags"],
            "first_batch_known_useful": any(i["known_groups_visible"] for i in known_first),
            "complete_known_evidence": success,
            "groups": groups,
            "first_batch": known_first,
            "anchor_failures": failures,
            "runtime_errors": actual["errors"],
            "verified_recovered_errors": recovered_errors,
            "unresolved_runtime_errors": unresolved_errors,
            "media_bytes_available": media_ok,
            "candidate_GPT_review": "pending",
            "returned_units": len(returned),
            "seconds": actual["seconds"],
            "content_calls": actual["content_calls"],
            "tool_text_proxy_tokens": actual["tool_text_proxy_tokens"],
            "result_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        results.append(row)
        print(
            json.dumps(
                {
                    "id": row["id"],
                    "first": row["first_batch_known_useful"],
                    "complete": success,
                    "groups": [sum(g["complete_context_returned"] for g in groups), len(groups)],
                    "missing": [g["need"] for g in groups if not g["complete_context_returned"]],
                    "errors": row["runtime_errors"],
                    "anchor_failures": len(failures),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    research = [r for r in results if r["type"] != "literal"]
    summary = {
        "literal": [
            sum(r["complete_known_evidence"] for r in results if r["type"] == "literal"),
            sum(r["type"] == "literal" for r in results),
        ],
        "research_first": [sum(r["first_batch_known_useful"] for r in research), len(research)],
        "research_complete": [sum(r["complete_known_evidence"] for r in research), len(research)],
        "known_groups": [
            sum(g["complete_context_returned"] for r in research for g in r["groups"]),
            sum(len(r["groups"]) for r in research),
        ],
        "by_type": {
            k: [
                sum(r["complete_known_evidence"] for r in results if r["type"] == k),
                sum(r["type"] == k for r in results),
            ]
            for k in ("within_source", "supplemental")
        },
        "anchor_failures": sum(len(r["anchor_failures"]) for r in results),
        "tool_text_proxy_tokens": sum(r["tool_text_proxy_tokens"] for r in results),
    }
    atomic_json(
        target,
        {
            "classification": "mechanical_reference_comparison_pending_GPT_review",
            "run_id": args.run_id,
            "dataset_id": lock["dataset_id"],
            "scored_at": utc_now(),
            "references_sha256": lock["files"]["references.json"],
            "scorer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "error_rule": "hash_verified_original_search_job_recovery_v1"
            if args.recover_existing_jobs
            else "any_recorded_error_is_fatal",
            "summary": summary,
            "tasks": results,
            "human_review": False,
            "sealed_gold": False,
            "scope": "Known original evidence groups only; unknown candidates are not labelled irrelevant.",
        },
    )
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
