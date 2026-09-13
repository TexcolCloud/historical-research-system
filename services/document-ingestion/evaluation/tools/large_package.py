"""Reproducible, physically written ZIP64 engineering input; no historical claims."""

import argparse
import hashlib
import json
import shutil
import time
import zipfile
from pathlib import Path


def generate(output: Path, byte_length: int):
    receipt_path = output.with_suffix(".json")
    if output.exists():
        receipt = json.loads(receipt_path.read_text())
        assert (
            receipt["member_bytes"] == byte_length and output.stat().st_size == receipt["zip_bytes"]
        )
        return receipt
    output.parent.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(output.parent).free
    if free < byte_length * 8 + 10 * 1024**3:
        raise RuntimeError("Insufficient space for the declared eight-copy acceptance budget")
    started, digest = time.monotonic(), hashlib.sha256()
    block_size = 1024 * 1024
    member = "engineering/large-source.txt"
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        with archive.open(member, "w", force_zip64=True) as stream:
            for offset in range(0, byte_length, block_size):
                prefix = f"ENGINEERING TRANSPORT ONLY; BLOCK OFFSET={offset:020d}\n".encode()
                block = (prefix + b"0123456789abcdef" * (block_size // 16))[
                    : min(block_size, byte_length - offset)
                ]
                stream.write(block)
                digest.update(block)
        wrapper = {
            "schema_version": 1,
            "source_bindings": [],
            "files": [
                {
                    "path": member,
                    "role": "engineering_archive_source",
                    "sha256": digest.hexdigest(),
                    "byte_length": byte_length,
                }
            ],
        }
        raw = json.dumps(wrapper, sort_keys=True, separators=(",", ":")).encode()
        archive.writestr("ingestion-package.json", raw)
    zip_digest = hashlib.sha256()
    with output.open("rb") as stream:
        while block := stream.read(block_size):
            zip_digest.update(block)
    with zipfile.ZipFile(output) as archive:
        info = archive.getinfo(member)
        directory_offset = archive.start_dir
        manifest_offset = archive.getinfo("ingestion-package.json").header_offset
        assert info.file_size == byte_length
        if byte_length > 2**32:
            assert (
                info.extract_version >= 45 and manifest_offset > 2**32 and directory_offset > 2**32
            )
    receipt = {
        "classification": "engineering_transport_not_historical_source",
        "member_path": member,
        "member_bytes": byte_length,
        "member_sha256": digest.hexdigest(),
        "zip_bytes": output.stat().st_size,
        "zip_sha256": zip_digest.hexdigest(),
        "manifest_sha256": hashlib.sha256(raw).hexdigest(),
        "manifest_offset": manifest_offset,
        "central_directory_offset": directory_offset,
        "compression": "stored",
        "sparse_file": False,
        "generation_seconds": time.monotonic() - started,
        "free_bytes_before": free,
    }
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--bytes", type=int, default=5 * 1024**3 + 1)
    args = parser.parse_args()
    print(json.dumps(generate(args.output, args.bytes)))
