"""Import reviewed real families through the unchanged ingestion public client.

This evaluation adapter only records explicit packaging path bindings. It never edits
the extraction output, queries an upstream database, or assigns human approval.
"""

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path
from types import SimpleNamespace

from document_ingestion.client import ManagementClient, submit_package
from document_ingestion.packing import PackageFiles, pack_directory
from document_ingestion.source_contract import read_table_evidence

MODULE = Path(__file__).resolve().parents[2]


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


class ExplicitBindings(PackageFiles):
    def resolve(self, reference, *, referrer=None, pointer=""):
        normalized = reference.replace("\\", "/")
        direct = self.root / normalized
        if direct.is_file() and direct.resolve().is_relative_to(self.root):
            return direct.relative_to(self.root).as_posix()
        candidates = []
        for marker in ("/ocr/", "/pages/", "/reviews/", "/assets/"):
            if marker in normalized:
                candidate = self.root / (marker[1:] + normalized.rsplit(marker, 1)[1])
                if candidate.is_file():
                    candidates.append(candidate)
        if not candidates:
            candidates = list(self.root.rglob(Path(normalized).name))
            match = re.search(r"page-\d+", referrer or "")
            if match:
                candidates = [path for path in candidates if match[0] in path.as_posix()]
        if referrer and pointer:
            parent = self.document(referrer)
            for key in pointer.split("/")[1:-1]:
                key = key.replace("~1", "/").replace("~0", "~")
                parent = parent[int(key)] if isinstance(parent, list) else parent[key]
            expected_hash = (
                parent.get("image_sha256", parent.get("sha256"))
                if isinstance(parent, dict)
                else None
            )
            if expected_hash and normalized.endswith(".png"):
                possible = list(self.root.rglob(Path(normalized).stem + "*.png"))
                candidates = [
                    path
                    for path in possible
                    if hashlib.sha256(path.read_bytes()).hexdigest() == expected_hash
                ]
            elif len(candidates) > 1:
                table = re.search(r"/tables/(\d+)/", pointer)
                suffix = f"-{int(table[1]) + 1:03d}" if table and int(table[1]) else ""
                if not table:
                    index = re.search(r"table-structure(-\d+)?/", referrer)
                    suffix = (index[1] or "") if index else ""
                preferred = [
                    path
                    for path in candidates
                    if ("/table-structure" + suffix + "/") in path.as_posix()
                ]
                if preferred:
                    candidates = preferred
        candidates = list(dict.fromkeys(path.resolve() for path in candidates))
        if len(candidates) != 1 or not candidates[0].is_relative_to(self.root):
            raise ValueError(f"Unresolved explicit evidence binding: {reference!r}, {referrer!r}")
        relative = candidates[0].relative_to(self.root).as_posix()
        binding = {
            "kind": "file_reference",
            "reference": reference,
            "referrer": referrer,
            "field_pointer": pointer,
            "package_path": relative,
        }
        if binding not in self.bindings:
            self.bindings.append(binding)
        return relative


def bindings_for(package):
    collection = ExplicitBindings(package, [])
    manifest = collection.document("manifest.json")
    mapping = collection.document(collection.resolve(manifest["source_map"]))
    pages = []
    for page in manifest["pages"]:
        refs = {
            s["source_ref"].partition("#")[0] for s in mapping["spans"] if s["page"] == page["page"]
        }
        for ref in sorted(refs):
            pages.append({"page": page["page"], "review": collection.file(collection.resolve(ref))})
    read_table_evidence(collection, pages)
    return collection.bindings


