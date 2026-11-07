"""SourceImport: retained originals and source-bound inspection."""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from filelock import FileLock

from hrs.processing.hashing import digest, file_digest
from hrs.processing.workflow_rules import (
    new_task,
)


class SourceImport:
    def __init__(self, context):
        self.context = context

    def register_manifest(self, manifest: dict, title: str = "") -> dict:
        return self.register(
            Path(manifest["source"]["path"]),
            manifest["pages"],
            title=title,
            expected_source_sha256=manifest["source"]["sha256"],
            original_name=manifest["source"].get("original_name"),
            media_type=manifest["source"].get("media_type"),
        )

    def register(
        self,
        source: Path,
        pages: list[dict],
        title: str = "",
        *,
        expected_source_sha256: str | None = None,
        original_name: str | None = None,
        media_type: str | None = None,
    ) -> dict:
        self.context.repository.require_writable()
        source = Path(source).resolve()
        source_hash = file_digest(source)
        if expected_source_sha256 is not None and source_hash != expected_source_sha256:
            raise ValueError("Source hash mismatch")
        document_id = source_hash[:24]
        if not pages or [p["page"] for p in pages] != list(range(1, len(pages) + 1)):
            raise ValueError("Pages must be a nonempty consecutive physical page sequence")
        with FileLock(self.context.repository.root / f".register-{document_id}.lock", timeout=30):
            directory = self.context.repository.directory(document_id)
            if self.context.repository.exists(document_id):
                with self.context.verified(document_id) as existing:
                    return existing
            directory.mkdir(parents=True, exist_ok=True)
            retained_source = directory / ("source" + source.suffix.lower())
            if source != retained_source.resolve():
                shutil.copyfile(source, retained_source)
            retained_pages = []
            for page in pages:
                image = Path(page["image"]).resolve()
                image_hash = file_digest(image)
                if page.get("image_sha256", page.get("sha256", image_hash)) != image_hash:
                    raise ValueError("Page image hash mismatch")
                target = directory / "pages" / f"{page['page']:05d}{image.suffix.lower()}"
                # Reuse retained renders from before page filenames were unified.
                legacy = directory / "pages" / f"{page['page']:06d}{image.suffix.lower()}"
                if image == legacy.resolve():
                    target = image
                target.parent.mkdir(parents=True, exist_ok=True)
                if image != target.resolve():
                    shutil.copyfile(image, target)
                retained_pages.append(
                    {
                        "page": page["page"],
                        "image": target.relative_to(directory).as_posix(),
                        "image_sha256": image_hash,
                        "state": "awaiting_ocr",
                    }
                )
                plan = page.get("extraction_plan")
                if plan is not None:
                    if plan.get("route") not in {"native", "mixed", "ocr", "blank_candidate"}:
                        raise ValueError("Unknown PDF extraction route")
                    native = plan.get("native_markdown", "")
                    if not isinstance(native, str) or plan.get(
                        "native_sha256", digest(native)
                    ) != digest(native):
                        raise ValueError("Native extraction text hash mismatch")
                    retained_pages[-1].update(extraction_plan=plan, route=plan["route"])
            doc = {
                "schema_version": 1,
                "id": document_id,
                "document_id": document_id,
                "title": title or Path(original_name or source.name).stem,
                "created_at": time.time(),
                "state": "processing",
                "source": {
                    "path": retained_source.name,
                    "sha256": source_hash,
                    "original_name": original_name or source.name,
                    "media_type": media_type,
                },
                "pages": retained_pages,
                "tasks": [new_task("ocr", p["page"]) for p in retained_pages],
                "organization": None,
                "publication": None,
            }
            for page in doc["pages"]:
                plan = page.get("extraction_plan", {})
                if plan.get("route") != "native" or not plan.get("native_markdown", "").strip():
                    continue
                task = next(t for t in doc["tasks"] if t["page"] == page["page"])
                task["stage"] = "extraction"
                inputs = self.context.inputs(doc, task)
                result = {"markdown": plan["native_markdown"], "input_sha256": digest(inputs)}
                receipt = {
                    "task_id": task["task_id"],
                    "worker_id": "local:pdfium",
                    "input_sha256": digest(inputs),
                    "approval_type": "extraction",
                    "human_approval": False,
                    "created_at": time.time(),
                }
                task.update(
                    state="completed",
                    inputs=inputs,
                    claim={"input_sha256": digest(inputs)},
                    result=result,
                    receipt={**receipt, "result_sha256": digest(result)},
                )
                page["ocr"] = {
                    **receipt,
                    "markdown": result["markdown"],
                    "sha256": digest(result["markdown"]),
                }
                page["state"] = "awaiting_review"
                doc["tasks"].append(new_task("review", page["page"]))
            with self.context.repository.lock:
                self.context.repository.write(doc)
            return doc
