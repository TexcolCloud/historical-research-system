"""Apply an already recorded original-first GPT verdict to exact public source ranges."""

import argparse
import hashlib
import json
import time
from pathlib import Path

import httpx

from document_retrieval.records import atomic_json, text_hash

MODULE = Path(__file__).resolve().parents[2]


def apply_one(client, family, version="v1"):
    folder = MODULE / "state/real-corpus" / family
    verdict_path = folder / f"gpt-source-review-{version}.json"
    verdict = json.loads(verdict_path.read_text("utf-8"))
    verdict_hash = hashlib.sha256(verdict_path.read_bytes()).hexdigest()
    candidates_path = folder / f"qualification-candidates-{version}.json"
    assert hashlib.sha256(candidates_path.read_bytes()).hexdigest() == verdict["candidates_sha256"]
    candidates = json.loads(candidates_path.read_text("utf-8"))["candidates"]
    # One observed source block may contain several disjoint verified ranges.
    selections = []
    for candidate in candidates:
        if candidate["id"] not in verdict["accepted_ids"]:
            continue
        ranges = verdict.get("source_ranges_by_id", {}).get(candidate["id"])
        ranges = ranges if isinstance(ranges, list) else [ranges]
        for selected in ranges:
            selections.append((candidate, selected))
    output = folder / f"source-qualification-{version}"
    output.mkdir(exist_ok=True)
    if (output / "result.json").exists():
        return json.loads((output / "result.json").read_text("utf-8"))
    request_path = output / "request.json"
    if not request_path.exists():
        operations, contexts, gaps = [], [], []
        for index, (candidate, selection) in enumerate(selections):
            anchor = dict(candidate["source"])
            if selection:
                assert anchor["start"] <= selection["start"] < selection["end"] <= anchor["end"]
                value = candidate["text"][
                    selection["start"] - anchor["start"] : selection["end"] - anchor["start"]
                ]
                anchor.update(
                    start=selection["start"],
                    end=selection["end"],
                    expected_text_sha256=text_hash(value),
                )
            response = client.post("source-qualification-context", json=anchor)
            response.raise_for_status()
            context = response.json()
            contexts.append(context)
            if not context["reviewable"]:
                gaps.append(
                    {"candidate_id": candidate["id"], "reason": "public_context_not_reviewable"}
                )
                continue
            operations.append(
                {
                    "op": "qualify_source",
                    "op_id": f"qualify-{index}",
                    "client_ref": f"q-{index}",
                    "source": anchor,
                    "context_sha256": context["context_sha256"],
                    "purposes": ["index_input", "source_reading", "card_input"],
                    "verified_fields": verdict.get("fields_by_id", {}).get(
                        candidate["id"], ["text"]
                    ),
                    "review": {
                        "reviewer": verdict["reviewer"],
                        "original_first": True,
                        "verdict": "verified",
                        "confidence": verdict["confidence"],
                        "observations": verdict["observations"],
                        "original_assets": context["original_assets"],
                        "evidence": [
                            {
                                "classification": "machine_review",
                                "record": str(verdict_path),
                                "sha256": verdict_hash,
                                "candidate_id": candidate["id"],
                                "observed_original_evidence": verdict["original_evidence"],
                            }
                        ],
                        "human_review": False,
                        "sealed_gold": False,
                        "historical_truth_approved": False,
                    },
                }
            )
        assert operations, family
        atomic_json(output / "contexts.json", contexts)
        atomic_json(output / "unreviewable-gaps.json", gaps)
        body = {
            "kind": "catalogue",
            "scope": {"target_type": "catalogue"},
            "base_snapshot_ids": sorted({row["source"]["snapshot_id"] for row in operations}),
            "reason": "GPT original-first machine qualification of the listed exact evidence ranges and fields for retrieval evaluation. Original readiness remains unchanged; no user-human or historical-truth approval.",
            "operations": operations,
        }
        atomic_json(request_path, body)
    body = json.loads(request_path.read_text("utf-8"))

    def post(path, request, key):
        response = client.post(
            path,
            json=request,
            headers={"Idempotency-Key": family + ":source-qualification-" + version + ":" + key},
        )
        atomic_json(
            output / (key + ".json"), {"status": response.status_code, "body": response.json()}
        )
        response.raise_for_status()
        return response.json()

    draft = post("drafts", body, "draft")
    preview = post(
        f"drafts/{draft['draft_id']}/previews",
        {"draft_revision_id": draft["current_revision_id"]},
        "preview",
    )
    assert all(group["state"] == "ready" for group in preview["groups"]), [
        {"state": group["state"], "issues": group["issues"]} for group in preview["groups"]
    ]
    application = post(
        f"drafts/{draft['draft_id']}/applications",
        {
            "draft_revision_id": draft["current_revision_id"],
            "preview_id": preview["preview_id"],
            "preview_fingerprint": preview["fingerprint"],
            "selected_group_ids": [group["group_id"] for group in preview["groups"]],
            "reason": body["reason"],
        },
        "application",
    )
    deadline = time.monotonic() + 600
    while True:
        response = client.get(f"jobs/{application['job']['job_id']}")
        response.raise_for_status()
        job = response.json()
        if job["execution_state"] in ("completed", "failed", "cancelled"):
            break
        assert time.monotonic() < deadline, job
        time.sleep(0.25)
    atomic_json(output / "job.json", job)
    assert job["execution_state"] == "completed" and job["result"]["outcome"] == "succeeded", job
    result = {
        "family": family,
        "classification": "machine_qualified_exact_source_ranges",
        "review_sha256": verdict_hash,
        "qualified_range_count": len(body["operations"]),
        "application": job["result"],
    }
    atomic_json(output / "result.json", result)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ids", nargs="+", required=True)
    parser.add_argument("--version", default="v1")
    args = parser.parse_args()
    with httpx.Client(base_url="http://127.0.0.1:18125/api/v1/", timeout=180) as client:
        for family in args.ids:
            result = apply_one(client, family, args.version)
            print(
                json.dumps(
                    {"family": family, "qualified_range_count": result["qualified_range_count"]}
                ),
                flush=True,
            )


if __name__ == "__main__":
    main()
