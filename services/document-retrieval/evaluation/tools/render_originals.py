"""Render selected original PDF pages before viewing extraction or retrieval candidates."""

import hashlib
import json
import sys
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    request_path = Path(sys.argv[1])
    request = json.loads(request_path.read_text(encoding="utf-8"))
    output = request_path.parent / "original-pages"
    output.mkdir(parents=True, exist_ok=True)
    report = []
    for item in request:
        source = Path(item["source"])
        document = pymupdf.open(source)
        pages = []
        thumbs = []
        for number in item["pages"]:
            page = document[number - 1]
            path = output / f"{item['id']}-{number:04d}.png"
            page.get_pixmap(matrix=pymupdf.Matrix(1.8, 1.8), alpha=False).save(path)
            pages.append(
                {"physical_page": number, "path": str(path.resolve()), "sha256": digest(path)}
            )
            thumb = Image.open(path).convert("RGB")
            thumb.thumbnail((650, 880))
            tile = Image.new("RGB", (670, 920), "#e3e6e9")
            tile.paste(thumb, ((670 - thumb.width) // 2, 30))
            ImageDraw.Draw(tile).text(
                (12, 9), f"{item['id']} / physical page {number}", fill="black"
            )
            thumbs.append(tile)
        montage = Image.new(
            "RGB", (670 * min(2, len(thumbs)), 920 * ((len(thumbs) + 1) // 2)), "white"
        )
        for index, tile in enumerate(thumbs):
            montage.paste(tile, ((index % 2) * 670, (index // 2) * 920))
        montage_path = output / f"{item['id']}-contact.png"
        montage.save(montage_path)
        report.append(
            {
                **item,
                "source_sha256": digest(source),
                "original_pages": len(document),
                "rendered_pages": pages,
                "contact_sheet": str(montage_path.resolve()),
                "contact_sha256": digest(montage_path),
            }
        )
        document.close()
        print(
            json.dumps(
                {
                    "id": item["id"],
                    "page_count": report[-1]["original_pages"],
                    "contact": str(montage_path.resolve()),
                }
            )
        )
    request_path.with_name(request_path.stem + "-rendered.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
