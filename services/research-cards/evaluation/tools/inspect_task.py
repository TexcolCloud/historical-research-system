"""Read only task diagnostics without printing credentials or model reasoning text."""
import argparse
import json
from pathlib import Path

from sqlalchemy import select

from research_cards import database as db
from research_cards.settings import Settings
from research_cards.store import Store, public_row


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--task", required=True)
    args = parser.parse_args()
    store = Store(Settings.load(env_file=args.env_file))
    with db.transaction(store.engine) as conn:
        task = store.get(db.tasks, args.task)
        calls = conn.execute(select(db.calls).where(db.calls.c.task_id == args.task, db.calls.c.kind == "model").order_by(db.calls.c.sequence)).mappings().all()
    print(json.dumps({"task": {key: task[key] for key in ("id", "state", "stage", "usage", "stop_reason")},
        "read_batch_sizes": [len(batch) for batch in task["checkpoint"].get("read_batches", [])], "completed_readings": len(task["checkpoint"].get("reading_records", [])),
        "calls": [{key: public_row(row)[key] for key in ("id", "sequence", "step_key", "status", "raw_usage", "error", "started_at", "finished_at")} for row in calls]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
