"""Register existing page-work references for the explicit real acceptance package."""

import argparse
import json
import re
from pathlib import Path, PurePosixPath

from document_ingestion.packing import PackageFiles
from document_ingestion.source_contract import read_table_evidence


class Registration(PackageFiles):
    def resolve(self, reference, *, referrer=None, pointer=""):
        normal = reference.replace("\\", "/")
        direct = self.root / normal
        if not PurePosixPath(normal).is_absolute() and ":" not in normal and direct.is_file():
            return super().resolve(reference, referrer=referrer, pointer=pointer)
        page = re.search(r"page-(\d{3})", referrer or "")
        if page is None:
            raise ValueError(f"No explicit page context: {referrer} {pointer}")
        work = f"ocr/page-{page[1]}-work"
        marker = normal.find(work + "/")
        candidates = (
            [normal[marker:]]
            if marker >= 0
            else [f"{work}/{normal}", f"{work}/table-structure/{normal}"]
        )
        found = [
            path
            for path in candidates
            if (self.root / path).is_file()
            and (self.root / path).resolve().is_relative_to(self.root)
        ]
        if len(found) != 1:
            raise ValueError(
                f"Missing or ambiguous explicit page-work reference: {referrer} {pointer} {reference} {found}"
            )
        binding = {
            "kind": "file_reference",
            "reference": reference,
            "referrer": referrer,
            "field_pointer": pointer,
            "package_path": found[0],
        }
        if binding not in self.bindings:
            self.bindings.append(binding)
        return found[0]


def register(package, output):
    collection = Registration(package, [])
    mapping = collection.document("source-map.json")
    pages = [
        {"page": span["page"], "review": collection.file(span["source_ref"].partition("#")[0])}
        for span in mapping["spans"]
    ]
    evidence = read_table_evidence(collection, pages)
    output.write_text(json.dumps(collection.bindings, indent=2) + "\n")
    print(
        json.dumps(
            {
                "binding_count": len(collection.bindings),
                "table_reference_count": len(evidence),
                "output": str(output),
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("package", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    register(args.package, args.output)
