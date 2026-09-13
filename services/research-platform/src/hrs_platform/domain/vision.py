import io

from PIL import Image

from .records import sha256


def original_crops(raw):
    """Shared native-pixel evidence crops, independent of any business store."""
    with Image.open(io.BytesIO(raw)) as image:
        width, height = image.size
        image = image.convert("RGB")
        # Six overlapping native-pixel crops keep small type legible despite the provider's
        # automatic image resizing. They contain only original pixels; no OCR correction.
        regions = [(0, 0, width, height)]
        for row in range(3):
            for column in range(2):
                regions.append(
                    (
                        max(0, round(width * (column / 2 - 0.025))),
                        max(0, round(height * (row / 3 - 0.025))),
                        min(width, round(width * ((column + 1) / 2 + 0.025))),
                        min(height, round(height * ((row + 1) / 3 + 0.025))),
                    )
                )
        results = []
        for region in regions:
            stream = io.BytesIO()
            image.crop(region).save(stream, format="PNG")
            content = stream.getvalue()
            digest = sha256(content)
            results.append(
                {
                    "content": content,
                    "sha256": digest,
                    "byte_length": len(content),
                    "media_type": "image/png",
                    "original_dimensions": [width, height],
                    "crop_box_pixels": list(region),
                    "transformation": "native_pixel_crop_rgb_png",
                    "original_text_modified": False,
                }
            )
    return results
