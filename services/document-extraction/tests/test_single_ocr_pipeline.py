"""Public single-OCR flow: recoverable pages and exact downstream evidence."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from document_extraction import artifacts, ocr, pipeline
from document_extraction.docling_conversion import DoclingConverter, export_markdown
from document_extraction.content_readiness import verify_quote
from document_extraction.models import OcrResult
from document_extraction.semantic_completion import complete_document
from test_single_ocr_completion import verdict


class SinglePipelineTests(unittest.TestCase):
    def test_model_flagged_tables_require_only_local_human_review(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            Image.new("RGB", (40, 40), "white").save(source)
            for label, table in [
                ("html", "<table><tr><td>一厂</td><td>180人</td></tr></table>"),
                (
                    "unlocated-table",
                    "<table><tr><td>二厂</td><td>200人</td></tr></table>",
                ),
                ("chart", "总务科 会计股 工务科 弹药股"),
            ]:
                with self.subTest(label=label):
                    folder = root / label
                    folder.mkdir()
                    image = folder / "page.png"
                    image.write_bytes(source.read_bytes())
                    text = (
                        "本文说明各厂独立生产。\n\n"
                        + table
                        + "\n\n① 本段注释记载1938年开始生产。"
                    )
                    concerns = (
                        []
                        if label == "html"
                        else [
                            {
                                "kind": "table",
                                "excerpt": table,
                                "explanation": "组织结构图交人工核对连线归属。",
                            }
                        ]
                    )
                    if label == "unlocated-table":
                        concerns[0]["excerpt"] = ""
                    changes = (
                        []
                        if label != "html"
                        else [
                            {
                                "kind": "claim",
                                "before": "180人",
                                "after": "1800人",
                                "source_reading": "1800人",
                                "location": "表格",
                                "explanation": "模型建议修改表中数量",
                            }
                        ]
                    )
                    completion = complete_document(
                        [{"page": 1, "image_path": image, "text": text}],
                        None,
                        folder,
                        reviewer=lambda *_: verdict(concerns=concerns, changes=changes),
                    )
                    self.assertEqual(completion["pages"][0]["text"], text)
                    self.assertEqual(completion["pages"][0]["changes"], [])
                    manifest = artifacts.write_outputs(
                        source, folder, completion, ["single"], {}
                    )
                    self.assertEqual(manifest["pending_human_review"], 1)
                    self.assertEqual(
                        verify_quote(folder, "本文说明各厂独立生产。")["status"],
                        "evidence-linked",
                    )
                    self.assertEqual(
                        verify_quote(folder, "① 本段注释记载1938年开始生产。")[
                            "status"
                        ],
                        "evidence-linked",
                    )
                    self.assertEqual(
                        verify_quote(folder, table)["status"], "needs-evidence"
                    )
                    cards = json.loads(
                        (folder / "review-cards.json").read_text("utf-8")
                    )["cards"]
                    self.assertEqual(len(cards), 1)
                    self.assertEqual(cards[0]["reviewer_required"], "human")
                    self.assertEqual(cards[0]["region"]["kind"], "table")
                    self.assertEqual(cards[0]["region"]["text"], table)
                    self.assertTrue((folder / cards[0]["page_image"]).is_file())

    def test_paddle_keeps_notes_and_reads_text_inside_figures(self):
        from document_extraction.ocr import PaddleBackend

        backend = PaddleBackend.__new__(PaddleBackend)
        backend.pipeline_version = "v1.6"
        backend.engine = "transformers"
        backend.profile = SimpleNamespace(
            torch_device="cpu", device="cpu", dtype="float32", attention=None
        )
        factory = Mock()
        backend._build(factory)
        options = factory.call_args.kwargs
        self.assertEqual(options["markdown_ignore_labels"], [])
        self.assertTrue(options["use_ocr_for_image_block"])
        self.assertTrue(options['format_block_content'])

    def test_docling_preserves_merged_table_cells_in_final_markdown(self):
        from docling_core.types.doc import DoclingDocument, TableData, TableCell

        doc = DoclingDocument(name="table")
        doc.add_table(
            data=TableData(
                num_rows=2,
                num_cols=2,
                table_cells=[
                    TableCell(
                        text="合计180人",
                        row_span=2,
                        start_row_offset_idx=0,
                        end_row_offset_idx=2,
                        start_col_offset_idx=0,
                        end_col_offset_idx=1,
                    ),
                    TableCell(
                        text="一厂",
                        start_row_offset_idx=0,
                        end_row_offset_idx=1,
                        start_col_offset_idx=1,
                        end_col_offset_idx=2,
                    ),
                    TableCell(
                        text="二厂",
                        start_row_offset_idx=1,
                        end_row_offset_idx=2,
                        start_col_offset_idx=1,
                        end_col_offset_idx=2,
                    ),
                ],
            )
        )
        text = export_markdown(doc)
        self.assertIn('rowspan="2"', text)
        self.assertEqual(text.count("合计180人"), 1)
        self.assertIn("二厂", text)

    def test_markdown_picture_uses_saved_asset_not_zero_area_vlm_crop(self):
        from docling_core.types.doc import DoclingDocument

        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            Image.new("RGB", (100, 100), "white").save(source)
            draft = root / "draft"
            draft.mkdir()
            Image.new("RGB", (15, 25), "black").save(draft / "portrait.png")
            (draft / "page.md").write_text(
                "![人物](portrait.png)\n\n① 注释中的四营归属。", encoding="utf-8"
            )
            settings = SimpleNamespace(
                render_dpi=150,
                ocr_backends=[{"name": "single", "kind": "fixture"}],
                acceleration=SimpleNamespace(cpu_fallback=True),
            )
            accelerator = SimpleNamespace(torch_device="cpu", cpu_threads=1)
            backend = SimpleNamespace(
                name="single",
                extract=Mock(
                    return_value=OcrResult(
                        (draft / "page.md").read_text("utf-8"),
                        [],
                        metadata={"markdown_path": str(draft / "page.md")},
                    )
                ),
            )
            with patch.object(ocr, "create", return_value=backend):
                converter = DoclingConverter(settings, accelerator)
                try:
                    pages, _ = converter.convert_document(source, root / "out")
                finally:
                    converter.close()
            doc = DoclingDocument.load_from_json(root / "out/docling-document.json")
            self.assertEqual(len(doc.pictures), 1)
            picture = doc.pictures[0].image
            self.assertEqual((picture.size.width, picture.size.height), (15, 25))
            self.assertTrue((root / "out" / str(picture.uri)).is_file())
            self.assertIn("四营归属", pages[0]["text"])
            self.assertIn("docling-assets", pages[0]["text"])

    def test_docling_can_select_full_page_rapidocr_without_native_text_shortcut(self):
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import OcrMode, RapidOcrOptions

        settings = SimpleNamespace(
            render_dpi=300,
            ocr_backends=[
                {
                    "name": "docling",
                    "kind": "docling",
                    "rapidocr": {
                        "mode": "full_page",
                        "backend": "torch",
                        "scale": 300 / 72,
                    },
                }
            ],
        )
        converter = DoclingConverter(
            settings, SimpleNamespace(torch_device="cpu", cpu_threads=1)
        )
        try:
            options = converter.converter.format_to_options[
                InputFormat.PDF
            ].pipeline_options.ocr_options
            self.assertIsInstance(options, RapidOcrOptions)
            self.assertEqual(options.mode, OcrMode.FULL_PAGE)
            self.assertEqual(options.scale, 300 / 72)
            self.assertEqual(options.backend, "torch")
        finally:
            converter.close()

    def test_public_entrypoint_uses_one_backend_and_keeps_verifiable_package(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            Image.new("RGB", (40, 40), "white").save(source)
            settings = SimpleNamespace(
                render_dpi=150,
                vision_review=None,
                risk=SimpleNamespace(enabled=True, auto_accept=True),
                model_root=root,
                acceleration=SimpleNamespace(device="cpu", cpu_fallback=True),
                ocr_backends=[{"name": "single", "kind": "fixture"}],
            )
            backend = SimpleNamespace(
                name="single",
                restructure_document=Mock(return_value={'status':'completed'}),
                extract=Mock(
                    return_value=OcrResult(
                        "# 研究甲\n\n本文否定二年说，确认为元年。\n\n[1] 史料甲。", []
                    )
                ),
            )

            def review(packet, images, settings):
                backend.restructure_document.assert_called_once()
                result = verdict()
                result["structure"].update(
                    starts_article=True, title="研究甲", author="作者甲"
                )
                return result

            with (
                patch.object(ocr, "create", return_value=backend) as create,
                patch.object(
                    pipeline,
                    "detect",
                    return_value=SimpleNamespace(
                        report=lambda: {}, torch_device="cpu", cpu_threads=1
                    ),
                ),
                patch(
                    "document_extraction.semantic_completion.review_page",
                    side_effect=review,
                ),
            ):
                manifest = pipeline.run(source, root / "out", settings)
            create.assert_called_once()
            self.assertEqual(backend.extract.call_count, 1)
            self.assertEqual(manifest["ocr_backends"], ["single"])
            self.assertIsNone(manifest["pages"][0]["ocr_similarity"])
            self.assertEqual(manifest["document_status"], "release-accepted")
            from docling_core.types.doc import DoclingDocument

            document = DoclingDocument.load_from_json(
                root / "out/docling-document.json"
            )
            self.assertEqual(len(document.pages), 1)
            self.assertIn("本文否定二年说", document.export_to_markdown())
            self.assertEqual(
                manifest["runtime"]["pipeline"], "docling-single-recognizer"
            )
            self.assertEqual(
                verify_quote(root / "out", "本文否定二年说，确认为元年。")["status"],
                "evidence-linked",
            )
            review_path = root / "out/reviews/page-001.json"
            page = json.loads(review_path.read_text("utf-8"))
            page["candidate_text"] = "遭到改写"
            review_path.write_text(json.dumps(page), encoding="utf-8")
            self.assertEqual(
                verify_quote(root / "out", "本文否定二年说，确认为元年。")["status"],
                "needs-evidence",
            )

    def test_failed_middle_page_does_not_abort_later_ocr_or_release_missing_text(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.pdf"
            pages = [Image.new("RGB", (40, 40), "white") for _ in range(3)]
            pages[0].save(source, save_all=True, append_images=pages[1:])
            images = []
            for n in range(1, 4):
                p = root / f"out/pages/page-{n:03d}.png"
                p.parent.mkdir(parents=True, exist_ok=True)
                images.append(p)

            def extract(image, work):
                if image == images[1]:
                    raise RuntimeError("controlled OCR failure")
                return OcrResult(f"第{images.index(image) + 1}页正文。", [])

            backend = SimpleNamespace(name="single", extract=Mock(side_effect=extract))
            settings = SimpleNamespace(
                render_dpi=150,
                vision_review=None,
                ocr_backends=[{"name": "single", "kind": "fixture"}],
                acceleration=SimpleNamespace(cpu_fallback=True),
                risk=SimpleNamespace(enabled=True, auto_accept=True),
            )
            accelerator = SimpleNamespace(
                report=lambda: {}, torch_device="cpu", cpu_threads=1
            )
            with patch.object(ocr, "create", return_value=backend):
                converter = DoclingConverter(settings, accelerator)
            try:
                with patch.object(
                    converter.converter, "convert", wraps=converter.converter.convert
                ) as convert:
                    result = pipeline.run_document(
                        source,
                        root / "out",
                        settings,
                        converter,
                        accelerator,
                        reviewer=lambda *args: {
                            "review_state": "unavailable",
                            "reason": "offline",
                        },
                    )
                convert.assert_called_once_with(source, raises_on_error=False)
            finally:
                converter.close()
            self.assertEqual(backend.extract.call_count, 4)
            self.assertEqual(result["page_count"], 3)
            self.assertEqual(result["pending_human_review"], 3)
            self.assertEqual(result["runtime"]["timings"]["ocr_failures"], 1)
            self.assertIn(
                "第3页正文。", (root / "out/document.candidate.md").read_text("utf-8")
            )
            cards = json.loads((root / "out/review-cards.json").read_text("utf-8"))[
                "cards"
            ]
            self.assertIn(2, [c["page"] for c in cards])

    def test_citation_issue_stays_local_and_continuation_ignores_footer(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            Image.new("RGB", (40, 40), "white").save(source)
            pages = []
            for n, text in enumerate(
                [
                    "前一段正文末尾尚未结束\n\n收稿日期待核实",
                    "随后一段续文接上前句\n\n另一个独立段落。",
                ],
                1,
            ):
                image = root / f"page-{n}.png"
                image.write_bytes(source.read_bytes())
                pages.append({"page": n, "image_path": image, "text": text})

            def review(packet, *_):
                n = packet["target"]["page"]
                v = verdict()
                if n == 1:
                    v["structure"].update(
                        starts_article=True, title="标题", ends_article=False
                    )
                    v["concerns"] = [
                        {
                            "kind": "citation",
                            "excerpt": "收稿日期待核实",
                            "explanation": "著录不清",
                        }
                    ]
                else:
                    v["structure"].update(
                        continues_previous=True,
                        continuation_before="前一段正文末尾尚未结束",
                        continuation_after="随后一段续文接上前句",
                    )
                return v

            completion = complete_document(pages, None, root, reviewer=review)
            manifest = artifacts.write_outputs(source, root, completion, ["single"], {})
            blocks = json.loads((root / "content-readiness.json").read_text("utf-8"))[
                "blocks"
            ]
            self.assertEqual(
                [b["status"] for b in blocks],
                ["usable", "usable-with-warning", "usable", "usable"],
            )
            self.assertIn("bibliographic_reference", blocks[1]["restricted_fields"])
            self.assertEqual(blocks[0]["dependencies"], [blocks[2]["block_id"]])
            self.assertEqual(blocks[1]["dependencies"], [])
            self.assertEqual(manifest["pending_human_review"], 0)
            self.assertEqual(
                verify_quote(root, "另一个独立段落。")["status"], "evidence-linked"
            )

    def test_invalid_source_records_failure_and_removes_stale_manifest(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "broken.pdf"
            source.write_bytes(b"broken PDF")
            output = root / "out"
            output.mkdir()
            (output / "manifest.json").write_text("{}")
            settings = SimpleNamespace(
                render_dpi=150,
                vision_review=None,
                ocr_backends=[{"name": "single", "kind": "fixture"}],
                acceleration=SimpleNamespace(cpu_fallback=True),
            )
            accelerator = SimpleNamespace(torch_device="cpu", cpu_threads=1)
            with patch.object(
                ocr, "create", return_value=SimpleNamespace(name="single")
            ):
                converter = DoclingConverter(settings, accelerator)
            try:
                with self.assertRaises(Exception):
                    pipeline.run_document(
                        source, output, settings, converter, accelerator
                    )
            finally:
                converter.close()
            self.assertFalse((output / "manifest.json").exists())
            self.assertEqual(
                json.loads((output / "conversion.json").read_text("utf-8"))["status"],
                "failed",
            )


if __name__ == "__main__":
    unittest.main()
