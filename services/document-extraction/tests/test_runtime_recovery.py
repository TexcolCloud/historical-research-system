"""Resource ownership and recoverable batch processing."""

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from document_extraction import cli
from document_extraction import ocr, pipeline
from document_extraction.docling_conversion import DoclingConverter
from document_extraction.utils import write_json
from types import SimpleNamespace


class RuntimeRecoveryTests(unittest.TestCase):
    def test_single_document_releases_converter_when_conversion_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = SimpleNamespace(
                ocr_backends=[{}], acceleration=None, model_root=Path(directory)
            )
            converter = Mock()
            with (
                patch.object(pipeline, "detect"),
                patch.object(pipeline, "DoclingConverter", return_value=converter),
                patch.object(
                    pipeline,
                    "run_document",
                    side_effect=RuntimeError("conversion failed"),
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "conversion failed"):
                    pipeline.run(Path("input.pdf"), Path("output"), settings)
            converter.close.assert_called_once()

    def test_failure_after_model_initialization_releases_converter(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "manifest.sha256").write_text("model hashes", encoding="utf-8")
            settings = SimpleNamespace(
                ocr_backends=[{}], acceleration=None, model_root=root
            )
            converter = Mock()
            with (
                patch.object(pipeline, "detect"),
                patch.object(pipeline, "DoclingConverter", return_value=converter),
                patch.object(
                    pipeline, "sha256", side_effect=OSError("manifest unreadable")
                ),
            ):
                with self.assertRaises(OSError):
                    pipeline.DocumentExtractor(settings)
            converter.close.assert_called_once()

    def test_docling_partial_construction_releases_recognizer(self):
        worker = Mock()

        def fail(converter):
            converter.recognizer = SimpleNamespace(pipeline=worker)
            raise RuntimeError("Docling setup failed")

        with (
            patch.object(DoclingConverter, "_build", fail),
            self.assertRaises(RuntimeError),
        ):
            DoclingConverter(
                SimpleNamespace(ocr_backends=[{"name": "paddleocr-vl"}]), None
            )
        worker.close.assert_called_once()

    def test_close_is_idempotent_even_after_close_error_and_run_is_rejected(self):
        extractor = pipeline.DocumentExtractor.__new__(pipeline.DocumentExtractor)
        worker = Mock()
        worker.close.side_effect = OSError("close failed")
        extractor.converter = worker
        with self.assertRaises(OSError):
            extractor.close()
        extractor.close()
        with self.assertRaisesRegex(RuntimeError, "closed"):
            extractor.run(Path("input.pdf"), Path("output"))
        worker.close.assert_called_once()

    def test_ocr_cache_binds_image_model_and_backend_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "page.png"
            image.write_bytes(b"original fixture")
            backend = SimpleNamespace(
                name="paddleocr-vl",
                pipeline_version="v1.6",
                engine="transformers",
                model_manifest_sha256="model-a",
            )
            first = ocr._ocr_cache_signature(backend, image)
            self.assertEqual(first, ocr._ocr_cache_signature(backend, image))
            image.write_bytes(b"changed fixture")
            image_changed = ocr._ocr_cache_signature(backend, image)
            backend.model_manifest_sha256 = "model-b"
            model_changed = ocr._ocr_cache_signature(backend, image)
            backend.pipeline_version = "other-version"
            version_changed = ocr._ocr_cache_signature(backend, image)
            self.assertEqual(
                len({first, image_changed, model_changed, version_changed}), 4
            )

    def test_malformed_or_stale_ocr_cache_is_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "page-001.paddleocr-vl.md").write_text(
                "1938年正文", encoding="utf-8"
            )
            evidence = root / "page-001.paddleocr-vl.json"
            write_json(
                evidence, {"blocks": [], "metadata": {"cache_signature": "current"}}
            )
            hit = ocr._load_cached_ocr(root, 1, "paddleocr-vl", "current")
            self.assertTrue(hit.metadata["artifact_cache_hit"])
            self.assertIsNone(ocr._load_cached_ocr(root, 1, "paddleocr-vl", "stale"))
            evidence.write_text("{broken", encoding="utf-8")
            self.assertIsNone(ocr._load_cached_ocr(root, 1, "paddleocr-vl", "current"))

    def test_cli_batch_closes_on_success_partial_failure_all_failure_and_interrupt(
        self,
    ):
        for failures in (set(), {"a"}, {"a", "b"}, {"interrupt"}):
            with (
                self.subTest(failures=failures),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                inputs, output = (root / "inputs", root / "outputs")
                inputs.mkdir()
                for name in ("a", "b"):
                    (inputs / f"{name}.pdf").write_bytes(b"transport fixture")
                extractor = Mock()

                def run(source, destination, scope):
                    if "interrupt" in failures:
                        raise KeyboardInterrupt("test interruption")
                    if source.stem in failures:
                        raise RuntimeError("controlled document failure")
                    destination.mkdir(parents=True)
                    (destination / "completed.fixture").write_text("kept")
                    return {"page_count": 1, "pending_human_review": 0}

                extractor.run.side_effect = run
                argv = [
                    "history-extract",
                    "extract-batch",
                    str(inputs),
                    "--output-root",
                    str(output),
                ]
                with (
                    patch("sys.argv", argv),
                    patch.object(cli.Settings, "load"),
                    patch.object(cli, "DocumentExtractor", return_value=extractor),
                    redirect_stdout(StringIO()),
                ):
                    if "interrupt" in failures:
                        with self.assertRaises(KeyboardInterrupt):
                            cli.main()
                    else:
                        self.assertEqual(int(bool(failures)), cli.main())
                        summary = json.loads(
                            (output / "batch-summary.json").read_text(encoding="utf-8")
                        )
                        self.assertEqual(
                            len(failures), summary["failed_document_count"]
                        )
                        self.assertEqual(2 - len(failures), summary["document_count"])
                        self.assertEqual(2, extractor.run.call_count)
                        for item in summary["completed"]:
                            self.assertTrue(
                                (Path(item["output"]) / "completed.fixture").exists()
                            )
                extractor.close.assert_called_once()
