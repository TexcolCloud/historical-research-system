"""Lock two diagnosed host repairs before resuming the same cumulative calibration tasks."""

from pathlib import Path

from research_cards.client import Client
from research_cards.records import atomic_json, now, read_json, sha256

MODULE = Path(__file__).resolve().parents[2]
OUTPUT = MODULE / "evaluation/development/calibration-v1"
destination = OUTPUT / "configuration-lock-model-contract-repair-1.json"
assert not destination.exists(), "Preserve the existing repair record."
with Client("http://127.0.0.1:18142") as client:
    tasks = client.request("GET", "tasks", params={"limit": 100})["items"]
    assert all(task["state"] != "running" for task in tasks)
    observations = []
    for task in tasks:
        usage = client.request("GET", "tasks/" + task["task_id"] + "/usage")
        atomic_json(OUTPUT / (task["task_id"] + "-before-model-repair.json"), {"task": task, "usage": usage})
        observations.append({"task_id": task["task_id"], "state": task["state"], "stage": task["stage"],
            "stop_reason": task["stop_reason"], "usage": task["usage"], "control_version": task["control_version"]})
    atomic_json(destination, {"at": now(), "phase": "finite_calibration_baseline_host_contract_repair",
        "prior_lock_sha256": sha256((OUTPUT / "configuration-lock-transport-repair-1.json").read_bytes()),
        "repairs": ["Reasoning-model calls without an explicit cap now use the already configured max_reasoning_output=32000; reading/vision retain 16000.",
            "Validate reading-unit coverage and exact candidate quotations before accepting a durable model result; violations get the existing one format correction, then stop."],
        "prompt_version_changed": False, "production_model_routing_changed": False,
        "task_budget_changed": False, "semantic_repair_allowance_changed": False,
        "original_extraction_changed": False, "prior_calls_and_failed_attempts_retained": True,
        "worker_reload": "worker-drain finished active units before process exit; same database, same tasks and checkpoints",
        "source_files": [{"path": str(path.relative_to(MODULE)), "sha256": sha256(path.read_bytes())}
            for path in sorted((MODULE / "src/research_cards").rglob("*.py"))],
        "observed_tasks": observations,
        "regression_evidence": {"output_cap": "real SDK HTTP request and durable reservations: one red reasoner-default case, then all 3 cases passed",
            "reading_contract": "real SDK SSE and PostgreSQL: both cases red before fix; exact correction and repeated-invalid bounded stop both passed after fix"}})
    for sample in ("C01", "C02"):
        receipt = read_json(OUTPUT / (sample + "-receipt.json"))
        task_id = receipt["response"]["task_id"]
        task = next(row for row in tasks if row["task_id"] == task_id)
        assert task["state"] == "failed"
        request = {"action": "retry", "expected_control_version": task["control_version"],
            "expected_attempt_id": task["attempt_id"],
            "reason": "Diagnosed host output-cap / exact-reading validation integration repaired and regression tested. Retain fixed inputs, prior failures, cumulative budget and usage."}
        atomic_json(OUTPUT / (sample + "-model-repair-retry-request.json"), request)
        result = client.with_receipt(OUTPUT / (sample + "-model-repair-retry-receipt.json"), "POST", "tasks/" + task_id + "/controls", request)
        print({"sample": sample, "task_id": task_id, "control": result})
