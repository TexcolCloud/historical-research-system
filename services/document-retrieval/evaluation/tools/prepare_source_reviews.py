"""Prepare fixed, raw source candidates after original-page inspection; no retrieval."""

import argparse
import json
from pathlib import Path

from document_retrieval.records import atomic_json, text_hash

MODULE = Path(__file__).resolve().parents[2]


def candidates(family, pages):
    path = MODULE / "state/real-corpus" / family / "original-review-source-archive.json"
    archive = json.loads(path.read_text("utf-8"))
    rows = []
    for span in archive["spans"]:
        if span["physical_page"] not in pages:
            continue
        for block in span["readiness"]:
            start, end = max(span["start"], block["start"]), min(span["end"], block["end"])
            if start >= end:
                continue
            value = span["text"][start - span["start"] : end - span["start"]]
            rows.append(
                {
                    "id": f"r{start}-{end}",
                    "physical_page": span["physical_page"],
                    "source": {
                        "snapshot_id": span["snapshot_id"],
                        "source_span_ref": span["source_span_ref"],
                        "start": start,
                        "end": end,
                        "expected_text_sha256": text_hash(value),
                    },
                    "kind": block["kind"],
                    "status": block["status"],
                    "block_id": block["block_id"],
                    "dependencies": block.get("dependencies", []),
                    "restricted_fields": block.get("restricted_fields", []),
                    "text": value,
                }
            )
    return sorted(rows, key=lambda row: (row["physical_page"], row["source"]["start"]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ids", nargs="+")
    parser.add_argument("--show", action="store_true")
    args = parser.parse_args()
    by_family = {}
    for name in ("calibration-original-tasks-v2.json", "holdout-original-tasks.json"):
        for task in json.loads((MODULE / "evaluation/development" / name).read_text("utf-8"))[
            "tasks"
        ]:
            for family in task["families"]:
                by_family.setdefault(family, set()).update(
                    task.get("pages_by_family", {}).get(family, task.get("pages", []))
                )
    for family, pages in by_family.items():
        if family == "english-pending" or args.ids and family not in args.ids:
            continue
        rows = candidates(family, pages)
        output = MODULE / "state/real-corpus" / family / "qualification-candidates-v1.json"
        if not output.exists():
            atomic_json(
                output,
                {
                    "classification": "unreviewed_fixed_source_candidates",
                    "family": family,
                    "pages": sorted(pages),
                    "candidates": rows,
                },
            )
        if args.show:
            print(f"FAMILY {family}")
            for row in rows:
                print(
                    f"\n{row['id']} page={row['physical_page']} kind={row['kind']} state={row['status']} dependencies={row['dependencies']}\n{row['text']}"
                )
        else:
            print(
                json.dumps(
                    {"family": family, "pages": sorted(pages), "candidate_count": len(rows)}
                ),
                flush=True,
            )


if __name__ == "__main__":
    main()
