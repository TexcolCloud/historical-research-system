from __future__ import annotations

import argparse
import json
from pathlib import Path

from .docling_conversion import DoclingConverter
from .accelerator import detect
from .pipeline import DocumentExtractor, run
from .settings import Settings


SERVICE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = SERVICE_ROOT / "config" / "default.json"


def check(settings: Settings) -> int:
    from docling.datamodel.base_models import InputFormat

    accelerator = detect(settings.acceleration)
    report = {
        "backends": {},
        "structure_models": {},
        "env_file": str(settings.env_file),
        "model_root": str(settings.model_root),
        "accelerator": accelerator.report(),
        "conversion_framework": "docling / pypdfium2",
    }
    backend = None
    name = settings.ocr_backends[0]["name"]
    try:
        backend = DoclingConverter(settings, accelerator, native_plan={})
        backend.converter.initialize_pipeline(InputFormat.PDF)
        report["backends"][name] = "ok"
    except Exception as exc:
        report["backends"][name] = str(exc)
    finally:
        if backend is not None:
            backend.close()
    report["deepseek_api_key"] = "set" if settings.vision_review.api_key else "missing"
    report["vision_api_mode"] = settings.vision_review.api_mode
    report["vision_endpoint"] = settings.vision_review.endpoint
    report["vision_model"] = settings.vision_review.model
    report["vision_review_enabled"] = settings.vision_review.enabled
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if all(value == "ok" for value in report["backends"].values()) else 1


def _run_batch(
    root: Path,
    output_root: Path,
    extractor: DocumentExtractor,
    memory_scope: str | None,
) -> dict:
    completed, failures = [], []
    for source in sorted(root.rglob("*.pdf")):
        output = output_root / source.relative_to(root).with_suffix("")
        try:
            manifest = extractor.run(source, output, memory_scope or source.stem)
            completed.append(
                {
                    "source": str(source),
                    "output": str(output),
                    "page_count": manifest["page_count"],
                    "pending_human_review": manifest["pending_human_review"],
                }
            )
        except Exception as exc:
            failures.append(
                {"source": str(source), "error": f"{type(exc).__name__}: {exc}"}
            )
    summary = {
        "document_count": len(completed),
        "failed_document_count": len(failures),
        "page_count": sum(item["page_count"] for item in completed),
        "pending_human_review": sum(item["pending_human_review"] for item in completed),
        "completed": completed,
        "failures": failures,
    }
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "batch-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Docling 文档转换与单路识别、语义复核服务"
    )
    parser.add_argument("--env", type=Path, help="覆盖主项目根目录.env")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    sub = parser.add_subparsers(dest="command", required=True)
    extract = sub.add_parser("extract")
    extract.add_argument("input", type=Path)
    extract.add_argument("--output", type=Path, required=True)
    extract.add_argument(
        "--memory-scope",
        help="兼容旧命令；现役流程不使用纠错记忆",
    )
    batch = sub.add_parser("extract-batch")
    batch.add_argument("input", type=Path, help="递归查找PDF的目录")
    batch.add_argument("--output-root", type=Path, required=True)
    batch.add_argument("--memory-scope", help="兼容旧命令；现役流程不使用纠错记忆")
    sub.add_parser("check")
    quote = sub.add_parser(
        "verify-quote",
        help="Check exact quotation provenance; does not generate history cards",
    )
    quote.add_argument("output", type=Path)
    quote.add_argument("--quote", required=True)
    quote.add_argument("--start", type=int)
    quote.add_argument("--markdown-sha256")
    args = parser.parse_args()
    if args.command == "verify-quote":
        from .content_readiness import verify_quote

        verdict = verify_quote(
            args.output,
            args.quote,
            start=args.start,
            expected_markdown_sha256=args.markdown_sha256,
        )
        print(json.dumps(verdict, ensure_ascii=False, indent=2))
        return 0 if verdict["status"] == "evidence-linked" else 1
    settings = Settings.load(args.config, args.env)
    if args.command == "check":
        return check(settings)
    if args.command == "extract-batch":
        root = args.input.resolve()
        if not root.is_dir():
            parser.error(f"批量输入不是目录：{root}")
        sources = sorted(root.rglob("*.pdf"))
        if not sources:
            parser.error(f"目录内没有PDF：{root}")
        extractor = DocumentExtractor(settings)
        try:
            summary = _run_batch(root, args.output_root, extractor, args.memory_scope)
        finally:
            extractor.close()
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 1 if summary["failures"] else 0
    manifest = run(args.input, args.output, settings, args.memory_scope)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
