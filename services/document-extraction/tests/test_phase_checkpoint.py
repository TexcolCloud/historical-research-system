"""Phase recovery checks use synthetic bytes, never claim recognition accuracy."""

import shutil

import pytest

from document_extraction.stages import load_checkpoint, save_checkpoint


def test_ocr_checkpoint_survives_directory_relocation_and_rejects_changed_original(tmp_path):
    source, config = tmp_path / "source.pdf", tmp_path / "config.json"
    source.write_bytes(b"synthetic source")
    config.write_text("{}")
    output = tmp_path / "original-cache"
    output.mkdir()
    image = output / "page.png"
    image.write_bytes(b"synthetic original image bytes")
    (output / "raw.md").write_text("1938年，粮食120吨。", encoding="utf-8")
    save_checkpoint(source, output, [{"page": 1, "image_path": image, "text": "1938年，粮食120吨。"}], {}, "frozen-test-backend", {}, config)
    restored = tmp_path / "different-cache"
    shutil.copytree(output, restored)
    result = load_checkpoint(source, restored, config)
    assert result["pages"][0]["image_path"] == restored / "page.png"
    assert result["pages"][0]["text"] == "1938年，粮食120吨。"
    (restored / "page.png").write_bytes(b"changed")
    with pytest.raises(ValueError, match="evidence differs"):
        load_checkpoint(source, restored, config)


def test_checkpoint_cannot_be_reused_for_a_different_source(tmp_path):
    source, config = tmp_path / "source.pdf", tmp_path / "config.json"
    source.write_bytes(b"one")
    config.write_text("{}")
    output = tmp_path / "cache"
    output.mkdir()
    image = output / "page.png"
    image.write_bytes(b"image")
    save_checkpoint(source, output, [{"page": 1, "image_path": image}], {}, "test", {}, config)
    source.write_bytes(b"two")
    with pytest.raises(ValueError, match="different source"):
        load_checkpoint(source, output, config)
