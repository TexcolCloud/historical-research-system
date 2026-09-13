"""Fetch only the registered inference artifacts, preserving the OCR model layout."""

import argparse
import hashlib
import json
from pathlib import Path

from huggingface_hub import snapshot_download

from document_retrieval.settings import MODEL_REVISIONS


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--comparisons", action="store_true")
    args = parser.parse_args()
    for model, revision in MODEL_REVISIONS.items():
        if model.startswith("Qwen/") and not args.comparisons:
            continue
        directory = args.root.resolve() / model.split("/")[-1] / revision
        snapshot_download(
            model,
            revision=revision,
            local_dir=directory,
            allow_patterns=["*.json", "*.txt", "*.model", "*.safetensors", "pytorch_model.bin"],
            ignore_patterns=["onnx/*"],
            max_workers=4,
        )
        artifacts = []
        for file in sorted(directory.iterdir()):
            if file.is_file() and file.name != "retrieval-model-manifest.json":
                with file.open("rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
                artifacts.append({"file": file.name, "size": file.stat().st_size, "sha256": digest})
        manifest = {"model": model, "revision": revision, "files": artifacts}
        (directory / "retrieval-model-manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        print(
            json.dumps({"model": model, "revision": revision, "files": len(artifacts)}), flush=True
        )


if __name__ == "__main__":
    main()
