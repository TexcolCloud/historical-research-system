"""Copy original PDF pages, preserving the parent-file and physical-page mapping."""

import hashlib
import json
from pathlib import Path

import pymupdf


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


project = Path(__file__).resolve().parents[4]
base = project / "services/document-retrieval/evaluation/development"
requests = json.loads((base / "primary-originals.json").read_text(encoding="utf-8"))
root = project / "services/document-retrieval/state/primary-excerpts"
root.mkdir(parents=True, exist_ok=True)
items = []
for item in requests:
    if item["id"] == "primary-northwest-1941":
        continue
    start, end = (60, 68) if item["id"] == "primary-new-fourth-army" else (9, 14)
    path = root / (item["id"] + ".pdf")
    source = pymupdf.open(item["source"])
    excerpt = pymupdf.open()
    excerpt.insert_pdf(source, from_page=start - 1, to_page=end - 1)
    excerpt.set_metadata(
        {
            "title": item["id"] + " / declared original-page excerpt",
            "subject": "Evaluation copy; no source pixels or wording changed.",
        }
    )
    excerpt.save(path)
    excerpt.close()
    items.append(
        {
            "id": item["id"],
            "source": str(path.resolve()),
            "source_sha256": digest(path),
            "parent_source": item["source"],
            "parent_sha256": digest(Path(item["source"])),
            "parent_page_count": len(source),
            "physical_page_map": [
                {"physical_page": index - start + 1, "parent_physical_page": index}
                for index in range(start, end + 1)
            ],
            "basis": "verbatim_original_PDF_page_copy",
            "family": item["id"],
            "independent_original_document": False,
        }
    )
    source.close()
(base / "primary-excerpt-manifest.json").write_text(
    json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8"
)
print(
    json.dumps(
        {"excerpts": len(items), "pages": sum(len(item["physical_page_map"]) for item in items)}
    )
)
