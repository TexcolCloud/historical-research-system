"""Inspect final structured messages from failed calls; omit reasoning content."""
import argparse
import json
from pathlib import Path

from research_cards.records import read_json

parser = argparse.ArgumentParser()
parser.add_argument("--task-root", type=Path, required=True)
args = parser.parse_args()
for path in sorted((args.task_root / "calls").glob("*-response.json")):
    value = read_json(path)
    response = value.get("response") or {}
    if "output" not in response:
        continue
    texts = [part.get("text", "") for item in response["output"] if item.get("type") == "message"
             for part in item.get("content", []) if part.get("type") == "output_text"]
    if not texts:
        continue
    try:
        output = json.loads("".join(texts))
        if "question" in output:
            print(json.dumps({"file": path.name, "message": output}, ensure_ascii=False))
    except ValueError as error:
        print(json.dumps({"file": path.name, "parse_error": str(error), "text": "".join(texts)[:2200]}, ensure_ascii=False))
