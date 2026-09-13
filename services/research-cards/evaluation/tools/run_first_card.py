"""One real, original-reviewed article through the durable workflow; not the formal holdout."""

import argparse
import asyncio
import json
from pathlib import Path

from research_cards.contracts import Control, GenerateCard
from research_cards.queue import TaskQueue
from research_cards.records import atomic_json
from research_cards.settings import Settings
from research_cards.store import Store
from research_cards.worker import Worker


async def main(args):
    settings = Settings.load(env_file=args.env_file).model_copy(update={"state_root": args.state.resolve(), "auto_sync": False})
    store = Store(settings)
    queue = TaskQueue(store)
    request = GenerateCard(kind="generate_card", primary_snapshot_id=args.snapshot)
    receipt, _ = queue.submit(args.key, request)
    if args.retry:
        observed = queue.view(receipt["task_id"])
        if observed["state"] in ("failed", "cancelled"):
            queue.control(receipt["task_id"], args.key + ":technical-retry:" + str(observed["control_version"]),
                Control(action="retry", expected_control_version=observed["control_version"], expected_attempt_id=observed["attempt_id"], reason="Diagnosed implementation defect corrected; reuse fixed input and cumulative accounting."))
    atomic_json(args.output / "receipt.json", receipt)
    worker = Worker(store)
    try:
        last_view = None
        while True:
            result = await worker.run_once()
            task = queue.view(receipt["task_id"])
            atomic_json(args.output / "task.json", task)
            view = {"task_id": task["task_id"], "state": task["state"], "stage": task["stage"],
                    "progress": task["progress"], "stop_reason": task["stop_reason"]}
            if view != last_view or result:
                print(json.dumps({**view, "unit": result}), flush=True)
                last_view = view
            if task["state"] in ("completed", "failed", "paused", "cancelled") or task["state"] == "waiting" and not task["wait_reason"].get("automatic"):
                break
            if not result:
                await asyncio.sleep(0.5)
        from research_cards.metering import Meter
        atomic_json(args.output / "usage.json", Meter(store).usage(receipt["task_id"]))
    finally:
        store.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--retry", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    asyncio.run(main(args))
