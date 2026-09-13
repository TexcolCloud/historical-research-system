"""Display public source text for original-first review without truncating ranges."""

import argparse
import sys
from collections import defaultdict
from pathlib import Path

from research_cards.records import read_json

sys.stdout.reconfigure(encoding="utf-8")
parser = argparse.ArgumentParser()
parser.add_argument("input", type=Path)
parser.add_argument("--pages", type=int, nargs="+")
parser.add_argument("--excluded", action="store_true")
args = parser.parse_args()
value = read_json(args.input)
if args.excluded:
    for index, row in enumerate(value["excluded_layout_inspection"]):
        if not row["whitespace_only"] and (not args.pages or row.get("physical_page") in args.pages):
            print(f"EXCLUDED RANGE {index}; PHYSICAL PAGE {row.get('physical_page')}; START {row['reference']['start']}")
            print(row["archive_fragment"])
else:
    by_page = defaultdict(list)
    for batch in value["research_pages"]:
        for item in batch["items"]:
            if item["kind"] == "included":
                row = item["source"]
                if not args.pages or row["physical_page"] in args.pages:
                    by_page[row["physical_page"]].append(row)
    for page, rows in sorted(by_page.items()):
        print(f"PHYSICAL PAGE {page}; {len(rows)} included ranges")
        for row in sorted(rows, key=lambda r: (r["position"], r["start"])):
            print(row["text"])
