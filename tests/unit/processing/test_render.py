import json
from pathlib import Path

import pytest
from PIL import Image
from pypdf import PdfWriter

from hrs.processing.application import prepare
from hrs.processing.render import digest, render_document
from hrs.processing.store import Store


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
    for name, content in [
        ("empty.pdf", b""),
        ("invalid.png", b"not an image"),
        ("unknown.txt", b"text"),
    ]:
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


@pytest.mark.parametrize("suffix", ["pdf", "tiff"])
def test_prepare_retains_one_image_per_page_and_reuses_it_on_retry(tmp_path, suffix):
    source = tmp_path / f"two-pages.{suffix}"
    if suffix == "pdf":
        writer = PdfWriter()
        writer.add_blank_page(width=40, height=50)
        writer.add_blank_page(width=50, height=60)
        writer.write(source)
    else:
        Image.new("RGB", (40, 50), "white").save(
            source, save_all=True, append_images=[Image.new("RGB", (50, 60), "black")]
        )
    store = Store(tmp_path / "workspace")
    document = prepare(store, str(source))
    directory = store.document_directory(document["id"])
    retained = {directory / page["image"] for page in document["pages"]}
    manifest = json.loads((directory / "render.json").read_text("utf-8"))

    assert {path.name for path in retained} == {"00001.png", "00002.png"}
    assert set((directory / "pages").iterdir()) == retained
    assert {Path(page["image"]) for page in manifest["pages"]} == retained
    before = {path: (digest(path), path.stat().st_mtime_ns) for path in retained}
    assert prepare(store, str(source))["pages"] == document["pages"]
    assert {path: (digest(path), path.stat().st_mtime_ns) for path in retained} == before
    assert set((directory / "pages").iterdir()) == retained
    store.verify_evidence(document)


def test_prepare_reuses_existing_six_digit_render_without_rewriting_evidence(tmp_path):
    source = tmp_path / "old-scan.png"
    Image.new("RGB", (40, 50), "white").save(source)
    store = Store(tmp_path / "workspace")
    directory = store.root / "documents" / digest(source)[:24]
    manifest = render_document(source, directory)
    old_page = directory / "pages" / "000001.png"
    current_page = Path(manifest["pages"][0]["image"])
    if current_page != old_page:
        current_page.rename(old_page)
    manifest["pages"][0]["image"] = str(old_page)
    manifest_path = directory / "render.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    original_manifest = manifest_path.read_bytes()

    document = prepare(store, str(source))

    assert document["pages"][0]["image"] == "pages/000001.png"
    assert list((directory / "pages").iterdir()) == [old_page]
    assert manifest_path.read_bytes() == original_manifest
    assert prepare(store, str(source))["pages"] == document["pages"]
    store.verify_evidence(document)


def test_external_render_keeps_an_independent_retained_copy(tmp_path):
    source = tmp_path / "borrowed.png"
    Image.new("RGB", (40, 50), "white").save(source)
    manifest = render_document(source, tmp_path / "external-render")
    store = Store(tmp_path / "workspace")
    document = store.register_manifest(manifest)
    directory = store.document_directory(document["id"])
    retained = directory / document["pages"][0]["image"]

    assert retained != Path(manifest["pages"][0]["image"])
    Path(manifest["source"]["path"]).write_bytes(b"changed external source")
    Path(manifest["pages"][0]["image"]).write_bytes(b"changed external page")
    assert digest(retained) == document["pages"][0]["image_sha256"]
    store.verify_evidence(document)
