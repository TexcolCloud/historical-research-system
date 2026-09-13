"""Public HTTP input preflight; does not call any retrieval or ranking operation."""

import argparse
import json
from pathlib import Path

from document_retrieval.records import atomic_json, text_hash
from document_retrieval.settings import Settings
from document_retrieval.upstream import Ingestion, anchor_for, field_allowed

MODULE = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=MODULE / "state/real-corpus")
    parser.add_argument("--ids", nargs="+")
    parser.add_argument("--reviewed", action="store_true")
    parser.add_argument("--version", default="")
    args = parser.parse_args()
    source = Ingestion(Settings.load(MODULE / "config/local.example.toml"))
    for receipt_path in sorted(args.root.glob("*/input.json")):
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if args.ids and receipt["id"] not in args.ids:
            continue
        suffix = "-" + args.version if args.version else ""
        final = receipt_path.parent / f"fixed-inputs{suffix}.json"
        if final.exists():
            continue
        if args.reviewed:
            adopted = json.loads(
                (receipt_path.parent / "research-reading-v3/result.json").read_text("utf-8")
            )
            ids = [group["snapshot"]["snapshot_id"] for group in adopted["groups"]]
        else:
            ids = [receipt["carrier_snapshot_id"]]
            ids.extend(
                effect["snapshot_id"]
                for group in receipt["application"]["groups"]
                for effect in group["receipt"]["effects"]
                if effect["kind"] == "set_reading"
            )
        fixed_inputs = []
        for snapshot_id in ids:
            fixed = source.fixed_input(snapshot_id)
            fixed["evaluation_adopted_pages"] = sorted(
                {
                    item["physical_page"]
                    for item in source.items(f"snapshots/{snapshot_id}/segments")
                }
            )
            sources = fixed["sources"]
            assert all(
                len(item["text"]) == item["end"] - item["start"]
                and text_hash(item["text"]) == item["text_sha256"]
                for item in sources
            )
            fixed["evaluation_usage"] = {
                purpose: source.usage(
                    [anchor_for(item) for item in sources], purpose, ["text", "provenance"]
                )
                for purpose in ("source_reading", "card_input")
            }
            fixed_inputs.append(fixed)
        fingerprint = atomic_json(final, fixed_inputs)
        summary = {
            "family": receipt["id"],
            "fixed_inputs_sha256": fingerprint,
            "items": [
                {
                    "snapshot_id": fixed["snapshot"]["snapshot_id"],
                    "occurrence_id": fixed["snapshot"].get("occurrence_id"),
                    "title": fixed["context"]["citation"].get("title"),
                    "target_role": fixed["context"]["target_role"],
                    "adopted_source_pages": fixed["evaluation_adopted_pages"],
                    "index_qualified_source_pages": sorted(
                        {item["physical_page"] for item in fixed["sources"]}
                    ),
                    "source_range_count": len(fixed["sources"]),
                    "index_excluded_ranges": len(fixed["excluded"]),
                    "source_reading_qualified_ranges": sum(
                        field_allowed(item, ["text", "provenance"])
                        for batch in fixed["evaluation_usage"]["source_reading"]
                        for item in batch["items"]
                    ),
                    "card_input_qualified_ranges": sum(
                        field_allowed(item, ["text", "provenance"])
                        for batch in fixed["evaluation_usage"]["card_input"]
                        for item in batch["items"]
                    ),
                }
                for fixed in fixed_inputs
            ],
        }
        atomic_json(receipt_path.parent / f"fixed-summary{suffix}.json", summary)
        print(
            json.dumps(
                {
                    "family": receipt["id"],
                    "fixed_input_count": len(fixed_inputs),
                    "sha256": fingerprint,
                }
            ),
            flush=True,
        )
    source.close()


if __name__ == "__main__":
    main()