def import_one(row, origin, output):
    folder = output / row["id"]
    folder.mkdir(parents=True, exist_ok=True)
    final = folder / "input.json"
    if final.exists():
        return json.loads(final.read_text(encoding="utf-8"))
    package = Path(row["package"])
    try:
        bindings = bindings_for(package)
    except ValueError:
        # Some historical post-review bundles deliberately carried only final files.
        # Their unchanged review documents still name the original table evidence.
        inventory = json.loads(
            (MODULE / "evaluation/development/source-candidate-inventory.json").read_text(
                encoding="utf-8"
            )
        )
        family = inventory["families"].get(row["source_sha256"])
        origins = [
            Path(path)
            for path in (family or {}).get("packages", [])
            if (Path(path) / "ocr").is_dir()
        ]
        if (package / "ocr").exists() or len(origins) != 1:
            raise
        prepared = folder / "prepared-package"
        shutil.copytree(package, prepared, dirs_exist_ok=True)
        shutil.copytree(origins[0] / "ocr", prepared / "ocr", dirs_exist_ok=True)
        save(
            folder / "carried-attachment-source.json",
            {
                "current_final_files": str(package),
                "unchanged_ocr_evidence_origin": str(origins[0] / "ocr"),
                "prepared_package": str(prepared),
                "rule": "All referenced files must pass the normal frozen source contract and every declared evidence hash.",
            },
        )
        package = prepared
        bindings = bindings_for(package)
    save(folder / "bindings.json", bindings)
    archive = folder / "真实来源.zip"
    if not archive.exists():
        packed = pack_directory(package, Path(row["source"]), archive, bindings=bindings)
        save(folder / "packing.json", packed)
    args = SimpleNamespace(
        url=origin,
        package=archive,
        existing_carrier=None,
        display_name=Path(row["source"]).stem,
        format_mode="standard",
        receipt=folder / "submission-receipt.json",
        wait=True,
        timeout=600,
    )
    submitted, exit_code = submit_package(args)
    save(folder / "submission.json", submitted)
    if exit_code:
        raise RuntimeError(f"Import failed for {row['id']}: see {folder / 'submission.json'}")
    client = ManagementClient(origin)
    receipt = submitted["import"]["receipt"]
    partition = client.wait_job(receipt["partition_job_id"], 600)
    save(folder / "partition.json", partition)
    if partition["execution_state"] != "completed":
        raise RuntimeError(f"Partition did not complete for {row['id']}")
    candidate = partition["result"]
    manifest = client.get(f"/api/v1/drafts/{candidate['draft_id']}/manifest")
    save(folder / "machine-draft-manifest.json", manifest)
    key = "retrieval-real-" + row["id"]
    draft = client.post("/api/v1/draft-manifests", manifest, key + "-draft")
    preview = client.post(
        f"/api/v1/drafts/{draft['draft_id']}/previews",
        {"draft_revision_id": draft["current_revision_id"]},
        key + "-preview",
    )
    save(folder / "preview.json", preview)
    if not all(group["state"] == "ready" for group in preview["groups"]):
        raise RuntimeError(f"Preview requires resolution for {row['id']}")
    applied = client.post(
        f"/api/v1/drafts/{draft['draft_id']}/applications",
        {
            "draft_revision_id": draft["current_revision_id"],
            "preview_id": preview["preview_id"],
            "preview_fingerprint": preview["fingerprint"],
            "selected_group_ids": [group["group_id"] for group in preview["groups"]],
            "reason": "GPT original-first development machine adoption for retrieval evaluation; not user-human review or sealed gold.",
        },
        key + "-apply",
    )
    application = client.wait_job(applied["job"]["job_id"], 600)
    save(folder / "application.json", application)
    if (
        application["execution_state"] != "completed"
        or application["result"]["outcome"] != "succeeded"
    ):
        raise RuntimeError(f"Application failed for {row['id']}")
    carrier_snapshot = client.get(f"/api/v1/snapshots/{receipt['snapshot_id']}")
    with archive.open("rb") as stream:
        archive_sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
    report = {
        **row,
        "classification": "real_frozen_public_ingestion_input",
        "carrier_snapshot_id": receipt["snapshot_id"],
        "carrier_id": carrier_snapshot["carrier_id"],
        "extraction_id": receipt["extraction_id"],
        "receipt": receipt,
        "application": application["result"],
        "human_review": False,
        "sealed_gold": False,
        "archive_sha256": archive_sha256,
    }
    save(final, report)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:18125")
    parser.add_argument(
        "--selection", type=Path, default=MODULE / "evaluation/development/corpus-selection.json"
    )
    parser.add_argument("--ids", nargs="+")
    parser.add_argument("--output", type=Path, default=MODULE / "state/real-corpus")
    args = parser.parse_args()
    for row in json.loads(args.selection.read_text(encoding="utf-8")):
        if args.ids and row["id"] not in args.ids:
            continue
        result = import_one(row, args.url, args.output)
        print(
            json.dumps({"id": row["id"], "carrier_snapshot_id": result["carrier_snapshot_id"]}),
            flush=True,
        )


if __name__ == "__main__":
    main()
