"""Adapt the retained extraction CLI; business artifacts are committed to S3."""

import json
import os
import subprocess
import time

from hrs_runtime.object_storage import S3Objects, file_digest
from pypdf import PdfReader
from sqlalchemy import func, insert, select, update
from temporalio import activity
from temporalio.exceptions import ApplicationError

from . import schema as db
from .books import get_run


def objects_for(settings):
    return S3Objects(
        endpoint=settings.s3_endpoint,
        bucket=settings.s3_bucket,
        access_key=settings.s3_access_key.get_secret_value(),
        secret_key=settings.s3_secret_key.get_secret_value(),
        region=settings.s3_region,
    )


class Activities:
    def __init__(self, settings, engine):
        self.settings, self.engine = settings, engine
        self.objects = objects_for(settings)

    def report_progress(self, run_id, completed, total):
        # Observability only: never change the content revision or release state.
        progress = {"stage": "vision", "completed": min(completed, total), "total": total}
        with self.engine.begin() as connection:
            run = (
                connection.execute(select(db.runs).where(db.runs.c.id == run_id).with_for_update())
                .mappings()
                .one()
            )
            result = run["result"] or {}
            if run["stage"] != "vision" or run["state"] != "processing" or result.get("progress") == progress:
                return
            connection.execute(
                update(db.runs).where(db.runs.c.id == run_id).values(result={**result, "progress": progress})
            )
            connection.execute(
                insert(db.events).values(
                    book_id=run["book_id"], run_id=run_id, kind="run.progress", payload={"progress": progress}
                )
            )

    def transition(self, run_id, state, stage, **values):
        with self.engine.begin() as connection:
            run = (
                connection.execute(select(db.runs).where(db.runs.c.id == run_id).with_for_update())
                .mappings()
                .one()
            )
            from .deletion import require_active

            require_active(connection, run["book_id"])
            connection.execute(
                update(db.runs)
                .where(db.runs.c.id == run_id)
                .values(
                    state=state, stage=stage, revision=run["revision"] + 1, updated_at=func.now(), **values
                )
            )
            book_state = "ready" if (run["result"] or {}).get("published") else state
            if run["kind"] == "book":
                connection.execute(
                    update(db.books).where(db.books.c.id == run["book_id"]).values(state=book_state)
                )
            connection.execute(
                insert(db.events).values(
                    book_id=run["book_id"],
                    run_id=run_id,
                    kind="run.changed",
                    payload={
                        "state": state,
                        "stage": stage,
                        "revision": run["revision"] + 1,
                        "run_kind": run["kind"],
                        "book_state": book_state if run["kind"] == "book" else None,
                    },
                )
            )

    @activity.defn
    def verify_upload(self, run_id: str) -> dict:
        run = get_run(self.engine, run_id)
        if run["source"].get("sha256"):
            self.objects.verify(run["source"])
            return {"run_id": run_id}
        source = run["source"]
        destination = self.settings.cache_root / run_id / "source.pdf"
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(".downloading")
        try:
            response = self.objects.client.get_object(Bucket=self.objects.bucket, Key=source["upload_key"])
            try:
                if response["ContentLength"] != source["byte_length"]:
                    raise ApplicationError("上传对象大小不符。", non_retryable=True)
                with temporary.open("wb") as stream:
                    for part in response["Body"].iter_chunks(1024 * 1024):
                        stream.write(part)
            finally:
                response["Body"].close()
            try:
                reader = PdfReader(temporary)
                if reader.is_encrypted or not len(reader.pages):
                    raise ValueError("empty or encrypted PDF")
            except Exception as error:
                raise ApplicationError(
                    "文件无法作为未加密 PDF 读取，请重新上传。", non_retryable=True
                ) from error
            temporary.replace(destination)
            reference = self.objects.put_file(destination, "application/pdf")
            self.transition(run_id, "processing", "conversion", source=reference)
            return {"run_id": run_id}
        finally:
            temporary.unlink(missing_ok=True)

    @activity.defn
    def convert_document(self, run_id: str) -> dict:
        from .outputs import Outputs

        with Outputs(self.settings, self.engine).operation(
            run_id, "component:convert_document", "文档转换与原件核验"
        ):
            return self._convert_document(run_id)

    def _convert_document(self, run_id):
        run = get_run(self.engine, run_id)
        if run["conversion"]:
            self.objects.verify(run["conversion"])
            return {"run_id": run_id, "state": run["state"]}
        directory = self.settings.cache_root / run_id
        source = self.objects.materialize(run["source"], directory / "source.pdf")
        output = directory / "conversion"
        manifest_path = output / "manifest.json"
        if not self._recoverable(manifest_path, run["source"]["sha256"]):
            self._ocr_checkpoint(run_id, source, output)
            self.transition(run_id, "processing", "vision")
            self._extract(run_id, source, output, phase="review")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not self._recoverable(manifest_path, run["source"]["sha256"]):
            raise ApplicationError("转换产物的来源或证据校验失败。", non_retryable=True)
        refs = {}
        for path in sorted(output.rglob("*")):
            if path.is_file():
                refs[path.relative_to(output).as_posix()] = self.objects.put_file(path)
                activity.heartbeat({"stage": "saving_evidence", "files": len(refs)})
        bundle = self.objects.put_bytes(
            json.dumps({"schema_version": 1, "manifest": manifest, "files": refs}, ensure_ascii=False).encode(
                "utf-8"
            )
        )
        # Machine results are preserved. This stage never records a human decision.
        state = (
            "ready_for_ingestion"
            if manifest.get("content_status") == "release-accepted" and not manifest["pending_human_review"]
            else "awaiting_review"
        )
        self.transition(
            run_id, state, "review" if state == "awaiting_review" else "ingestion", conversion=bundle
        )
        return {"run_id": run_id, "state": state}

    @staticmethod
    def _recoverable(path, source_sha):
        if not path.is_file():
            return False
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
            if manifest["source_sha256"] != source_sha or not manifest["page_count"]:
                return False
            if manifest.get("content_status") == "unreviewed-source":
                return False
            checks = dict(manifest["evidence_hashes"])
            if not checks:
                return False
            mapping = json.loads((path.parent / manifest["source_map"]).read_text(encoding="utf-8"))
            checks[manifest["candidate_markdown"]] = mapping["markdown_sha256"]
            checks.update({page["image"]: page["image_sha256"] for page in manifest["pages"]})
            return bool(checks) and all(
                (path.parent / name).is_file() and file_digest(path.parent / name)[0] == digest
                for name, digest in checks.items()
            )
        except (KeyError, ValueError, OSError):
            return False

    def _ocr_checkpoint(self, run_id, source, output):
        from .outputs import Outputs

        outputs = Outputs(self.settings, self.engine)
        config = self.settings.project_root / "services/document-extraction/config/default.json"
        dependency = {"source": file_digest(source)[0], "config": file_digest(config)[0]}
        saved = outputs.get(run_id, "ocr-evidence", dependency)
        if saved is None:
            self.transition(run_id, "processing", "ocr")
            self._extract(run_id, source, output, phase="ocr")
            files = {}
            for path in sorted(output.rglob("*")):
                if path.is_file():
                    files[path.relative_to(output).as_posix()] = self.objects.put_file(path)
                    activity.heartbeat({"stage": "saving_ocr", "files": len(files)})
            outputs.put(run_id, "ocr-evidence", {"files": files}, dependency)
        else:
            for name, reference in saved["files"].items():
                destination = (output / name).resolve()
                if not destination.is_relative_to(output.resolve()):
                    raise ValueError("OCR checkpoint file is outside the compute directory")
                self.objects.materialize(reference, destination)
                activity.heartbeat({"stage": "restoring_ocr"})

    def _extract(self, run_id, source, output, *, phase):
        root = self.settings.project_root
        python = root / "services/document-extraction/.venv/Scripts/python.exe"
        config = root / "services/document-extraction/config/default.json"
        values = json.loads(config.read_text(encoding="utf-8"))
        if values["vision_review"]["review_mode"] != "full":
            raise ApplicationError("新书必须使用完整视觉核验配置。", non_retryable=True)
        output.parent.mkdir(parents=True, exist_ok=True)
        checkpoint = output / "ocr-checkpoint.json"
        total = (
            len(json.loads(checkpoint.read_text("utf-8"))["pages"])
            if phase == "review" and checkpoint.exists()
            else 0
        )
        observed = set()
        last_reported = -1

        def progress():
            nonlocal last_reported
            if not total:
                return
            # The retained converter owns these immutable per-page receipts. This
            # count is initial-pass telemetry, never a final acceptance decision.
            for path in (output / "reviews").glob("completion-*-initial.json"):
                if path.name in observed:
                    continue
                try:
                    value = json.loads(path.read_text("utf-8"))
                    if (
                        value.get("phase") == "initial"
                        and value.get("verdict", {}).get("review_state") == "completed"
                    ):
                        observed.add(path.name)
                except (OSError, ValueError):
                    continue  # A receipt may still be being written.
            if len(observed) != last_reported:
                self.report_progress(run_id, len(observed), total)
                last_reported = len(observed)

        with (output.parent / "extraction.log").open("ab") as log:
            process = subprocess.Popen(
                [
                    str(python),
                    "-m",
                    "document_extraction.stages",
                    phase,
                    str(source),
                    "--output",
                    str(output),
                    "--config",
                    str(config),
                ],
                cwd=root,
                stdout=log,
                stderr=log,
                env={**os.environ, "PYTHONIOENCODING": "utf-8", "HRS_TASK_ID": run_id},
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            try:
                while process.poll() is None:
                    if activity.in_activity() and activity.is_cancelled():
                        from temporalio.exceptions import CancelledError

                        raise CancelledError("Book task cancelled")
                    activity.heartbeat({"stage": phase, "run_id": run_id})
                    progress()
                    time.sleep(2)
                if process.returncode:
                    raise ApplicationError("文档转换失败，诊断日志已保留。")
                progress()
            finally:
                if process.poll() is None:
                    if os.name == "nt":
                        # The Windows venv launcher has an interpreter child. Stop
                        # only this owned tree so cancellation cannot orphan GPU work.
                        subprocess.run(
                            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                            check=False,
                            stdout=log,
                            stderr=log,
                        )
                    else:
                        process.terminate()
                    try:
                        process.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()

    @activity.defn
    def record_conversion_failure(self, run_id: str):
        self.transition(
            run_id,
            "failed",
            "conversion",
            error={"code": "conversion_failed", "message": "处理未完成，可在运行记录查看失败原因；未入库。"},
        )
