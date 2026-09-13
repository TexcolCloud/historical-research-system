"""Persist the active GPT's original-first development verdict and its exact evidence."""

from pathlib import Path

from research_cards.records import atomic_json, now, read_json, sha256

root = Path(__file__).resolve().parents[4]
output = root / "services/research-cards/evaluation/development/first-card-v2"
candidate_path = output / "candidate-review-input.json"
candidate = read_json(candidate_path)
reference = root / "services/research-cards/evaluation/development/family-15-original-reference-v1.json"
original = read_json(reference)
paths = [candidate_path, reference, Path(original["image_path"]), Path(original["source"])]
verdict = {
    "kind": "original_first_candidate_development_review", "at": now(),
    "reviewer": "active_codex_gpt_vision", "machine_review": True,
    "human_source_review": False, "sealed_gold": False,
    "original_first": True, "original_reference_created_before_generation": original["at"],
    "original_page_reinspected_before_candidate": True,
    "candidate_revision_id": candidate["id"], "formal_acceptance_sample": False,
    "verdict": "candidate_requires_correction_before_complete_quality_acceptance",
    "reviewed_scope": "Complete original physical page 1; all candidate evidence, sections and arguments; exact persisted content retained.",
    "strengths": [
        "The candidate preserves the four functional sections and attributes causal claims to the 2012 author.",
        "The article's wartime event period remains distinct from its modern publication date.",
        "The left-column ending is joined to the right-column continuation without losing the concluding clause.",
        "The card does not treat the Epstein quotation as independent confirmation from a separately read original."
    ],
    "findings": [
        {"severity": "material", "code": "original_metadata_not_yet_integrated",
         "item_ids": ["0269514a-10cf-5d67-abf2-17a2eff9194c", "76fefd78-63d9-54b6-89ab-24af682930eb"],
         "evidence": "The original displays 湘潮(下半月), 2012年第10期, 总第391期, 2012年10月, printed page 1, and the complete single-page article. Candidate repeatedly says publication/page/boundary are unknown.",
         "required_change": "Use a separately evidenced original-image observation to resolve layout and metadata; preserve text anchors and distinguish the receipt date 2012-8-27."},
        {"severity": "material", "code": "unread_reference_content_overclaim",
         "item_ids": ["213cfa3a-9ac6-50a7-9f99-325d6dbcb6fd", "15e92cfe-47e3-5121-9d42-4e15595db21b", "93480d04-6cb2-53e7-8048-5bdbc57d34bb"],
         "evidence": "A 1990 compilation's publication year does not establish that all documents contained within it were formed later. The cited volume has not been read in this task.",
         "required_change": "Limit conclusions to the observed bibliography and this article's citation practice; leave the cited collection's individual source layers undetermined."},
        {"severity": "material", "code": "date_basis_unresolved",
         "item_ids": [], "evidence": "formation_date.basis contains E8 and event_date.basis contains E1/E4/E9, while stored item IDs are UUIDs.",
         "required_change": "Resolve model-local date basis identifiers during assembly, using the same mapping as evidence and entity references."},
        {"severity": "minor", "code": "normalized_text_formatting_visible_in_quote",
         "item_ids": ["e6f8605d-204e-548c-88b6-f11fe4caead1", "04917d0a-ef5b-50d3-a621-ed6208a1a63d"],
         "evidence": "Heading markers # and ## belong to the extracted representation and do not appear as printed characters on the original.",
         "required_change": "Display verified original wording using exact selected subranges or explicitly label normalized extraction. Never silently change text behind an existing hash."}
    ],
    "independence_limit": "Same active GPT prepared the source reference and assessed the candidate; machine development judgment, not independent human gold.",
    "production_policy": "DeepSeek remains the production reviewer. This record does not adopt, rewrite, or mark production checks passed.",
    "evidence": [{"path": str(path), "sha256": sha256(path.read_bytes()), "byte_length": path.stat().st_size} for path in paths]
}
atomic_json(output / "candidate-original-first-gpt-review-v1.json", verdict)
print(verdict["verdict"])
