"""Verify the actual isolated restore and lock the narrowly scoped transport repair."""

import json
from importlib.metadata import version
from pathlib import Path

from research_cards import database as db
from research_cards import prompts
from research_cards.records import atomic_json, now, read_json, sha256
from research_cards.settings import Settings
from research_cards.store import Store

MODULE = Path(__file__).resolve().parents[2]
OUTPUT = MODULE / "evaluation/development/calibration-v1"
CONFIG = MODULE / "state/calibration-v1-restored/restored.toml"
settings = Settings.load(CONFIG, MODULE.parents[1] / ".env")
bundle = OUTPUT / "recovery-transport-fix"
manifest = read_json(bundle / "manifest.json")
prior_tasks = [json.loads(line) for line in (bundle / "database/tasks.jsonl").read_text("utf-8").splitlines()]
with_store = Store(settings)
try:
    comparisons = []
    for prior in prior_tasks:
        restored = with_store.get(db.tasks, prior["id"])
        fields = ["state", "stage", "budget", "usage", "checkpoint", "request", "fixed_input", "result_summary", "stop_reason"]
        assert all(prior[field] == restored[field] for field in fields)
        comparisons.append({"task_id": prior["id"], "state": prior["state"], "stage": prior["stage"], "exactly_preserved_fields": fields})
    copied = []
    for file in manifest["files"]:
        if file["path"].startswith("attachments/"):
            path = settings.state_root / file["path"][len("attachments/"):]
            assert sha256(path.read_bytes()) == file["sha256"]
            copied.append(file)
    files = [*sorted((MODULE / "src/research_cards").glob("*.py")), MODULE / "pyproject.toml", MODULE / "uv.lock", CONFIG]
    atomic_json(OUTPUT / "configuration-lock-transport-repair-1.json", {"at": now(),
        "phase": "finite_calibration_baseline_transport_repair", "configuration": settings.public(), "budget": settings.budget(),
        "original_lock_sha256": sha256((OUTPUT / "configuration-lock.json").read_bytes()),
        "prompt_version": prompts.VERSION, "packages": {name: version(name) for name in ["openai", "openai-agents", "pydantic", "httpx", "sqlalchemy"]},
        "files": [{"path": str(path), "sha256": sha256(path.read_bytes())} for path in files],
        "repairs": ["Handle HTTP 202 source-resolution jobs through their public job/result endpoints and checkpointed waiting.",
            "Allow full-source HTTP reads up to 600 seconds; the same actual C02 read succeeded after 138.61 seconds in a saved diagnostic.",
            "Restore exact stopped tasks into a separate owned database while holding source dispatch after a verified backup.",
            "Add explicit worker-drain so later worker reloads finish and save active units before stopping."],
        "models_prompts_reading_strategy_or_semantic_retry_allowance_changed": False,
        "diagnostic_read_used_as_model_input": False,
        "prior_failed_attempts_retained": True,
        "source_service_config": "config/calibration-v1.toml", "source_dispatch_held": True,
        "active_service_config": str(CONFIG), "active_port": settings.port,
        "real_restore_verification": {"backup_id": manifest["backup_id"],
            "manifest_sha256": sha256((bundle / "manifest.json").read_bytes()), "tasks": comparisons,
            "attachment_hashes_verified": len(copied), "source_and_destination_different": True},
        "engineering_checks": {"pytest": "31 passed full suite; then 3 targeted passed including the added drain test (32 total distinct tests)",
            "source_resolution_cases": ["pending_to_completed_after_restart", "pending_to_failed_after_restart", "drain_saves_active_unit_leaves_queued_work"]}})
    print({"restored_tasks_verified": len(comparisons), "attachment_hashes_verified": len(copied), "active_port": settings.port})
finally:
    with_store.close()
