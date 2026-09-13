"""Persist the active Codex GPT's observed development verdicts with evidence hashes."""

import hashlib
import json
from pathlib import Path

from document_retrieval.records import atomic_json, utc_now

base = Path(__file__).resolve().parents[1] / "development"


def hashed(path):
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"path": str(path.resolve()), "sha256": digest}


originals = [
    entry
    for name in ("primary-originals-rendered.json", "council-originals-rendered.json")
    for entry in json.loads((base / name).read_text(encoding="utf-8"))
]
review = {
    "schema_version": 1,
    "reviewed_at": utc_now(),
    "reviewer": "active Codex GPT vision model",
    "classification": "machine_review",
    "sequence": "Original PDF page renders were viewed before extraction or retrieval candidates for these sources.",
    "user_human_review": False,
    "sealed_gold": False,
    "module_acceptance": "not_yet_complete",
    "observed_originals": [
        {
            "id": entry["id"],
            "source_sha256": entry["source_sha256"],
            "pages": entry["rendered_pages"],
            "contact_sheet": entry["contact_sheet"],
            "contact_sha256": entry["contact_sha256"],
        }
        for entry in originals
    ],
    "findings": [
        {
            "id": "northwest-1941",
            "verdict": "readable_catalogue_unreliable_faded_body_not_selected",
            "confidence": "high",
            "evidence": "primary-northwest-1941 pages 1, 10, 20, 40",
            "detail": "The editorial note identifies reprints of 1941–1945 Northwest Bureau documents and discloses editorial interventions. Pages 20 and 40 contain visibly faded or missing characters; their body text and figures are not accepted as reference evidence here.",
        },
        {
            "id": "new-fourth-report",
            "verdict": "machine_approved_for_reference_preparation",
            "confidence": "high",
            "evidence": "primary-new-fourth-army page 60; new-fourth-report pages 61–64",
            "detail": "The printed report is dated 17 September 1937 and describes three months of peace-negotiation work. Body text, cross-page continuation and numbered explanatory notes are legible. This is a modern simplified-character reprint of a historical document, not a contemporaneous printing claim. Pages 65–68 still require original review before candidate assessment.",
        },
        {
            "id": "council-newspapers",
            "verdict": "machine_approved_for_reference_preparation_with_local_uncertainty",
            "confidence": "medium",
            "evidence": "primary-political-council pages 9–14 through the separate front-b/front-c renders",
            "detail": "Pages 9, 10, 12 and 13 reproduce vertical traditional-character newspaper reports; large headlines and accompanying dated captions are readable. Fine newspaper body text is locally uncertain and cannot be assumed correct. Pages 11 and 14 provide original-image and caption evidence. Modern captions and reproduced newspaper wording remain distinct evidence layers.",
        },
        {
            "id": "council-frontmatter",
            "verdict": "context_only_not_near_modern_primary_text",
            "confidence": "high",
            "evidence": "council-front-a pages 2–5; council-front-b pages 6–8",
            "detail": "Publication matter and later handwritten inscriptions are contextual pages. They are not substituted for the contemporaneous newspaper material required by the acceptance plan.",
        },
        {
            "id": "mapping",
            "verdict": "machine_approved_for_conservative_compatibility",
            "confidence": "high",
            "detail": "The active GPT read the explicit ordinary traditional/simplified pair inventory and implementation. Only approved equal-length changed pairs are accepted from TSPhrases/TSCharacters; unreviewed variants and unequal-length conversions remain original. ASCII case matching is separate. This is a restricted retrieval aid, not authority for historical name equivalence.",
        },
        {
            "id": "paired-restore",
            "verdict": "machine_approved_for_the_recorded_development_run",
            "confidence": "high",
            "detail": "The first recovery verification exposed a SQLite close/checkpoint hash problem. The replacement package passed verification and was actually restored while the service was stopped. The prior source anchor hashes match, later external lists are missing explicitly, and a fresh readiness epoch works. Subsequent recovery code changes require another final-source run.",
        },
        {
            "id": "engineering-only",
            "verdict": "machine_approved_for_control_evidence_only",
            "confidence": "high",
            "detail": "The observed synthetic input is three Unicode characters, 甲𠀀乙, with a one-pixel engineering source image. Its exact/compatible/hybrid readback and offset hashes prove only the engineering path, not historical retrieval quality.",
        },
    ],
    "evidence": [
        hashed(base / name)
        for name in (
            "actual-paired-restore.json",
            "offline-restore.json",
            "recovery-create-stable.json",
            "recovery-verify-stable.json",
            "engineering-three-modes.json",
            "source-candidate-inventory.json",
            "primary-excerpt-manifest.json",
            "unit-controls-text.xml",
        )
    ]
    + [hashed(base.parents[1] / "src/document_retrieval/normalization.py")],
}
atomic_json(base / "gpt-original-first-development-review.json", review)
print(
    json.dumps(
        {
            "classification": review["classification"],
            "original_review_groups": len(originals),
            "findings": len(review["findings"]),
            "module_acceptance": review["module_acceptance"],
        }
    )
)
