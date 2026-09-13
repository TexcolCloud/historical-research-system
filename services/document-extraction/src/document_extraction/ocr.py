"""Paddle recognizer and source/model-bound OCR artifact cache."""

from __future__ import annotations
import gc
import copy
import hashlib
import json
import math
import os
import site
import sys
from functools import lru_cache
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any
from .accelerator import AcceleratorProfile, cpu_profile
from .models import OcrResult
from .utils import sha256

_DLL_HANDLES: list[Any] = []


def paddle_result_snapshot(result):
    """Keep formatted JSON alongside raw block text for reprocessing and precise matching."""
    data = copy.deepcopy(result.json.get('res', {}))
    original = result.get('parsing_res_list', []) if callable(getattr(result, 'get', None)) else []
    for index, block in enumerate(data.get('parsing_res_list', [])):
        block['formatted_block_content'] = block.get('block_content', '')
        if index < len(original) and hasattr(original[index], 'content'):
            block['block_content'] = original[index].content
    return data


def paddle_layout_evidence(results, image):
    """Retain detector scores separately from recognition; never infer text ownership."""
    frames = []
    for result_index, data in enumerate(results):
        settings = data.get('model_settings') or {}
        # Disabled detectors can emit synthetic whole-page boxes with score=1.
        if settings.get('use_layout_detection') is not True:
            continue
        layouts = data.get('layout_det_res') or []
        layouts = layouts if isinstance(layouts, list) else [layouts]
        for frame_index, layout in enumerate(layouts):
            detections = []
            for box in layout.get('boxes', []) if isinstance(layout, dict) else []:
                score, bbox = box.get('score'), box.get('coordinate')
                valid_score = type(score) in (int, float) and math.isfinite(score) and 0 <= score <= 1
                valid_box = (isinstance(bbox, (list, tuple)) and len(bbox) == 4
                    and all(type(v) in (int, float) and math.isfinite(v) for v in bbox)
                    and 0 <= bbox[0] < bbox[2] and 0 <= bbox[1] < bbox[3])
                detections.append({'label':box.get('label'), 'bbox':list(bbox) if valid_box else None,
                    'confidence':score if valid_score else None})
            frames.append({'result_index':result_index, 'frame_index':frame_index,
                'width':data.get('width'), 'height':data.get('height'),
                'coordinate_basis':'provider-layout-frame-pixels',
                'original_image_coordinates_confirmed':False,
                'document_preprocessor_enabled':settings.get('use_doc_preprocessor'),
                'detections':detections})
    boxes = [b for frame in frames for b in frame['detections']]
    scores = [b['confidence'] for b in boxes if b['confidence'] is not None]
    return {'schema_version':1, 'provider':'paddleocr-vl-layout-detector',
        'confidence_kind':'layout-detection', 'confidence_scope':'region',
        'source_image_sha256':sha256(image), 'frames':frames,
        'status':'available' if boxes and len(scores) == len(boxes) and all(b['bbox'] for b in boxes)
            else 'partial' if scores else 'unavailable',
        'minimum_detection_confidence':min(scores) if scores else None,
        'detected_regions':len(boxes), 'scored_regions':len(scores)}


def _register_nvidia_dlls() -> None:
    if sys.platform != "win32" or _DLL_HANDLES:
        return
    bins = [
        path
        for root in site.getsitepackages()
        for path in (Path(root) / "nvidia").glob("*/bin")
    ]
    for path in bins:
        _DLL_HANDLES.append(os.add_dll_directory(str(path)))
    if bins:
        os.environ["PATH"] = (
            os.pathsep.join(map(str, bins)) + os.pathsep + os.environ["PATH"]
        )


def close_backend(backend) -> None:
    worker = getattr(backend, "pipeline", None)
    if worker is not None:
        backend.pipeline = None
        if callable(getattr(worker, "close", None)):
            worker.close()


def _is_cuda_oom(exc: RuntimeError) -> bool:
    return "out of memory" in str(exc).lower() and "cuda" in str(exc).lower()


def _release_cuda() -> None:
    gc.collect()
    try:
        import torch

        torch.cuda.empty_cache()
    except (ImportError, RuntimeError):
        pass


