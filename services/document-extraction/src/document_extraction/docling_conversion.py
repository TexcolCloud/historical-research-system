"""Docling owns file decoding, pagination, images and document serialization.

Recognition is selected once per converter. The local adapter only supplies a
page prediction to Docling's VLM pipeline; it does not split or parse PDFs.
"""

from dataclasses import asdict
from importlib.metadata import version
from pathlib import Path
import json
import time

from . import ocr
from .models import OcrResult
from .utils import display_text, sha256, write_json
from .native_pdf import export_matches, inspect_pdf
from .provenance import text_hash


class NativeExportMismatch(ValueError):
    def __init__(self, pages):
        self.pages = pages
        super().__init__(f"Native text changed during Markdown serialization on pages {pages}")


class DoclingConverter:
    def __init__(self, settings, accelerator, native_plan=None):
        if len(settings.ocr_backends) != 1:
            raise ValueError("Docling conversion requires one recognition backend")
        self.settings = settings
        self.accelerator = accelerator
        self.config = settings.ocr_backends[0]
        self.name = self.config["name"]
        self.recognizer = None
        self.output = None
        self.page_evidence = {}
        self.model_manifest_sha256 = None
        self.native_plan = native_plan
        try:
            self.converter = (None if getattr(settings, "native_pdf", False) and native_plan is None else self._build())
        except BaseException:
            ocr.close_backend(self.recognizer)
            raise

    def _build(self):
        ocr._register_nvidia_dlls()
        from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
        from docling.datamodel.base_models import (
            InputFormat,
            VlmPrediction,
            VlmStopReason,
        )
        from docling.datamodel.pipeline_options import (
            AcceleratorOptions,
            PdfPipelineOptions,
            VlmPipelineOptions,
            VlmConvertOptions,
        )
        from docling.datamodel.pipeline_options import RapidOcrOptions
        from docling.datamodel.pipeline_options_vlm_model import ResponseFormat
        from docling.datamodel.stage_model_specs import VlmModelSpec
        from docling.datamodel.vlm_engine_options import TransformersVlmEngineOptions
        from docling.document_converter import (
            DocumentConverter,
            PdfFormatOption,
            ImageFormatOption,
        )
        from docling.pipeline.standard_pdf_pipeline import StandardPdfPipeline
        from docling.pipeline.vlm_pipeline import VlmPipeline

        scale = self.settings.render_dpi / 72
        acceleration = AcceleratorOptions(
            device=self.accelerator.torch_device,
            num_threads=self.accelerator.cpu_threads,
            cuda_use_flash_attention2=False,
        )
        if self.config["kind"] == "docling":
            options = PdfPipelineOptions(
                generate_page_images=True,
                images_scale=scale,
                accelerator_options=acceleration,
            )
            if "rapidocr" in self.config:
                options.ocr_options = RapidOcrOptions(**self.config["rapidocr"])
            pipeline_class = StandardPdfPipeline
        else:
            if not self.native_plan or any(p['route'] != 'native' for p in self.native_plan.values()):
                self.recognizer = ocr.create(
                    self.config, self.accelerator, self.settings.acceleration.cpu_fallback
                )
            owner = self

            class RecognitionStage:
                def __call__(self, conversion, page_batch):
                    for page in page_batch:
                        image = owner.output / "pages" / f"page-{page.page_no:03d}.png"
                        image.parent.mkdir(parents=True, exist_ok=True)
                        page.get_image(scale=scale).save(image)
                        native = owner.native_plan.get(page.page_no, {}) if owner.native_plan else {}
                        text = (native['text'] if native.get('route') == 'native'
                                else owner._recognize(page.page_no, image).text)
                        page.predictions.vlm_response = VlmPrediction(
                            text=text,
                            generation_time=owner.page_evidence.get(page.page_no, {}).get("seconds", 0),
                            stop_reason=VlmStopReason.END_OF_SEQUENCE
                            if text.strip()
                            else VlmStopReason.UNSPECIFIED,
                        )
                        yield page

            class RecognitionPipeline(VlmPipeline):
                def _initialize_new_runtime_system(self, pipeline_options):
                    self.force_backend_text = False
                    self.keep_images = True
                    self.build_pipe = [RecognitionStage()]

                def _convert_text_page(
                    self, conversion, page, input_format, backend_class
                ):
                    from docling.datamodel.backend_options import MarkdownBackendOptions

                    native = (owner.native_plan or {}).get(page.page_no, {})
                    if native.get('route') == 'native' and native.get('layout_blocks'):
                        from .native_layout import to_docling
                        return to_docling(native, page.get_image(scale=scale), owner.settings.render_dpi)
                    raw = owner.page_evidence.get(page.page_no, {}).get("raw", {})
                    source_uri = raw.get("metadata", {}).get("markdown_path")

                    class AssetBackend(backend_class):
                        def __init__(self, *args, **kwargs):
                            kwargs["options"] = MarkdownBackendOptions(
                                fetch_images=bool(source_uri),
                                enable_local_fetch=bool(source_uri),
                                source_uri=Path(source_uri) if source_uri else None,
                            )
                            super().__init__(*args, **kwargs)

                    return super()._convert_text_page(
                        conversion, page, input_format, AssetBackend
                    )

            options = VlmPipelineOptions(
                generate_page_images=True,
                generate_picture_images=False,
                images_scale=scale,
                accelerator_options=acceleration,
                # The required engine descriptor is replaced by RecognitionStage;
                # Docling never downloads or starts a second recognition model.
                vlm_options=VlmConvertOptions(
                    engine_options=TransformersVlmEngineOptions(),
                    model_spec=VlmModelSpec(
                        name=self.name,
                        default_repo_id=self.name,
                        prompt="",
                        response_format=ResponseFormat.MARKDOWN,
                    ),
                ),
            )
            pipeline_class = RecognitionPipeline
        return DocumentConverter(
            allowed_formats=[InputFormat.PDF, InputFormat.IMAGE],
            format_options={
                InputFormat.PDF: PdfFormatOption(
                    pipeline_cls=pipeline_class,
                    pipeline_options=options,
                    backend=PyPdfiumDocumentBackend,
                ),
                InputFormat.IMAGE: ImageFormatOption(
                    pipeline_cls=pipeline_class, pipeline_options=options
                ),
            },
        )

    def _recognize(self, number, image):
        from .ocr import _load_cached_ocr, _ocr_cache_signature

        self.recognizer.model_manifest_sha256 = self.model_manifest_sha256
        signature = _ocr_cache_signature(self.recognizer, image, sha256(image))
        started = time.monotonic()
        result = _load_cached_ocr(self.output / "ocr", number, self.name, signature)
        hit, attempts, errors = result is not None, 0, []
        if not hit:
            result = OcrResult("", [])
            for _ in range(2):
                attempts += 1
                try:
                    result = self.recognizer.extract(
                        image, self.output / "ocr" / f"page-{number:03d}-work"
                    )
                    if result.text.strip():
                        break
                except Exception as exc:
                    errors.append(type(exc).__name__)
            result.metadata = dict(
                result.metadata or {},
                cache_signature=signature,
                attempts=attempts,
                errors=errors,
                artifact_cache_hit=False,
            )
            self._save_raw(number, result)
        self.page_evidence[number] = {
            "raw": asdict(result),
            "cache_hit": hit,
            "calls": attempts,
            "seconds": round(time.monotonic() - started, 3),
        }
        return result

    def _save_raw(self, number, result):
        folder = self.output / "ocr"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"page-{number:03d}.{self.name}.md").write_text(
            result.text, encoding="utf-8", newline="\n"
        )
        write_json(folder / f"page-{number:03d}.{self.name}.json", asdict(result))

    def convert_document(self, source, output):
        source, output = Path(source).resolve(), Path(output).resolve()
        output.mkdir(parents=True, exist_ok=True)
        self.output = output
        self.page_evidence = {}
        if self.converter is None:
            self.native_plan = inspect_pdf(source, enabled=getattr(self.settings, "native_pdf", False))
            self.converter = self._build()
        started = time.monotonic()
        report = {
            "framework": "docling",
            "framework_version": version("docling"),
            "pdf_backend": "pypdfium2",
            "recognizer": self.name,
            "source": str(source),
            "source_sha256": sha256(source),
            "status": "started",
        }
        write_json(output / "conversion.json", report)
        try:
            return self._convert(source, output, report, started)
        except Exception as exc:
            report.update(
                status="failed",
                failure=f"{type(exc).__name__}: {exc}",
                conversion_seconds=round(time.monotonic() - started, 3),
            )
            write_json(output / "conversion.json", report)
            raise

    def _convert(self, source, output, report, started):
        from docling_core.types.doc import DoclingDocument, ImageRefMode

        converted = self.converter.convert(source, raises_on_error=False)
        report.update(
            status=converted.status.value,
            errors=[e.model_dump(mode="json") for e in converted.errors],
            expected_page_count=converted.input.page_count,
        )
        document = converted.document
        document.save_as_json(
            output / "docling-document.json",
            artifacts_dir=output / "docling-assets",
            image_mode=ImageRefMode.REFERENCED,
        )
        referenced = DoclingDocument.load_from_json(output / "docling-document.json")
        (output / "docling-document.md").write_text(
            export_markdown(referenced, asset_root=output), encoding="utf-8"
        )
        pages, mismatches = [], []
        for number, item in sorted(document.pages.items()):
            if item.image is None:
                raise RuntimeError(
                    f"Docling did not preserve source image for page {number}"
                )
            image = output / "pages" / f"page-{number:03d}.png"
            image.parent.mkdir(parents=True, exist_ok=True)
            item.image.pil_image.save(image)
            text = display_text(export_markdown(referenced, page_no=number, asset_root=output))
            evidence = self.page_evidence.get(number)
            native = (self.native_plan or {}).get(number, {})
            if native.get('route') == 'native':
                if not export_matches(text, native):
                    mismatches.append(number)
                pages.append({
                    'page': number, 'image_path': image, 'text': text, 'ocr': {},
                    'extraction_method': 'native_pdf',
                    'native_evidence': {**native, 'exported_text_sha256': text_hash(text)},
                    'conversion_evidence': {'document': 'docling-document.json', 'page': number,
                        'source_sha256': report['source_sha256'], 'image_sha256': sha256(image)},
                })
                continue
            if evidence is None:
                result = OcrResult(
                    text, [], metadata={"framework": "docling", "source_page": number}
                )
                self._save_raw(number, result)
                evidence = {
                    "raw": asdict(result),
                    "cache_hit": False,
                    "calls": 1,
                    "seconds": None,
                }
            pages.append(
                {
                    "page": number,
                    "extraction_method": "ocr",
                    "native_probe": native,
                    "image_path": image,
                    "text": text,
                    "ocr": {self.name: evidence["raw"]["text"]},
                    "ocr_evidence": evidence["raw"],
                    "conversion_evidence": {
                        "document": "docling-document.json",
                        "page": number,
                        "image_sha256": sha256(image),
                        "localization": "page; item boxes retained in Docling JSON when available",
                    },
                }
            )
        expected = set(range(1, converted.input.page_count + 1))
        report.update(
            missing_pages=sorted(expected - {p["page"] for p in pages}),
            page_count=len(pages),
        )
        if not pages or report["missing_pages"]:
            raise RuntimeError(
                "Docling omitted pages; retained conversion artifacts are incomplete"
            )
        if mismatches:
            raise NativeExportMismatch(mismatches)
        restructure = getattr(self.recognizer, 'restructure_document', None)
        if callable(restructure):
            # Provider group identities are local to each uninterrupted OCR run.
            runs = []
            for page in pages:
                if page.get('extraction_method') == 'native_pdf':
                    continue
                if not runs or runs[-1][-1]['page'] + 1 != page['page']:
                    runs.append([])
                runs[-1].append(page)
            summaries = []
            for run in runs:
                folder = output if len(run) == len(pages) else output / f"ocr-run-{run[0]['page']:03d}"
                folder.mkdir(parents=True, exist_ok=True)
                summaries.append(restructure(run, folder))
                if folder != output:
                    for page in run:
                        if page.get('restructure_evidence'):
                            page['restructure_evidence']['artifact'] = folder.name + '/paddle-restructure.json'
            report['restructure'] = summaries[0] if len(runs) == 1 and len(runs[0]) == len(pages) else {'runs': summaries}
            if runs and any(len(run) != len(pages) for run in runs):
                artifacts = [json.loads((output / f"ocr-run-{run[0]['page']:03d}" / 'paddle-restructure.json').read_text('utf-8'))
                             for run in runs]
                write_json(output / 'paddle-restructure.json', {
                    'policy': 'paddle-document-restructure-v1', 'status': 'completed',
                    'scope': 'Independent contiguous OCR runs; native pages never cross-merged',
                    'tables': [table for artifact in artifacts for table in artifact['tables']],
                    'titles': [title for artifact in artifacts for title in artifact['titles']],
                    'runs': summaries,
                })
        report.update(
            conversion_seconds=round(time.monotonic() - started, 3),
            ocr_cache_hits=sum(e["cache_hit"] for e in self.page_evidence.values()),
            ocr_calls=sum(e["calls"] for e in self.page_evidence.values())
            if self.recognizer
            else sum(p.get('extraction_method') != 'native_pdf' for p in pages),
            native_pages=sum(p.get('extraction_method') == 'native_pdf' for p in pages),
            ocr_failures=sum(not p["text"].strip() for p in pages),
            docling_document_sha256=sha256(output / "docling-document.json"),
        )
        write_json(output / "conversion.json", report)
        return pages, report

    def close(self):
        ocr.close_backend(self.recognizer)
        self.converter = None


def export_markdown(document, page_no=None, asset_root=None):
    """Use Docling's HTML table serializer to preserve merged-cell ownership."""
    from docling_core.transforms.serializer.html import HTMLTableSerializer
    from docling_core.transforms.serializer.markdown import (
        MarkdownDocSerializer,
        MarkdownParams,
    )
    from docling_core.types.doc import ImageRefMode

    text = (
        MarkdownDocSerializer(
            doc=document,
            table_serializer=HTMLTableSerializer(),
            params=MarkdownParams(
                pages={page_no} if page_no is not None else None,
                image_mode=ImageRefMode.REFERENCED,
                traverse_pictures=True,
            ),
        )
        .serialize()
        .text
    )

    if asset_root is not None:
        from urllib.parse import quote
        for picture in document.pictures:
            if picture.image is not None and Path(str(picture.image.uri)).is_absolute():
                uri = picture.image.uri
                relative = Path(str(uri)).resolve().relative_to(asset_root.resolve()).as_posix()
                text = text.replace(f']({uri!s})', f']({quote(relative, safe="/")})')
    return text
