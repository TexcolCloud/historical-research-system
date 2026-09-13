"""Own one Docling converter and run conversion, semantic review and export."""

from pathlib import Path
import time

from .accelerator import detect
from .artifacts import write_outputs
from .docling_conversion import DoclingConverter
from .semantic_completion import complete_document
from .settings import Settings
from .utils import sha256
from hrs_runtime.local_vision import MODEL as LOCAL_MODEL, gpu_lease, prepare_ocr


class ScheduledConverter:
    """Load OCR only inside its GPU lease; release all OCR tensors before review."""
    def __init__(self, settings, accelerator):
        self.settings, self.accelerator = settings, accelerator
        self.name = settings.ocr_backends[0]['name']
        self.model_manifest_sha256 = None

    def convert_document(self, source, output):
        from .ocr import _release_cuda
        with gpu_lease():
            prepare_ocr()
            backend = None
            try:
                backend = DoclingConverter(self.settings, self.accelerator)
                backend.model_manifest_sha256 = self.model_manifest_sha256
                return backend.convert_document(source, output)
            finally:
                try:
                    if backend is not None:
                        backend.close()
                finally:
                    backend = None
                    _release_cuda()

    def close(self):
        pass


class DocumentExtractor:
    """Reuse loaded models across a batch; release them through close()."""

    def __init__(self, settings: Settings):
        if len(settings.ocr_backends) != 1:
            raise ValueError(
                "Exactly one OCR backend is required; the dual-OCR pipeline is retired"
            )
        self.settings = settings
        self.accelerator = detect(settings.acceleration)
        self.converter = None
        try:
            local = getattr(getattr(settings, 'vision_review', None), 'model', None) == LOCAL_MODEL
            self.converter = (ScheduledConverter if local else DoclingConverter)(settings, self.accelerator)
            manifest = settings.model_root / "manifest.sha256"
            self.converter.model_manifest_sha256 = (
                sha256(manifest) if manifest.is_file() else None
            )
        except BaseException:
            self.close()
            raise

    def run(self, source: Path, output: Path, memory_scope: str | None = None) -> dict:
        """memory_scope is accepted for CLI compatibility; correction memory is retired."""
        if self.converter is None:
            raise RuntimeError("DocumentExtractor is closed")
        return run_document(
            source, output, self.settings, self.converter, self.accelerator
        )

    def close(self) -> None:
        converter, self.converter = self.converter, None
        if converter is not None:
            converter.close()


def run(
    source: Path, output: Path, settings: Settings, memory_scope: str | None = None
) -> dict:
    extractor = DocumentExtractor(settings)
    try:
        return extractor.run(source, output, memory_scope)
    finally:
        extractor.close()


def run_document(source, output, settings, backend, accelerator, *, reviewer=None):
    source, output = Path(source).resolve(), Path(output).resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    output.mkdir(parents=True, exist_ok=True)
    # A failed rerun must not expose a stale success manifest from an earlier run.
    (output / "manifest.json").unlink(missing_ok=True)
    started = time.monotonic()
    pages, timings = backend.convert_document(source, output)
    review_start = time.monotonic()
    completion = complete_document(
        pages, settings.vision_review, output, reviewer=reviewer
    )
    timings["semantic_seconds"] = round(time.monotonic() - review_start, 3)
    timings["total_seconds"] = round(time.monotonic() - started, 3)
    timings["semantic_tasks"] = completion["model_calls"]
    return write_outputs(
        source,
        output,
        completion,
        [backend.name],
        {
            "pipeline": "docling-single-recognizer",
            "accelerator": accelerator.report(),
            "timings": timings,
        },
        auto_accept=settings.risk.enabled and settings.risk.auto_accept,
    )