class PaddleBackend:
    name = "paddleocr-vl"

    def __init__(
        self,
        pipeline_version: str,
        engine: str,
        profile: AcceleratorProfile,
        cpu_fallback: bool,
    ) -> None:
        try:
            from paddleocr import PaddleOCRVL
        except ImportError as exc:
            raise RuntimeError("PaddleOCR-VL未安装：pip install -e .[paddle]") from exc
        self.pipeline_version = pipeline_version
        self.engine = engine
        self.profile = profile
        self.cpu_fallback = cpu_fallback
        try:
            self.pipeline = self._build(PaddleOCRVL)
        except RuntimeError as exc:
            if not (_is_cuda_oom(exc) and cpu_fallback):
                raise
            self.profile = cpu_profile(profile)
            _release_cuda()
            self.pipeline = self._build(PaddleOCRVL)

    def _build(self, pipeline_class=None):
        if pipeline_class is None:
            from paddleocr import PaddleOCRVL as pipeline_class
        device_id = (
            int(self.profile.torch_device.rsplit(":", 1)[1])
            if self.profile.torch_device.startswith("cuda:")
            else None
        )
        engine_config = {
            "device_type": "gpu" if device_id is not None else "cpu",
            "dtype": self.profile.dtype,
        }
        if device_id is not None:
            engine_config["device_id"] = device_id
        if self.profile.attention:
            engine_config["attn_implementation"] = self.profile.attention
        return pipeline_class(
            pipeline_version=self.pipeline_version,
            engine=self.engine,
            device=self.profile.device,
            engine_config=engine_config,
            # Historical notes and marginal text contain source claims.
            markdown_ignore_labels=[],
            use_ocr_for_image_block=True,
            format_block_content=True,
        )

    def restructure_document(self, pages, output):
        from .paddle_restructure import restructure_document
        return restructure_document(pages, output, self.pipeline.restructure_pages)

    def extract(self, image: Path, work_dir: Path) -> OcrResult:
        output_dir = work_dir / self.name
        output_dir.mkdir(parents=True, exist_ok=True)
        results_data = []
        try:
            results = self.pipeline.predict(str(image))
            for result in results:
                result.save_to_markdown(save_path=output_dir)
                results_data.append(paddle_result_snapshot(result))
        except RuntimeError as exc:
            if not (_is_cuda_oom(exc) and self.cpu_fallback):
                raise
            self.profile = cpu_profile(self.profile)
            self.pipeline.close()
            del self.pipeline
            _release_cuda()
            self.pipeline = self._build()
            for result in self.pipeline.predict(str(image)):
                result.save_to_markdown(save_path=output_dir)
                results_data.append(paddle_result_snapshot(result))
        generated = sorted(
            output_dir.glob("**/*.md"), key=lambda path: path.stat().st_mtime_ns
        )
        created = generated[-max(1, len(results_data)) :]
        if not created:
            raise RuntimeError("PaddleOCR-VL没有生成Markdown")
        blocks = []
        for data in results_data:
            for block in data.get("parsing_res_list", []):
                blocks.append(
                    {
                        "text": block.get("block_content", ""),
                        "bbox": block.get("block_bbox"),
                        "label": block.get("block_label", "text"),
                        "order": block.get("block_order"),
                    }
                )
        return OcrResult(
            "\n\n".join(path.read_text(encoding="utf-8") for path in created),
            blocks,
            metadata={
                "width": results_data[0].get("width") if results_data else None,
                "height": results_data[0].get("height") if results_data else None,
                "markdown_path": str(created[0].resolve()),
                "layout_evidence": paddle_layout_evidence(results_data, image),
                "paddle_results": results_data,
            },
        )


def _load_cached_ocr(
    ocr_dir: Path,
    page_number: int,
    backend_name: str,
    expected_signature: str | None = None,
) -> OcrResult | None:
    stem = f"page-{page_number:03d}.{backend_name}"
    text_path, evidence_path = ocr_dir / f"{stem}.md", ocr_dir / f"{stem}.json"
    try:
        text = text_path.read_text(encoding="utf-8").strip()
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        blocks = evidence["blocks"]
        if not text or not isinstance(blocks, list):
            return None
        metadata = dict(evidence.get("metadata") or {})
        if expected_signature and metadata.get("cache_signature") != expected_signature:
            return None
        metadata["artifact_cache_hit"] = True
        return OcrResult(text, blocks, evidence.get("confidence"), metadata)
    except (OSError, UnicodeError, KeyError, TypeError, json.JSONDecodeError):
        return None


@lru_cache(maxsize=1)
def _ocr_runtime_signature_context() -> tuple[dict[str, str | None], str]:
    packages = {}
    for name in ("docling", "paddleocr", "paddlex", "torch", "transformers"):
        try:
            packages[name] = importlib_metadata.version(name)
        except importlib_metadata.PackageNotFoundError:
            packages[name] = None
    return packages, hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _ocr_cache_signature(backend, image: Path, image_sha256: str | None = None) -> str:
    packages, backend_code_sha256 = _ocr_runtime_signature_context()
    configuration = {
        "schema": 2,
        "class": type(backend).__name__,
        "name": backend.name,
        "pipeline_version": getattr(backend, "pipeline_version", None),
        "engine": getattr(backend, "engine", None),
        "command": getattr(backend, "command", None),
        "timeout": getattr(backend, "timeout", None),
        "text": getattr(backend, "text", None),
        "backend_code_sha256": backend_code_sha256,
        "runtime_packages": packages,
        "model_manifest_sha256": getattr(backend, "model_manifest_sha256", None),
        "image_sha256": image_sha256 or hashlib.sha256(image.read_bytes()).hexdigest(),
    }
    return hashlib.sha256(
        json.dumps(configuration, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def create(
    config: dict[str, Any], profile: AcceleratorProfile, cpu_fallback: bool
) -> PaddleBackend:
    if config.get("kind") != "paddleocr-vl":
        raise ValueError(f"Unsupported recognizer: {config.get('kind')}")
    return PaddleBackend(
        config.get("pipeline_version", "v1.6"),
        config.get("engine", "transformers"),
        profile,
        cpu_fallback,
    )
