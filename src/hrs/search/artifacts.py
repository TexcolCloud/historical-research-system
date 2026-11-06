"""Inspect complete pinned retrieval artifacts without loading a model runtime."""

import hashlib
import json
from pathlib import Path

from hrs.search.settings import MODEL_REVISIONS


def _nonempty(path):
    return path.is_file() and path.stat().st_size > 0


def _contained(directory, name):
    path = (directory / name).resolve()
    if not path.is_relative_to(directory.resolve()) or path == directory.resolve():
        raise ValueError("Artifact manifest path escapes its model directory")
    return path


def _weight_files(directory):
    for name in ("model.safetensors.index.json", "pytorch_model.bin.index.json"):
        index = directory / name
        if index.is_file():
            weight_map = json.loads(index.read_text("utf-8")).get("weight_map")
            if not isinstance(weight_map, dict) or not weight_map:
                raise ValueError("Weight index has no tensor files")
            names = sorted(set(weight_map.values()))
            if any(not isinstance(value, str) for value in names):
                raise ValueError("Invalid tensor file in model index")
            for value in names:
                _contained(directory, value)
            return [name, *names]
    return next(
        (
            [name]
            for name in ("model.safetensors", "pytorch_model.bin")
            if _nonempty(directory / name)
        ),
        ["model.safetensors or pytorch_model.bin"],
    )


def inspect_artifacts(models_root: Path) -> dict:
    """A config alone, a zero-byte file or an incomplete shard set is not present."""
    report = {}
    for model, revision in MODEL_REVISIONS.items():
        directory = Path(models_root) / model.split("/")[-1] / revision
        missing, errors, checked = [], [], []
        try:
            names = [
                "config.json",
                "tokenizer.json",
                "tokenizer_config.json",
                *_weight_files(directory),
            ]
            missing = [name for name in names if not _nonempty(_contained(directory, name))]
            for name in ("config.json", "tokenizer_config.json"):
                if name not in missing:
                    json.loads((directory / name).read_text("utf-8"))
            manifest_path = directory / "retrieval-model-manifest.json"
            if manifest_path.is_file():
                manifest = json.loads(manifest_path.read_text("utf-8"))
                if manifest.get("model") != model or manifest.get("revision") != revision:
                    raise ValueError("Model manifest identity differs from pinned revision")
                for row in manifest.get("files", []):
                    path = _contained(directory, row["file"])
                    if not path.is_file() or path.stat().st_size != row["size"]:
                        errors.append(f"Manifest size mismatch: {row['file']}")
                        continue
                    sha = hashlib.sha256()
                    with path.open("rb") as stream:
                        for part in iter(lambda: stream.read(1024 * 1024), b""):
                            sha.update(part)
                    if sha.hexdigest() != row["sha256"]:
                        errors.append(f"Manifest hash mismatch: {row['file']}")
                    checked.append(row["file"])
        except (OSError, ValueError, TypeError, KeyError) as error:
            errors.append(type(error).__name__ + ": " + str(error))
        report[model] = {
            "path": str(directory),
            "revision": revision,
            "present": not missing and not errors,
            "missing": missing,
            "errors": errors,
            "checksum_files": checked,
            "inference_checked": False,
        }
    return report
