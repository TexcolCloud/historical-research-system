"""Select original source families and pages, without reading extracted candidates."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    families = json.loads(
        (ROOT / "development/source-candidate-inventory.json").read_text(encoding="utf-8")
    )["families"]
    corpus = []
    originals = []
    for number, (fingerprint, source) in enumerate(families.items(), 1):
        if number in (18, 19):
            continue
        pages = source["declared_pages"]
        selected = sorted({1, min(2, pages), max(1, pages // 2), pages})
        if number == 9:
            selected = [1, 3, 5, 7]
        if number == 20:
            selected = [10, 24, 38, 44]
        row = {
            "id": f"family-{number:02d}",
            "split": "calibration" if number <= 8 else "holdout",
            "source": source["source"],
            "source_sha256": fingerprint,
            "package": source["packages"][-1],
            "physical_pages_declared": pages,
            "original_review_pages": selected,
        }
        corpus.append(row)
        originals.append({"id": row["id"], "source": row["source"], "pages": selected})
    primary = json.loads((ROOT / "development/council-originals.json").read_text(encoding="utf-8"))[
        -1
    ]
    originals.append({**primary, "id": "new-fourth-report-tail", "pages": [65, 66, 67, 68]})
    (ROOT / "development/corpus-selection.json").write_text(
        json.dumps(corpus, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (ROOT / "development/corpus-originals.json").write_text(
        json.dumps(originals, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"selected_existing_families": len(corpus), "render_groups": len(originals)}))


if __name__ == "__main__":
    main()
