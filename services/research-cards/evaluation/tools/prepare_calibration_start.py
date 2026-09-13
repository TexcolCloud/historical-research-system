"""Record reviewed admissions and a finite calibration configuration before sends."""

from importlib.metadata import version
from pathlib import Path

from research_cards import generation_contracts, prompts
from research_cards.records import atomic_json, fingerprint, now, read_json, sha256
from research_cards.settings import Settings

MODULE = Path(__file__).resolve().parents[2]
DEVELOPMENT = MODULE / "evaluation/development"
OUTPUT = DEVELOPMENT / "calibration-v1"


def basis(value):
    return [row["source"] for page in value["research_pages"] for row in page["items"] if row["kind"] == "included"]


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    readiness = {row["snapshot_id"]: row for row in read_json(DEVELOPMENT / "readiness-preflight-v1.json")["page"]["items"]}
    rows = []
    for identity, family, snapshot in [
        ("C01", "family-15", "6be34d7d-4bca-4048-a924-669f9075a21b"),
        ("C02", "family-04", "9666b3cd-3361-4b1c-a936-5b7c0bc30e9f"),
    ]:
        old = read_json(DEVELOPMENT / "source-preflight-v1" / (snapshot + ".json"))
        path = DEVELOPMENT / "calibration-source-inputs-v1" / (snapshot + ".json")
        current = read_json(path)
        # Repair a development preflight display-field variable shadow; research inputs
        # and fresh layout inspection remain unchanged and are compared below.
        current["readiness"] = readiness[snapshot]
        atomic_json(path, current)
        assert fingerprint(basis(old)) == fingerprint(basis(current)), "Re-review changed source text/context"
        assert current["summary"]["excluded_nonwhitespace_ranges"] == 0
        source_manifest = read_json(DEVELOPMENT / "originals" / family / "original-manifest.json")
        reference = DEVELOPMENT / "originals/family-04/original-reference.json" if family == "family-04" else DEVELOPMENT / "family-15-original-reference-v1.json"
        request = {"kind": "generate_card", "primary_snapshot_id": snapshot, "regenerate": True,
            "trigger": "calibration_v1", "workload": "interactive"}
        atomic_json(OUTPUT / (identity + "-request.json"), request)
        rows.append({"sample_id": identity, "family": family, "snapshot_id": snapshot,
            "status": "admitted_before_generation", "source_sha256": source_manifest["source_sha256"],
            "main_research_pages": 12 if family == "family-04" else 1,
            "source_reference_path": str(reference), "source_reference_sha256": sha256(reference.read_bytes()),
            "fresh_input_path": str(path), "fresh_input_sha256": sha256(path.read_bytes()),
            "source_coverage": "complete_main_article_and_attached_notes", "research_card_quality_pass": False,
            "prior_exposure": "extraction/retrieval development; family-15 additionally has two earlier engineering card tasks",
            "machine_review": True, "human_source_review": False, "sealed_gold": False})
    for identity, family in [("C03", "family-05"), ("C04", "family-03"), ("C05", "family-16"),
            ("C06", "primary-new-fourth-army:report"), ("C07", "primary-new-fourth-army:telegram-0928"),
            ("C08", "primary-new-fourth-army:telegram-1001")]:
        rows.append({"sample_id": identity, "family": family.split(":")[0], "proposed_work": family,
            "status": "pending_original_and_public_input_review", "research_card_quality_pass": False})
    atomic_json(OUTPUT / "samples.json", {"at": now(), "calibration_only": True, "planned_main_samples": 8,
        "holdout_samples_run": 0, "samples": rows,
        "selection_note": "Replace tentatively proposed family-07 with family-16 before generation to include a 25-page main research article; family-07 remains a possible holdout source. No candidate result influenced this choice."})
    prices = {"deepseek-v4-flash": ["0.007", "0.22", "0.66"], "deepseek-v4-pro": ["0.022", "0.66", "1.98"],
        "deepseek-v4-flash-vision-exp": ["0.007", "0.22", "0.66"]}
    price_page = DEVELOPMENT / "deepseek-pricing-calibration-v1.html"
    atomic_json(OUTPUT / "prices.json", {"at": now(), "official_url": "https://api-docs.deepseek.com/quick_start/pricing/",
        "page_sha256": sha256(price_page.read_bytes()), "currency": "USD", "per_tokens": 1000000,
        "rate_order": ["cached_input", "uncached_input", "output"], "off_peak_rates": prices,
        "peak_multiplier": 2, "peak_utc_weekdays": [0, 1, 2, 3, 4], "peak_utc_hours": [[1, 4], [6, 10]],
        "unknown_usage_is_zero": False, "cache_input_is_subset": True, "reasoning_output_is_subset": True,
        "vision_tokens_already_in_input": True, "charges_are_estimates_from_actual_reported_usage_not_provider_invoices": True})
    files = [*sorted((MODULE / "src/research_cards").glob("*.py")), MODULE / "pyproject.toml", MODULE / "uv.lock"]
    settings = Settings.load(MODULE / "config/development.toml", MODULE.parents[1] / ".env")
    from agents import AgentOutputSchema
    from pydantic import BaseModel
    for contract in vars(generation_contracts).values():
        if isinstance(contract, type) and issubclass(contract, BaseModel) and contract.__module__ == generation_contracts.__name__:
            AgentOutputSchema(contract).json_schema()
    atomic_json(OUTPUT / "configuration-lock.json", {"at": now(), "phase": "finite_calibration_baseline",
        "settings": settings.public(), "budget": settings.budget(), "prompt_version": prompts.VERSION,
        "packages": {name: version(name) for name in ["openai", "openai-agents", "pydantic", "httpx", "sqlalchemy"]},
        "files": [{"path": str(path), "sha256": sha256(path.read_bytes())} for path in files],
        "maximum_semantic_revisions": 1, "development_review_model": "active Codex GPT vision",
        "production_reviewer_replaced_by_gpt": False,
        "reading_batch_basis": "48 units with 12000 codepoint cap and bounded split on truncation; Flash reading low effort was established by actual development calls. Preserve every original reading for separate Pro candidate checks."})
    print(f"Recorded {len(rows)} calibration slots; {sum(row['status']=='admitted_before_generation' for row in rows)} admitted; no task submitted by this script.")


if __name__ == "__main__":
    main()
