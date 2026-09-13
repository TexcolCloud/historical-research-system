"""Read raw public source archives for original-first review, never query evidence."""

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from document_retrieval.records import atomic_json
from document_retrieval.settings import Settings
from document_retrieval.upstream import Ingestion

MODULE = Path(__file__).resolve().parents[2]


def archive(path):
    receipt = json.loads(path.read_text("utf-8"))
    output = path.parent / "original-review-source-archive.json"
    if output.exists():
        return receipt["id"], "carried"
    source = Ingestion(Settings.load(MODULE / "config/local.example.toml"))
    try:
        spans = source.items(f"snapshots/{receipt['carrier_snapshot_id']}/segments")
        digest = atomic_json(
            output,
            {
                "classification": "raw_source_archive_for_development_review",
                "current_usage_evaluated": False,
                "retrieval_eligible": False,
                "source_sha256": receipt["source_sha256"],
                "carrier_snapshot_id": receipt["carrier_snapshot_id"],
                "spans": spans,
            },
        )
        return receipt["id"], {"sha256": digest, "spans": len(spans)}
    finally:
        source.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ids", nargs="+")
    args = parser.parse_args()
    paths = [
        path
        for path in (MODULE / "state/real-corpus").glob("*/input.json")
        if not args.ids or path.parent.name in args.ids
    ]
    with ThreadPoolExecutor(max_workers=3) as pool:
        for family, result in pool.map(archive, paths):
            print(json.dumps({"family": family, "result": result}), flush=True)


if __name__ == "__main__":
    main()
