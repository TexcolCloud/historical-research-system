"""Apply only explicit GPT-reviewed exact ranges through ingestion's public draft API."""

import argparse
import time
from pathlib import Path

import httpx

from research_cards.records import atomic_json, now, read_json, sha256


def main(args):
    review_path = args.review.resolve()
    review = read_json(review_path)
    assert review["original_first"] and not review["human_review"] and not review["sealed_gold"]
    public_path = (review_path.parent / review["public_input"]).resolve()
    public = read_json(public_path)
    snapshot = public["summary"]["snapshot_id"]
    original_path = review_path.parent / review["original_manifest"]
    original = read_json(original_path)
    reference_path = review_path.parent / review["original_reference"]
    output = (review_path.parent / review.get("qualification_output", "source-qualification")).resolve()
    assert output.is_relative_to(review_path.parent), "Qualification output stays beside its review."
    output.mkdir(parents=True, exist_ok=True)
    if (output / "result.json").exists():
        print(read_json(output / "result.json"))
        return
    evidence = {"review_path": str(review_path), "review_sha256": sha256(review_path.read_bytes()),
        "original_manifest_sha256": sha256(original_path.read_bytes()), "source_sha256": original["source_sha256"],
        "reference_sha256": sha256(reference_path.read_bytes()), "public_input_sha256": sha256(public_path.read_bytes())}
    operations, contexts = [], []
    with httpx.Client(base_url="http://127.0.0.1:18125/api/v1/", timeout=180) as client:
        for index, approved in enumerate(review["accepted_ranges"]):
            anchor = {"snapshot_id": snapshot,
                **{key: approved[key] for key in ("source_span_ref", "start", "end", "expected_text_sha256")}}
            candidates = [(row["reference"], row["archive_fragment"]) for row in public["excluded_layout_inspection"]]
            candidates.extend((row["source"], row["source"]["text"])
                for batch in public["research_pages"] for row in batch["items"] if row["kind"] == "included")
            source, text = next((source, text) for source, text in candidates
                if source["source_span_ref"] == anchor["source_span_ref"]
                and source["start"] <= anchor["start"] < anchor["end"] <= source["end"])
            text = text[anchor["start"] - source["start"]:anchor["end"] - source["start"]]
            assert sha256(text.encode()) == anchor["expected_text_sha256"]
            image = next(row for row in original["images"] if row["physical_page"] == approved["physical_page"])
            assert sha256(Path(image["path"]).read_bytes()) == image["sha256"]
            response = client.post("source-qualification-context", json=anchor)
            atomic_json(output / f"context-{index}.json", {"status": response.status_code, "body": response.json()})
            response.raise_for_status()
            context = response.json()
            contexts.append(context)
            assert context["reviewable"], "Source context must be reviewable before application."
            assert context["physical_page"] == approved["physical_page"]
            assert original["source_sha256"] in {asset["sha256"] for asset in context["original_assets"]}
            operations.append({"op": "qualify_source", "op_id": f"qualify-{index}", "client_ref": f"q-{index}",
                "source": anchor, "context_sha256": context["context_sha256"],
                "purposes": ["index_input", "source_reading", "card_input"], "verified_fields": approved["verified_fields"],
                "review": {"reviewer": review["reviewer"], "original_first": True, "verdict": "verified",
                    "confidence": review["confidence"], "observations": [review["observations"]],
                    "original_assets": context["original_assets"],
                    "evidence": [{"classification": "machine_review", **evidence, "original_page": image}],
                    "human_review": False, "sealed_gold": False, "historical_truth_approved": False}})
        atomic_json(output / "contexts.json", contexts)
        body = {"kind": "catalogue", "scope": {"target_type": "catalogue"}, "base_snapshot_ids": [snapshot],
            "reason": "GPT original-first machine qualification of the explicitly reviewed text ranges for whole-source research-card calibration; extraction output and original readiness are preserved.",
            "operations": operations}
        atomic_json(output / "request.json", body)

        def post(path, value, name):
            response = client.post(path, json=value, headers={"Idempotency-Key": args.key + ":" + name})
            atomic_json(output / (name + ".json"), {"status": response.status_code, "body": response.json()})
            response.raise_for_status()
            return response.json()

        draft = post("drafts", body, "draft")
        preview = post(f"drafts/{draft['draft_id']}/previews", {"draft_revision_id": draft["current_revision_id"]}, "preview")
        assert all(row["state"] == "ready" for row in preview["groups"]), "Inspect saved preview before any application."
        application = post(f"drafts/{draft['draft_id']}/applications", {
            "draft_revision_id": draft["current_revision_id"], "preview_id": preview["preview_id"],
            "preview_fingerprint": preview["fingerprint"], "selected_group_ids": [row["group_id"] for row in preview["groups"]],
            "reason": body["reason"]}, "application")
        deadline = time.monotonic() + 180
        while True:
            response = client.get(f"jobs/{application['job']['job_id']}")
            response.raise_for_status()
            job = response.json()
            if job["execution_state"] in ("completed", "failed", "cancelled"):
                break
            if time.monotonic() >= deadline:
                raise SystemExit("Application remains pending; preserve and inspect its receipt.")
            time.sleep(0.5)
        atomic_json(output / "job.json", job)
        assert job["execution_state"] == "completed" and job["result"]["outcome"] == "succeeded"
        result = {"at": now(), "snapshot_id": snapshot, "qualified_range_count": len(operations),
            "classification": "machine_qualified_exact_source_ranges", "evidence": evidence, "application": job["result"]}
        atomic_json(output / "result.json", result)
        print({"qualified_range_count": len(operations), "snapshot_id": snapshot, "human_review": False})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--key", required=True)
    main(parser.parse_args())
