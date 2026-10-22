"""Hash-bound source detail crops without recognition or semantic reconstruction."""

import io
import json
import math
from hashlib import sha256
from importlib.metadata import version

import pypdfium2 as pdfium
from PIL import Image, ImageOps

from hrs_codex.render import _atomic_bytes
from hrs_codex.store import Store, atomic_json, file_digest


def original_detail(store: Store, document_id: str, page: int, box: list[float]) -> tuple[dict, bytes]:
    """Render a retained PDF at 432 dpi, or use a raster frame's native pixels."""
    if (
        len(box) != 4
        or any(not math.isfinite(value) or not 0 <= value <= 1 for value in box)
        or not (box[0] < box[2] and box[1] < box[3])
    ):
        raise ValueError("Detail box must be normalized [left, top, right, bottom] within the page")
    document = store.status(document_id)
    if not any(row["page"] == page for row in document["pages"]):
        raise ValueError("Unknown physical page")
    source = store._path(document, document["source"]["path"])
    expected = document["source"]["sha256"]
    if file_digest(source) != expected:
        raise ValueError("Retained original source hash mismatch")
    is_pdf = source.suffix.lower() == ".pdf"
    policy = (
        f"pdfium-{version('pypdfium2')}-432dpi-normalized-crop-v1"
        if is_pdf
        else "raster-native-exif-white-background-normalized-crop-v1"
    )
    request = {
        "document_id": document_id,
        "source_sha256": expected,
        "page": page,
        "box": [float(value) for value in box],
        "render_policy": policy,
    }
    key = sha256(json.dumps(request, sort_keys=True).encode("utf-8")).hexdigest()[:24]
    relative = f"evidence/details/{key}.png"
    metadata_relative = f"evidence/details/{key}.json"
    image_path = store._path(document, relative)
    metadata_path = image_path.with_suffix(".json")
    if image_path.exists() and metadata_path.exists():
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        data = image_path.read_bytes()
        if any(metadata.get(name) != value for name, value in request.items()) or metadata.get("image") != relative:
            raise ValueError("Retained original detail metadata mismatch")
        if sha256(data).hexdigest() != metadata["image_sha256"]:
            raise ValueError("Retained original detail hash mismatch")
        return {
            **metadata,
            "metadata": metadata_relative,
            "metadata_sha256": file_digest(metadata_path),
        }, data

    if is_pdf:
        with pdfium.PdfDocument(source) as pdf:
            physical_page = pdf[page - 1]
            try:
                bitmap = physical_page.render(scale=6)
                try:
                    image = bitmap.to_pil().copy()
                finally:
                    bitmap.close()
            finally:
                physical_page.close()
    else:
        with Image.open(source) as frame:
            frame.seek(page - 1)
            image = ImageOps.exif_transpose(frame).copy()
    width, height = image.size
    bounds = [
        math.floor(box[0] * width),
        math.floor(box[1] * height),
        math.ceil(box[2] * width),
        math.ceil(box[3] * height),
    ]
    cropped = image.crop(bounds)
    rgba = cropped.convert("RGBA")
    rendered = Image.new("RGB", cropped.size, "white")
    rendered.paste(rgba, mask=rgba.getchannel("A"))
    buffer = io.BytesIO()
    rendered.save(buffer, format="PNG")
    data = buffer.getvalue()
    if file_digest(source) != expected:
        raise ValueError("Retained original source changed during detail rendering")
    detail_hash = sha256(data).hexdigest()
    metadata = {
        "schema_version": 1,
        **request,
        "source_size": [width, height],
        "crop_pixels": bounds,
        "image_size": list(rendered.size),
        "image": relative,
        "image_sha256": detail_hash,
        "detail_sha256": detail_hash,
    }
    image_path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_bytes(image_path, data)
    atomic_json(metadata_path, metadata)
    return {
        **metadata,
        "metadata": metadata_relative,
        "metadata_sha256": file_digest(metadata_path),
    }, data
