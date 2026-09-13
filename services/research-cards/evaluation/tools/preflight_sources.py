"""Inventory actual public source ranges; readiness and PDF page counts are not admission."""

import argparse
import asyncio
import json
from pathlib import Path

import httpx

from research_cards.records import atomic_json, now


async def main(args):
    ready = json.loads(args.readiness.read_text(encoding="utf-8"))["page"]["items"]
    if args.snapshots:
        ready = [row for row in ready if row["snapshot_id"] in args.snapshots]
    semaphore = asyncio.Semaphore(3)
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    async def inspect(row):
        snapshot_id = row["snapshot_id"]
        path = output / (snapshot_id + ".json")
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))["summary"]
        async with semaphore, httpx.AsyncClient(base_url=args.ingestion.rstrip("/") + "/api/v1/", timeout=120) as client:
            response = await client.get("snapshots/" + snapshot_id)
            response.raise_for_status()
            snapshot = response.json()
            pages, cursor = [], None
            while True:
                params = {"purpose": "card_input", "limit": 200}
                if cursor:
                    params["cursor"] = cursor
                response = await client.get("snapshots/" + snapshot_id + "/research-segments", params=params)
                response.raise_for_status()
                page = response.json()
                pages.append(page)
                cursor = page.get("next_cursor")
                if not cursor:
                    break
            ranges = [entry for page in pages for entry in page["items"]]
            archive, cursor = [], None
            while True:
                params = {"limit": 200}
                if cursor:
                    params["cursor"] = cursor
                response = await client.get("snapshots/" + snapshot_id + "/segments", params=params)
                response.raise_for_status()
                page = response.json()
                archive.extend(page["items"])
                cursor = page.get("next_cursor")
                if not cursor:
                    break
            excluded_details = []
            for entry in ranges:
                if entry["kind"] != "excluded":
                    continue
                ref = entry["reference"]
                matching = [segment for segment in archive if segment["source_span_ref"] == ref["source_span_ref"]
                    and segment["start"] <= ref["start"] < ref["end"] <= segment["end"]]
                fragment = matching[0]["text"][ref["start"] - matching[0]["start"]:ref["end"] - matching[0]["start"]] if len(matching) == 1 else None
                excluded_details.append({"reference": ref, "whitespace_only": fragment is not None and not fragment.strip(),
                    "archive_fragment": fragment, "not_for_model_input": True,
                    "physical_page": matching[0].get("physical_page") if len(matching) == 1 else None,
                    "position": matching[0].get("position") if len(matching) == 1 else None})
            counts = {kind: sum(1 for r in ranges if r["kind"] == kind) for kind in ("included", "excluded")}
            chars = {kind: sum(r["usage"]["reference"]["end"] - r["usage"]["reference"]["start"] for r in ranges if r["kind"] == kind) for kind in counts}
            summary = {"snapshot_id": snapshot_id, "occurrence_id": snapshot.get("occurrence_id"),
                       "source_segment_count": snapshot["segment_count"], "ranges": counts, "codepoints": chars,
                       "excluded_nonwhitespace_ranges": sum(not row["whitespace_only"] for row in excluded_details),
                       "whole_article_admitted": False, "admission_status": "requires_original_boundary_and_content_review"}
            atomic_json(path, {"readiness": row, "snapshot": snapshot, "research_pages": pages,
                "excluded_layout_inspection": excluded_details, "summary": summary})
            print(json.dumps(summary), flush=True)
            return summary
    values = await asyncio.gather(*(inspect(row) for row in ready))
    atomic_json(output / "summary.json", {"at": now(), "sources": values,
        "ready_entries": len(ready), "fully_permitted_enumerated_ranges": sum(not x["ranges"]["excluded"] for x in values),
        "whole_article_admissions": 0, "scope": "public_range_inventory_not_historical_quality_verdict"})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--readiness", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ingestion", default="http://127.0.0.1:18125")
    parser.add_argument("--snapshots", nargs="+")
    asyncio.run(main(parser.parse_args()))
