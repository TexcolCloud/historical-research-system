"""Persist the active GPT's source-identity addendum for already reviewed ranges.

This is an audit writer, not a judge. The original page, source archive and prior
original-first verdict were inspected before the decision recorded here.
"""

import hashlib
import json
import shutil
from pathlib import Path

from document_retrieval.records import atomic_json, utc_now

MODULE = Path(__file__).resolve().parents[2]


def main():
    corpus = json.loads(
        (MODULE / "evaluation/datasets/retrieval-representative-20260908-v3/corpus.json").read_text(
            "utf-8"
        )
    )
    for family in corpus:
        folder = Path(family["fixed_inputs_path"]).parent
        prior_path = Path(family["source_review_path"])
        prior = json.loads(prior_path.read_text("utf-8"))
        version = "v2" if family["family"] == "family-16" else "v1"
        candidate_path = folder / ("qualification-candidates-" + version + ".json")
        target = folder / "gpt-source-review-v3.json"
        if target.exists():
            continue
        assert hashlib.sha256(candidate_path.read_bytes()).hexdigest() == prior["candidates_sha256"]
        for image in prior["original_evidence"]:
            assert hashlib.sha256(Path(image["path"]).read_bytes()).hexdigest() == image["sha256"]
        shutil.copyfile(candidate_path, folder / "qualification-candidates-v3.json")
        record = {
            **prior,
            "qualification_required": True,
            "fields_by_id": {key: ["provenance"] for key in prior["accepted_ids"]},
            "reviewed_at": utc_now(),
            "review_kind": "original_source_identity_and_page_mapping_addendum",
            "previous_review": {
                "path": str(prior_path),
                "sha256": hashlib.sha256(prior_path.read_bytes()).hexdigest(),
            },
            "observations": [
                "The active Codex GPT has already inspected the original PDF pages and the corresponding raw fixed source ranges in the linked original-first review. The source PDF hash, original-page images, carrier source archive, exact Unicode offsets and adopted source composition identify these ranges without substituting another document or edition.",
                "Approve the provenance field only for those exact accepted ranges, preserving every previously narrowed boundary and every rejection. This establishes the source PDF/page/range identity required for an attributable quotation. It does not approve an inferred bibliography, date, author, title, article hierarchy, image interpretation or historical truth.",
                "The prior text or image qualifications remain separate. Adding provenance does not qualify previously excluded text or the whole document. Unreviewed fine print in the newspaper and all incomplete or uncertain ranges remain excluded.",
                "This addendum resolves the source-provenance preflight omission observed in the first calibration attempt, retained as cal-bge-default-20260908-v1. No held-out retrieval result has been viewed or used to choose the approved ranges.",
            ],
        }
        atomic_json(target, record)
        print(family["family"], len(prior["accepted_ids"]), flush=True)


if __name__ == "__main__":
    main()
