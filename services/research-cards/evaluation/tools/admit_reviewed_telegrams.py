"""Persist completed original/public-text comparison and submit two tiny complete telegrams."""

import subprocess
import sys
from pathlib import Path

from research_cards.client import Client
from research_cards.records import atomic_json, now, read_json

MODULE = Path(__file__).resolve().parents[2]
DEVELOPMENT = MODULE / "evaluation/development"
FOLDER = DEVELOPMENT / "originals/primary-new-fourth-army"
for sample, snapshot, pages in [("C07", "8e4f2988-bc70-47bd-8b15-c2ca29fe5bf5", [7]),
    ("C08", "69163ec1-26b7-4832-b55c-3b5364c3bc47", [8, 9])]:
    source = DEVELOPMENT / "calibration-source-inputs-v3" / (snapshot + ".json")
    observed = read_json(source)
    assert observed["summary"]["excluded_nonwhitespace_ranges"] == 0
    assessment = FOLDER / (sample + "-source-input-assessment.json")
    atomic_json(assessment, {"at": now(), "status": "admitted_complete_main_scope", "candidate_seen": False,
        "public_input_snapshot_id": snapshot, "main_physical_pages": pages,
        "reviewer": "active_codex_gpt_vision", "original_first": True,
        "machine_review": True, "human_review": False, "sealed_gold": False,
        "observations": ["All public included text and attached notes were read after the complete original pages; all excluded ranges are whitespace.",
            "Heading markers and bracket/spacing normalization are extraction presentation. C08 has a continuation across physical pages 8–9; both are present.",
            "The unusual wording 在行营他完全同意 is visible in the reproduced original; preserve it as source text without silently rewriting."] if sample == "C08" else
            ["The original really is a short one-page telegram; all 80 included codepoints cover title/date/addressee/body/signature/date code. Its brevity is not a missing-page gap.",
             "All five excluded ranges (10 codepoints) are whitespace; no missing substantive text found."]})
    subprocess.run([sys.executable, str(MODULE / "evaluation/tools/record_calibration_admission.py"),
        "--sample", sample, "--family", "primary-new-fourth-army", "--snapshot", snapshot,
        "--reference", str(FOLDER / (sample + "-original-reference.json")), "--assessment", str(assessment),
        "--input", str(source)], check=True)
    with Client("http://127.0.0.1:18142") as client:
        request = read_json(DEVELOPMENT / "calibration-v1" / (sample + "-request.json"))
        result = client.with_receipt(DEVELOPMENT / "calibration-v1" / (sample + "-receipt.json"), "POST", "tasks", request)
        atomic_json(DEVELOPMENT / "calibration-v1" / (sample + "-submission.json"), result)
        print({"sample": sample, "task_id": result["task_id"]})
