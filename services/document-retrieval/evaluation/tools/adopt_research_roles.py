"""Apply reviewed occurrence roles through the public ingestion draft workflow."""

import argparse
import copy
import hashlib
import json
import time
from pathlib import Path

import httpx

from document_retrieval.records import atomic_json, utc_now

MODULE = Path(__file__).resolve().parents[2]
REVIEWED = {
    "family-04": {
        "occurrence_id": "83c80f35-380e-5599-bc7c-40f192cb9e75",
        "observation": "Original pages 1, 2 and 6 identify one Chinese article on Song Qingling and the China Defense League. Page 13 contains unrelated journal abstracts and is outside this main occurrence. The original title and article body support a research_item role; this does not approve every extracted character or restricted field.",
        "pages": [1, 2, 6, 13],
    },
    "family-05": {
        "occurrence_id": "1e52344f-11e5-59a2-b349-4fdb0e317313",
        "observation": "Original pages 1, 2, 6 and 12 show the start, argument, factory example and end of the same uniform-supply article. The existing occurrence follows its 12 physical pages. Its scope supports a research_item role; text eligibility continues to follow upstream field decisions.",
        "pages": [1, 2, 6, 12],
    },
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def apply_one(client, family):
    review = REVIEWED[family]
    folder = MODULE / "state/real-corpus" / family
    output = folder / "research-role-v1"
    output.mkdir(exist_ok=True)
    if (output / "result.json").exists():
        return json.loads((output / "result.json").read_text("utf-8"))
    receipt = json.loads((folder / "input.json").read_text("utf-8"))
    manifest = json.loads((folder / "machine-draft-manifest.json").read_text("utf-8"))
    created = {
        effect["op_id"]: effect["created_id"]
        for group in receipt["application"]["groups"]
        for effect in group["receipt"]["effects"]
        if effect.get("created_id")
    }
    refs = {
        operation["client_ref"]: created[operation["op_id"]]
        for operation in manifest["body"]["operations"]
        if operation.get("client_ref") and operation["op_id"] in created
    }
    reading = next(
        operation
        for operation in manifest["body"]["operations"]
        if operation["op"] == "set_reading"
        and refs[operation["target"]["target"]["client_ref"]] == review["occurrence_id"]
    )
    current = client.get(
        "current-input", params={"target_type": "occurrence", "target_id": review["occurrence_id"]}
    )
    current.raise_for_status()
    snapshot = current.json()
    observed = MODULE / "evaluation/development/original-pages"
    verdict = {
        "classification": "machine_review",
        "reviewer": "active Codex GPT vision model",
        "original_first": True,
        "human_review": False,
        "sealed_gold": False,
        "state": "machine_approved_research_role_only",
        "confidence": "high",
        "original_source_sha256": receipt["source_sha256"],
        "observation": review["observation"],
        "original_evidence": [
            {
                "path": str(observed / f"{family}-{page:04d}.png"),
                "sha256": digest(observed / f"{family}-{page:04d}.png"),
            }
            for page in review["pages"]
        ],
        "candidate_manifest_sha256": digest(folder / "machine-draft-manifest.json"),
        "fixed_snapshot": snapshot,
        "reviewed_at": utc_now(),
    }
    review_path = output / "gpt-role-review.json"
    review_hash = digest(review_path) if review_path.exists() else atomic_json(review_path, verdict)
    evidence = [
        {
            "kind": "gpt_original_first_machine_review",
            "sha256": review_hash,
            "record": str(output / "gpt-role-review.json"),
            "human_review": False,
        }
    ]
    segments = copy.deepcopy(reading["segments"])
    for segment in segments:
        if segment.get("source_edition", {}).get("client_ref"):
            segment["source_edition"] = {"id": refs[segment["source_edition"]["client_ref"]]}
    body = {
        "kind": "organization",
        "scope": {"target_type": "catalogue"},
        "base_snapshot_ids": [snapshot["snapshot_id"], receipt["carrier_snapshot_id"]],
        "reason": "GPT original-first machine adoption of a research role for module evaluation; source eligibility is preserved; no user-human or sealed gold approval.",
        "operations": [
            {
                "op": "create_organization",
                "op_id": "organization",
                "client_ref": "org",
                "display_name": "检索模块验收 " + family,
            },
            {
                "op": "create_node",
                "op_id": "node",
                "client_ref": "item",
                "depends_on": ["organization"],
                "organization": {"client_ref": "org"},
                "position": 0,
                "kind": "item",
                "label": Path(receipt["source"]).stem,
                "label_origin": "provisional",
                "role": "research_item",
                "occurrence": {"id": review["occurrence_id"]},
                "evidence": evidence,
            },
            {
                "op": "set_reading",
                "op_id": "reading",
                "depends_on": ["node"],
                "target": {"target_type": "occurrence", "target": {"id": review["occurrence_id"]}},
                "organization": {"client_ref": "org"},
                "segments": segments,
            },
        ],
    }
    request_path = output / "request.json"
    if request_path.exists():
        body = json.loads(request_path.read_text("utf-8"))
    else:
        atomic_json(request_path, body)

    def post(path, body, key):
        response = client.post(
            path, json=body, headers={"Idempotency-Key": family + "-role-v1-" + key}
        )
        atomic_json(
            output / (key + ".json"),
            {"http_status": response.status_code, "response": response.json()},
        )
        response.raise_for_status()
        return response.json()

    draft = post("drafts", body, "draft")
    preview = post(
        f"drafts/{draft['draft_id']}/previews",
        {"draft_revision_id": draft["current_revision_id"]},
        "preview",
    )
    assert all(group["state"] == "ready" for group in preview["groups"]), preview
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
    while True:
        response = client.get(f"jobs/{application['job']['job_id']}")
        response.raise_for_status()
        job = response.json()
        if job["execution_state"] in ("completed", "failed", "cancelled"):
            break
        time.sleep(0.25)
    atomic_json(output / "job.json", job)
    assert job["execution_state"] == "completed" and job["result"]["outcome"] == "succeeded", job
    response = client.get(
        "current-input", params={"target_type": "occurrence", "target_id": review["occurrence_id"]}
    )
    response.raise_for_status()
    result = {
        "family": family,
        "snapshot": response.json(),
        "role_review_sha256": review_hash,
        "application": job["result"],
        "classification": "machine_adopted_role",
    }
    atomic_json(output / "result.json", result)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ids", nargs="+", choices=list(REVIEWED), default=list(REVIEWED))
    args = parser.parse_args()
    with httpx.Client(base_url="http://127.0.0.1:18125/api/v1/", timeout=120) as client:
        for family in args.ids:
            result = apply_one(client, family)
            print(
                json.dumps({"family": family, "snapshot_id": result["snapshot"]["snapshot_id"]}),
                flush=True,
            )


if __name__ == "__main__":
    main()
