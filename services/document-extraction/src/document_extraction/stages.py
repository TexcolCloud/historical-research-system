"""Durable phase adapter for the platform; recognition and review rules are unchanged.

The original CLI continues to run its complete pipeline. This adapter separates
the same converter and reviewer so orchestration can commit OCR evidence first.
"""

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

from .utils import sha256, write_json
from .provenance import text_hash

CHECKPOINT = "ocr-checkpoint.json"


def extraction_fingerprint(config):
    values = {key: value for key, value in config.items() if key not in {'vision_review', 'risk'}}
    return text_hash(json.dumps(values, sort_keys=True, ensure_ascii=False))


def save_checkpoint(source, output, pages, timings, backend, accelerator, config):
    output = Path(output).resolve()
    payload = {
        "schema_version": 2,
        "config": json.loads(Path(config).read_text('utf-8')),
        "extraction_sha256": extraction_fingerprint(json.loads(Path(config).read_text('utf-8'))),
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


def load_checkpoint(source, output, config, *, reuse_legacy=False):
    output = Path(output).resolve()
    payload = json.loads((output / CHECKPOINT).read_text("utf-8"))
    values = json.loads(Path(config).read_text('utf-8'))
    version = payload['schema_version']
    config_matches = (payload.get('extraction_sha256') == extraction_fingerprint(values)
                      if version == 2 else payload['config_sha256'] == sha256(Path(config)))
    # Previously committed OCR may be reviewed under the old full-review policy.
    # Never grant native acceptance to these legacy artifacts on configuration drift.
    legacy_full = (reuse_legacy and version == 1 and not values.get('native_pdf')
                   and values.get('vision_review', {}).get('review_mode') == 'full'
                   and not any(p.get('native_evidence') for p in payload['pages']))
    if version not in {1, 2} or payload["source_sha256"] != sha256(Path(source)) or not (config_matches or legacy_full):
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
            save_checkpoint(args.source, args.output, pages, timings, extractor.converter.name, extractor.converter.accelerator.report(), args.config)
        finally:
            extractor.close()
        print(json.dumps({"stage": "ocr", "pages": len(pages), "reviewed": False}))
    else:
        if not settings.vision_review.enabled or settings.vision_review.review_mode != "full":
            raise ValueError("Platform document review requires the full configured visual review")
        checkpoint = load_checkpoint(args.source, args.output, args.config, reuse_legacy=True)
        cached = SimpleNamespace(name=checkpoint["backend"], convert_document=lambda *_: (checkpoint["pages"], checkpoint["timings"]))
        accelerator = SimpleNamespace(report=lambda: checkpoint["accelerator"])
        manifest = run_document(args.source, args.output, settings, cached, accelerator)
        print(json.dumps({"stage": "review", "content_status": manifest["content_status"]}))


if __name__ == "__main__":
    main()
