"""Inventory existing extraction packages without treating old outputs as adopted inputs."""

import hashlib
import json
from pathlib import Path

from document_retrieval.records import atomic_json


def main():
    project = Path(__file__).resolve().parents[4]
    root = project / "services/document-extraction/output"
    families = {}
    for path in root.rglob("manifest.json"):
        if not any(part.startswith("round") for part in path.parts):
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        if not value.get("source_sha256") or not str(value.get("source", "")).lower().endswith(
            ".pdf"
        ):
            continue
        required = [
            "document.candidate.md",
            "source-map.json",
            "content-readiness.json",
            "artifact-manifest.json",
        ]
        if not all((path.parent / name).is_file() for name in required):
            continue
        source = Path(value["source"])
        if not source.is_file():
            continue
        family = families.setdefault(
            value["source_sha256"],
            {"source": str(source), "declared_pages": value["page_count"], "packages": []},
        )
        family["packages"].append(str(path.parent))
    for digest, family in families.items():
        with Path(family["source"]).open("rb") as stream:
            family["actual_sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
        family["hash_matches"] = digest == family["actual_sha256"]
    atomic_json(
        project
        / "services/document-retrieval/evaluation/development/source-candidate-inventory.json",
        {"basis": "metadata_only_not_visual_review", "families": families},
    )
    for index, family in enumerate(families.values(), 1):
        print(
            json.dumps(
                {
                    "i": index,
                    "name": Path(family["source"]).name,
                    "pages": family["declared_pages"],
                    "packages": len(family["packages"]),
                    "latest": max(family["packages"]),
                    "hash_matches": family["hash_matches"],
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
