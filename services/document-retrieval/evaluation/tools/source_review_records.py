"""Record explicit active-GPT decisions; this utility does not evaluate candidates."""

import hashlib
import json
from pathlib import Path

from document_retrieval.records import atomic_json, utc_now

MODULE = Path(__file__).resolve().parents[2]


def record(
    family,
    *,
    images,
    accepted=None,
    rejected=(),
    observations,
    ranges=None,
    fields=None,
    qualification_required=True,
    confidence="high",
    version="v1",
):
    folder = MODULE / "state/real-corpus" / family
    candidates = folder / f"qualification-candidates-{version}.json"
    rows = json.loads(candidates.read_text("utf-8"))["candidates"]
    ids = {row["id"] for row in rows}
    accepted = list(ids - set(rejected)) if accepted is None else accepted
    assert set(accepted) <= ids and set(rejected) <= ids
    evidence = []
    for page, name in images:
        path = MODULE / "evaluation/development/original-pages" / name
        evidence.append(
            {
                "physical_page": page,
                "path": str(path.resolve()),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    archive = json.loads((folder / "original-review-source-archive.json").read_text("utf-8"))
    original_sha = archive["source_sha256"]
    value = {
        "classification": "machine_review",
        "reviewer": "active Codex GPT vision model",
        "original_first": True,
        "human_review": False,
        "sealed_gold": False,
        "historical_truth_approved": False,
        "confidence": confidence,
        "original_evidence": evidence,
        "original_source_sha256": original_sha,
        "candidates_sha256": hashlib.sha256(candidates.read_bytes()).hexdigest(),
        "accepted_ids": sorted(accepted),
        "rejected_ids": sorted(rejected),
        "unreviewed_ids": sorted(ids - set(accepted) - set(rejected)),
        "source_ranges_by_id": ranges or {},
        "fields_by_id": fields or {},
        "observations": observations,
        "qualification_required": qualification_required,
        "scope": "Only the listed original-supported ranges and fields; extraction formatting warnings remain. No user-human, sealed-gold or historical-truth approval.",
        "reviewed_at": utc_now(),
    }
    target = folder / f"gpt-source-review-{version}.json"
    assert not target.exists(), target
    atomic_json(target, value)
    return value
