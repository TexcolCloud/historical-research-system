"""Persist an already completed GPT original/public-input review; sends no tasks."""

import argparse
from pathlib import Path

from research_cards.records import atomic_json, now, read_json, sha256

MODULE = Path(__file__).resolve().parents[2]
DEVELOPMENT = MODULE / "evaluation/development"
parser = argparse.ArgumentParser()
parser.add_argument("--sample", required=True)
parser.add_argument("--family", required=True)
parser.add_argument("--snapshot", required=True)
parser.add_argument("--reference", type=Path)
parser.add_argument("--assessment", type=Path)
parser.add_argument("--input", type=Path, required=True)
args = parser.parse_args()
folder = DEVELOPMENT / "originals" / args.family
reference_path = args.reference or folder / "original-reference.json"
assessment_path = args.assessment or folder / "source-input-assessment.json"
reference, assessment = read_json(reference_path), read_json(assessment_path)
assert reference["original_first"] and not reference["candidate_seen"]
assert assessment["status"] == "admitted_complete_main_scope" and not assessment["candidate_seen"]
assert assessment["public_input_snapshot_id"] == args.snapshot
source = read_json(args.input)
assert source["summary"]["snapshot_id"] == args.snapshot
original = read_json(folder / "original-manifest.json")
assessment.update({"at": now(), "source_sha256": original["source_sha256"],
    "reference_sha256": sha256(reference_path.read_bytes()), "public_input_sha256": sha256(args.input.read_bytes()),
    "original_manifest_sha256": sha256((folder / "original-manifest.json").read_bytes())})
atomic_json(assessment_path, assessment)
plan_path = DEVELOPMENT / "calibration-v1/samples.json"
plan = read_json(plan_path)
row = next(row for row in plan["samples"] if row["sample_id"] == args.sample)
assert row["status"] == "pending_original_and_public_input_review", "Admission already recorded; preserve it."
row.update({"family": args.family, "snapshot_id": args.snapshot, "status": "admitted_before_generation",
    "admitted_at": now(), "source_sha256": original["source_sha256"],
    "main_research_pages": len(assessment["main_physical_pages"]),
    "source_reference_path": str(reference_path.resolve()), "source_reference_sha256": sha256(reference_path.read_bytes()),
    "fresh_input_path": str(args.input.resolve()), "fresh_input_sha256": sha256(args.input.read_bytes()),
    "input_assessment_path": str(assessment_path.resolve()), "input_assessment_sha256": sha256(assessment_path.read_bytes()),
    "source_coverage": "complete_main_article_and_attached_notes", "research_card_quality_pass": False,
    "prior_exposure": "prior extraction/retrieval development; no previous card generation for this family",
    "machine_review": True, "human_source_review": False, "sealed_gold": False})
atomic_json(plan_path, plan)
atomic_json(plan_path.parent / (args.sample + "-request.json"), {"kind": "generate_card",
    "primary_snapshot_id": args.snapshot, "regenerate": True, "trigger": "calibration_v1", "workload": "interactive"})
print({"sample_id": args.sample, "status": row["status"], "task_submitted": False})
