"""Coordinated backup joins native state to SQL without requiring legacy databases."""

import json
from pathlib import Path

from PIL import Image

from hrs.config import Settings
from hrs.processing.application import prepare
from hrs.processing.store import Store
from hrs.storage import backups


def test_codex_manifest_round_trips_sql_and_native_checkpoints(tmp_path, monkeypatch):
    settings = Settings.load().model_copy(
        update={
            "codex_root": tmp_path / "native",
            "cache_root": tmp_path / "cache",
            "processing_backend": "codex",
        }
    )
    source = tmp_path / "source.png"
    Image.new("RGB", (20, 30), "white").save(source)
    store = Store(settings.codex_root)
    doc = prepare(store, str(source))
    store.request_run(doc["id"])
    objects = {}

    class Storage:
        def put_bytes(self, data):
            key = str(len(objects))
            objects[key] = data
            return {"key": key}

        def put_file(self, path, media):
            return self.put_bytes(Path(path).read_bytes())

        def read_bytes(self, reference):
            return objects[reference["key"]]

        def materialize(self, reference, destination):
            destination.write_bytes(self.read_bytes(reference))
            return destination

    storage = Storage()
    monkeypatch.setattr(backups, "require_quiesced", lambda *_: None)
    monkeypatch.setattr(backups, "objects_for", lambda settings: storage)
    dumped, restored = [], []

    def dump(settings, name, destination):
        dumped.append(name)
        destination.write_bytes(b"Synthetic SQL dump; native ZIP is real")

    monkeypatch.setattr(backups, "dump_database", dump)
    monkeypatch.setattr(
        backups, "restore_database", lambda settings, name, source: restored.append(name)
    )
    reference = backups.backup(settings)
    manifest = json.loads(storage.read_bytes(reference))
    assert len(dumped) == 1 and not any("temporal" in name for name in dumped)
    assert manifest["native_document_ids"] == [doc["id"]]
    result = backups.restore(settings, reference, "test", native_destination=tmp_path / "restored")
    assert len(restored) == 1
    after = Store(Path(result["native_workspace"])).status(doc["id"])
    assert after["run_request"] == store.status(doc["id"])["run_request"]
    assert after["pages"] == doc["pages"]
