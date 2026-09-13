"""Render original PDF pages for GPT review; this never admits a sample or edits OCR."""

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ids", nargs="+")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    for row in json.loads(args.plan.read_text("utf-8")):
        if args.ids and row["id"] not in args.ids:
            continue
        source = Path(row["source"])
        source_hash = digest(source)
        if row.get("source_sha256") and row["source_sha256"] != source_hash:
            raise ValueError("The original PDF changed: " + row["id"])
        metadata = subprocess.run(["pdfinfo", str(source)], capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
        count = int(re.search(r"^Pages:\s+(\d+)$", metadata.stdout, re.MULTILINE)[1])
        pages = row.get("pages", list(range(1, count + 1)))
        folder = args.output / row["id"]
        folder.mkdir(parents=True, exist_ok=True)
        images = []
        for page in pages:
            assert 1 <= page <= count
            stem = folder / f"original-{page:04d}"
            path = stem.with_suffix(".png")
            if not path.exists():
                subprocess.run(["pdftoppm", "-f", str(page), "-l", str(page), "-r", str(row.get("dpi", 144)),
                    "-singlefile", "-png", str(source), str(stem)], check=True, capture_output=True)
            images.append({"physical_page": page, "path": str(path.resolve()), "sha256": digest(path)})
        result = {**row, "source_sha256": source_hash, "physical_page_count": count, "images": images,
            "render_only": True, "sample_admitted": False, "review_performed": False}
        (folder / "original-manifest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), "utf-8")
        print(json.dumps({"id": row["id"], "rendered_pages": len(images), "source_sha256": source_hash}), flush=True)


if __name__ == "__main__":
    main()
