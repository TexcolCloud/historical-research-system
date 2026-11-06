"""A complete shard set and its retained checksums determine artifact readiness."""

import hashlib
import json

from hrs.search.artifacts import inspect_artifacts
from hrs.search.settings import MODEL_REVISIONS


def model_folders(root):
    result = []
    for model, revision in MODEL_REVISIONS.items():
        folder = root / model.split("/")[-1] / revision
        folder.mkdir(parents=True)
        for name in ("config.json", "tokenizer.json", "tokenizer_config.json"):
            (folder / name).write_text("{}", "utf-8")
        result.append((model, revision, folder))
    return result


def test_incomplete_shards_remain_unavailable_until_every_referenced_file_exists(tmp_path):
    folders = model_folders(tmp_path)
    for _, _, folder in folders:
        (folder / "model.safetensors.index.json").write_text(
            json.dumps(
                {
                    "weight_map": {
                        "first": "model-00001.safetensors",
                        "last": "model-00002.safetensors",
                    },
                }
            ),
            "utf-8",
        )
        (folder / "model-00001.safetensors").write_bytes(b"first shard")
    partial = inspect_artifacts(tmp_path)
    assert all(
        not value["present"] and value["missing"] == ["model-00002.safetensors"]
        for value in partial.values()
    )
    for _, _, folder in folders:
        (folder / "model-00002.safetensors").write_bytes(b"last shard")
    assert all(value["present"] for value in inspect_artifacts(tmp_path).values())


def test_model_manifest_rejects_changed_weight_bytes_and_accepts_the_retained_checksum(tmp_path):
    folders = model_folders(tmp_path)
    for model, revision, folder in folders:
        original = b"fixed registered weights"
        (folder / "pytorch_model.bin").write_bytes(original)
        (folder / "retrieval-model-manifest.json").write_text(
            json.dumps(
                {
                    "model": model,
                    "revision": revision,
                    "files": [
                        {
                            "file": "pytorch_model.bin",
                            "size": len(original),
                            "sha256": hashlib.sha256(original).hexdigest(),
                        }
                    ],
                }
            ),
            "utf-8",
        )
    assert all(value["present"] for value in inspect_artifacts(tmp_path).values())
    for _, _, folder in folders:
        (folder / "pytorch_model.bin").write_bytes(b"other registered weights")
    assert all(
        not value["present"] and value["errors"] for value in inspect_artifacts(tmp_path).values()
    )


def test_weight_manifest_paths_cannot_escape_the_selected_model(tmp_path):
    for _, _, folder in model_folders(tmp_path):
        (folder / "model.safetensors.index.json").write_text(
            json.dumps(
                {
                    "weight_map": {"tensor": "../../outside-model.safetensors"},
                }
            ),
            "utf-8",
        )
    report = inspect_artifacts(tmp_path)
    assert all(
        not value["present"] and "escapes" in value["errors"][0] for value in report.values()
    )
