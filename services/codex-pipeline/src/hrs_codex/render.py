"""Retain originals and render physical pages; recognition belongs to Codex."""

import hashlib
import json
import os
import shutil
from pathlib import Path
from uuid import uuid4

import pypdfium2 as pdfium
from PIL import Image, ImageOps, ImageSequence
from pypdf import PdfReader

MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _atomic_bytes(path: Path, content: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _save_page(image: Image.Image, path: Path, number: int) -> dict:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        rgba = image.convert("RGBA")
        rendered = Image.new("RGB", image.size, "white")
        rendered.paste(rgba, mask=rgba.getchannel("A"))
        rendered.save(temporary, format="PNG")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "page": number,
        "image": str(path),
        "image_sha256": digest(path),
        "width": image.width,
        "height": image.height,
    }


def _verify_manifest(manifest: dict, source_hash: str, destination: Path) -> None:
    if manifest["source"]["sha256"] != source_hash:
        raise ValueError("Destination already belongs to a different source document.")
    paths = [(manifest["source"]["path"], source_hash)]
    paths.extend((page["image"], page["image_sha256"]) for page in manifest["pages"])
    if not manifest["pages"] or manifest["page_count"] != len(manifest["pages"]):
        raise ValueError("Retained render manifest has an invalid page count.")
    for name, expected in paths:
        path = Path(name).resolve()
        if not path.is_relative_to(destination) or not path.is_file() or digest(path) != expected:
            raise ValueError(f"Retained source or page evidence was changed or lost: {path}")


def render_document(source: Path, destination: Path) -> dict:
    """Render PDF or PNG/JPEG/TIFF into a recoverable directory without OCR.

    Existing completed output is reused only after checking every evidence hash.
    A failed first render retains the original and can be retried safely. A
    completed directory with changed evidence requires restoring that evidence.
    """
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if not source.is_file():
        raise ValueError("Source document is not a readable file.")
    suffix = source.suffix.lower()
    if suffix not in MEDIA_TYPES:
        raise ValueError("Supported source formats: PDF, PNG, JPEG and TIFF.")
    source_hash = digest(source)
    manifest_path = destination / "render.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text("utf-8"))
        _verify_manifest(manifest, source_hash, destination)
        return manifest
    destination.mkdir(parents=True, exist_ok=True)
    retained = destination / ("source" + suffix)
    if retained.exists():
        if digest(retained) != source_hash:
            raise ValueError("Destination already contains a different retained source.")
    else:
        temporary = retained.with_name(f".source.{uuid4().hex}.tmp")
        try:
            shutil.copyfile(source, temporary)
            if digest(temporary) != source_hash:
                raise ValueError("Source changed while it was being retained; retry the import.")
            temporary.replace(retained)
        finally:
            temporary.unlink(missing_ok=True)
    pages_directory = destination / "pages"
    pages_directory.mkdir(exist_ok=True)
    pages = []
    if suffix == ".pdf":
        try:
            reader = PdfReader(retained)
            if reader.is_encrypted or not len(reader.pages):
                raise ValueError("Encrypted or empty PDFs cannot be imported.")
        except (ValueError, OSError):
            raise
        except Exception as error:
            raise ValueError("Source could not be read as a PDF.") from error
        with pdfium.PdfDocument(retained) as document:
            for number in range(len(document)):
                page = document[number]
                try:
                    bitmap = page.render(scale=3)
                    try:
                        image = bitmap.to_pil()
                        pages.append(_save_page(image, pages_directory / f"{number + 1:06d}.png", number + 1))
                    finally:
                        bitmap.close()
                finally:
                    page.close()
    else:
        with Image.open(retained) as document:
            for number, frame in enumerate(ImageSequence.Iterator(document), start=1):
                image = ImageOps.exif_transpose(frame)
                pages.append(_save_page(image, pages_directory / f"{number:06d}.png", number))
    if not pages:
        raise ValueError("Source document did not contain any pages.")
    manifest = {
        "schema_version": 1,
        "document_id": source_hash[:24],
        "source": {
            "path": str(retained),
            "sha256": source_hash,
            "original_name": source.name,
            "media_type": MEDIA_TYPES[suffix],
        },
        "page_count": len(pages),
        "render_policy": "pdfium-216dpi-or-image-native-pixels-exif-white-background-rgb-png-v1",
        "pages": pages,
    }
    _atomic_bytes(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
    return manifest
