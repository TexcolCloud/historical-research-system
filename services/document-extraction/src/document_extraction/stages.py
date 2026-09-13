"""Durable phase adapter for the platform; recognition and review rules are unchanged.

The original CLI continues to run its complete pipeline. This adapter separates
the same converter and reviewer so orchestration can commit OCR evidence first.
"""

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

from .utils import sha256, write_json

CHECKPOINT = "ocr-checkpoint.json"


def save_checkpoint(source, output, pages, timings, backend, accelerator, config):
    output = Path(output).resolve()
    payload = {
        "schema_version": 1,
        "source_sha256": sha256(Path(source)),
        "config_sha256": sha256(Path(config)),
        "backend": backend,
        "accelerator": accelerator,
        "timings": timings,
        "pages": [{**page, "image_path": Path(page["image_path"]).resolve().relative_to(output).as_posix()} for page in pages],
        "files": {path.relative_to(output).as_posix(): sha256(path) for path in sorted(output.rglob("*")) if path.is_file() and path.name not in {CHECKPOINT, CHECKPOINT + ".tmp"}},
    }
    temporary = output / (CHECKPOINT + ".tmp")
    write_json(temporary, payload)
    temporary.replace(output / CHECKPOINT)
    return payload


def load_checkpoint(source, output, config):
    output = Path(output).resolve()
    payload = json.loads((output / CHECKPOINT).read_text("utf-8"))
    if payload["schema_version"] != 1 or payload["source_sha256"] != sha256(Path(source)) or payload["config_sha256"] != sha256(Path(config)):
        raise ValueError("OCR checkpoint belongs to different source or configuration")
    if not payload["pages"] or not payload["files"]:
        raise ValueError("OCR checkpoint has no complete evidence")
    for name, digest in payload["files"].items():
        path = (output / name).resolve()
        if not path.is_relative_to(output) or sha256(path) != digest:
            raise ValueError("OCR checkpoint evidence differs from its committed source")
    for page in payload["pages"]:
        name = page["image_path"]
        if name not in payload["files"]:
            raise ValueError("OCR page image has no bound evidence")
        page["image_path"] = output / name
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["ocr", "review"])
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    from .settings import Settings
    from .pipeline import DocumentExtractor, run_document

    settings = Settings.load(args.config)
    if args.phase == "ocr":
        args.output.mkdir(parents=True, exist_ok=True)
        extractor = DocumentExtractor(settings)
        try:
            pages, timings = extractor.converter.convert_document(args.source.resolve(), args.output.resolve())
            save_checkpoint(args.source, args.output, pages, timings, extractor.converter.name, extractor.accelerator.report(), args.config)
        finally:
            extractor.close()
        print(json.dumps({"stage": "ocr", "pages": len(pages), "reviewed": False}))
    else:
        if not settings.vision_review.enabled or settings.vision_review.review_mode != "full":
            raise ValueError("Platform document review requires the full configured visual review")
        checkpoint = load_checkpoint(args.source, args.output, args.config)
        cached = SimpleNamespace(name=checkpoint["backend"], convert_document=lambda *_: (checkpoint["pages"], checkpoint["timings"]))
        accelerator = SimpleNamespace(report=lambda: checkpoint["accelerator"])
        manifest = run_document(args.source, args.output, settings, cached, accelerator)
        print(json.dumps({"stage": "review", "content_status": manifest["content_status"]}))


if __name__ == "__main__":
    main()
