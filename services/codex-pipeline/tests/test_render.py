from pathlib import Path

import pytest
from PIL import Image
from pypdf import PdfWriter

from hrs_codex.render import digest, render_document


def test_tiff_retains_frames_and_detects_changed_evidence(tmp_path):
    source = tmp_path / "scan.tiff"
    first = Image.new("RGB", (120, 160), "white")
    second = Image.new("RGB", (140, 180), "black")
    first.save(source, save_all=True, append_images=[second])
    target = tmp_path / "retained"
    result = render_document(source, target)
    assert [page["page"] for page in result["pages"]] == [1, 2]
    assert [(page["width"], page["height"]) for page in result["pages"]] == [(120, 160), (140, 180)]
    assert result["source"]["sha256"] == digest(source)
    assert Path(result["source"]["path"]).read_bytes() == source.read_bytes()
    assert render_document(source, target) == result
    Path(result["pages"][0]["image"]).write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed or lost"):
        render_document(source, target)


def test_pdf_renders_physical_pages_and_rejects_encryption(tmp_path):
    source = tmp_path / "book.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=150)
    writer.add_blank_page(width=200, height=250)
    with source.open("wb") as stream:
        writer.write(stream)
    result = render_document(source, tmp_path / "render")
    assert result["page_count"] == 2
    assert [(page["width"], page["height"]) for page in result["pages"]] == [(300, 450), (600, 750)]
    writer.encrypt("password")
    with source.open("wb") as stream:
        writer.write(stream)
    with pytest.raises(ValueError, match="Encrypted"):
        render_document(source, tmp_path / "encrypted")


def test_import_retries_incomplete_output_but_cannot_replace_original(tmp_path):
    source = tmp_path / "page.png"
    Image.new("RGB", (40, 50), "white").save(source)
    target = tmp_path / "retained"
    target.mkdir()
    (target / "source.png").write_bytes(source.read_bytes())
    assert render_document(source, target)["page_count"] == 1
    Image.new("RGB", (40, 50), "black").save(source)
    with pytest.raises(ValueError, match="different source"):
        render_document(source, target)


def test_empty_invalid_and_unsupported_sources_do_not_produce_manifest(tmp_path):
    for name, content in [("empty.pdf", b""), ("invalid.png", b"not an image"), ("unknown.txt", b"text")]:
        source = tmp_path / name
        source.write_bytes(content)
        target = tmp_path / name.replace(".", "-")
        with pytest.raises((ValueError, OSError)):
            render_document(source, target)
        assert not (target / "render.json").exists()


def test_transparent_scan_uses_white_paper_without_changing_retained_source(tmp_path):
    source = tmp_path / "transparent.png"
    scan = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
    scan.putpixel((1, 1), (0, 0, 0, 255))
    scan.save(source)
    manifest = render_document(source, tmp_path / "render")
    with Image.open(manifest["pages"][0]["image"]) as page:
        assert page.getpixel((0, 0)) == (255, 255, 255)
        assert page.getpixel((1, 1)) == (0, 0, 0)
    assert Path(manifest["source"]["path"]).read_bytes() == source.read_bytes()
