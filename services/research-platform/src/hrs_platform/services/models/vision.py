"""Original-first local visual verdicts, reusable responses and native-pixel retry."""

import base64
import json
import time
from importlib.metadata import version
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import HTTPError, URLError

from botocore.exceptions import BotoCoreError, ClientError
from hrs_runtime.local_vision import MODEL, POLICY, chat
from hrs_runtime.object_storage import ObjectStorageError
from hrs_platform.domain.errors import TaskError

from hrs_platform.domain import prompts
from hrs_platform.domain.generation_contracts import ImageCheck
from hrs_platform.domain.vision import original_crops
from hrs_platform.services.runs.outputs import Outputs, fingerprint
from hrs_platform.services.documents.review import Review

RENDER_POLICY = f"pdfium-{version('pypdfium2')}-216dpi-v1"


def result_key(card_id, group_key, revision=None):
    rules = fingerprint(
        {
            "instructions": prompts.VISION,
            "schema": ImageCheck.model_json_schema(),
            "model": MODEL,
            "policy": POLICY,
        }
    )
    return f"视觉核对:{card_id}:{group_key}:rules-{rules[:16]}" + (
        f":candidate-{revision}" if revision else ""
    )


class VisualReview:
    def __init__(self, settings, engine):
        self.settings, self.outputs, self.review = (
            settings,
            Outputs(settings, engine),
            Review(settings, engine),
        )

    def prepare(self, run, bundle, numbers):
        """Verify source objects, restoring physical PDF pages without OCR or approval."""
        manifest = bundle["manifest"]
        mapped = {row["page"]: row for row in manifest.get("pages", [])}
        ready, issues = [], []
        for number in sorted(set(numbers)):
            key = f"original-page:{run['source']['sha256']}:{number}:{RENDER_POLICY}"
            retained = self.outputs.get(run["id"], key)
            reference = (
                retained["reference"]
                if retained
                else bundle["files"].get(mapped.get(number, {}).get("image"))
            )
            try:
                if reference is None:
                    raise FileNotFoundError("Missing page mapping")
                self.review.objects.verify(reference)
            except (FileNotFoundError, ObjectStorageError, ClientError, BotoCoreError) as error:
                if isinstance(error, (ClientError, BotoCoreError)) and not self._missing(error):
                    raise TaskError(
                        f"原件第 {number} 页暂时无法读取，存储重试后仍失败。",
                        type="original_unavailable",
                        non_retryable=False,
                    ) from error
                try:
                    reference = self._restore_page(run, manifest, number, key)
                except TaskError as failure:
                    if failure.type != "original_missing":
                        raise
                    issues.append(
                        {
                            "page": number,
                            "error_type": failure.type,
                            "message": str(failure),
                            "required_action": "恢复原 PDF/页图或修正来源映射后重试；不能跳过原图核验。",
                        }
                    )
                    continue
            ready.append({"page": number, "reference": reference})
        report = {"pages": ready, "issues": issues, "machine_approval": False}
        self.outputs.put(
            run["id"],
            f"original-preflight:{fingerprint(report)}",
            report,
            {"source": run["source"], "numbers": sorted(set(numbers))},
        )
        return report

    @staticmethod
    def _missing(error):
        return isinstance(error, ClientError) and str(error.response.get("Error", {}).get("Code")) in {
            "404",
            "NoSuchKey",
            "NotFound",
        }

    def _restore_page(self, run, manifest, number, key):
        import pypdfium2 as pdfium

        source = run["source"]
        if (
            manifest.get("source_sha256") != source.get("sha256")
            or source.get("media_type") != "application/pdf"
            or not isinstance(number, int)
            or not 1 <= number <= manifest.get("page_count", 0)
        ):
            raise TaskError(
                f"原件第 {number} 页缺失，无法确认原 PDF 与物理页映射。",
                type="original_missing",
                non_retryable=True,
            )
        self.settings.cache_root.mkdir(parents=True, exist_ok=True)
        temporary = TemporaryDirectory(prefix="original-recovery-", dir=self.settings.cache_root)
        try:
            path = self.review.objects.materialize(source, Path(temporary.name) / "original.pdf")
            document = pdfium.PdfDocument(str(path))
            try:
                if len(document) != manifest["page_count"]:
                    raise TaskError(
                        f"原 PDF 页数与转换记录不符，不能恢复第 {number} 页。",
                        type="original_missing",
                        non_retryable=True,
                    )
                page = document[number - 1]
                try:
                    bitmap = page.render(scale=3)
                    try:
                        with BytesIO() as buffer:
                            image = bitmap.to_pil()
                            try:
                                image.save(buffer, format="PNG")
                            finally:
                                image.close()
                            reference = self.review.objects.put_bytes(buffer.getvalue(), "image/png", run_id=run["id"])
                    finally:
                        bitmap.close()
                finally:
                    page.close()
            finally:
                document.close()
        except (ClientError, BotoCoreError) as error:
            missing = self._missing(error)
            raise TaskError(
                f"原 PDF 无法读取，未恢复第 {number} 页。",
                type="original_missing" if missing else "original_unavailable",
                non_retryable=missing,
            ) from error
        except (ObjectStorageError, pdfium.PdfiumError) as error:
            raise TaskError(
                f"原 PDF 无法通过完整性检查或渲染，未恢复第 {number} 页。",
                type="original_missing",
                non_retryable=True,
            ) from error
        finally:
            temporary.cleanup()
        # The content-addressed object can be restored again if it was deleted.
        self.outputs.put(
            run["id"],
            key,
            {
                "reference": reference,
                "page": number,
                "source_sha256": source["sha256"],
                "renderer": RENDER_POLICY,
                "machine_approval": False,
            },
            {"source": source, "page": number},
        )
        return reference

    def check(self, run, bundle, card, group):
        try:
            return self._check(run, bundle, card, group)
        except Exception as error:
            self.outputs.node(
                run["id"],
                result_key(card["id"], group["key"], card.get("visual_revision")),
                kind="model",
                label="原件视觉核验",
                objective=f"对照原件 {group['pages']} 页",
                parent=card["parent_node"],
                state="waiting" if isinstance(error, TaskError) and error.type == "vision_service_wait" else "failed",
                details={"error_type": type(error).__name__},
            )
            raise

    def _response(self, run_id, request_key, response_key, request, messages):
        """Separate local service outages from invalid visual verdict attempts."""
        epoch = 0
        while previous := self.outputs.get(run_id, response_key + f":transport:{epoch}"):
            if previous["retry_at"] > time.time():
                raise TaskError("本地视觉服务暂不可用，保留证据等待恢复。", previous, type="vision_service_wait", non_retryable=True)
            epoch += 1
        suffix = f":service:{epoch}" if epoch else ""
        response = self.outputs.get(run_id, response_key + suffix)
        if response is not None:
            return response
        if self.outputs.get(run_id, request_key + suffix):
            return None  # Unknown outcome remains spent, never counted as approval.
        self.outputs.reserve_request(run_id, request_key + suffix, request, self.settings.vision_max_calls)
        try:
            response = chat(messages, schema=ImageCheck.model_json_schema(), max_tokens=8000)
        except (HTTPError, URLError, TimeoutError, ConnectionError) as error:
            if isinstance(error, HTTPError) and error.code not in {408, 429} and error.code < 500:
                raise TaskError("本地视觉服务拒绝请求，请检查配置。", type="vision_request_rejected", non_retryable=True) from None
            receipt = {"attempt": epoch + 1, "retry_at": time.time() + min(60 * 2 ** min(epoch, 5), 1800)}
            self.outputs.put(run_id, response_key + f":transport:{epoch}", receipt, request)
            raise TaskError("本地视觉服务暂不可用，保留证据等待恢复。", receipt, type="vision_service_wait", non_retryable=True) from None
        except ValueError:
            response = {"invalid_output": True}
        self.outputs.put(run_id, response_key + suffix, response, request)
        return response

    def _check(self, run, bundle, card, group):
        run_id = run["id"]
        key = result_key(card["id"], group["key"], card.get("visual_revision"))
        request_prefix = f"{key}:recovery:{run['recovery_attempt']}" if run["recovery_attempt"] else key
        cached = self.outputs.get(run_id, key)
        if cached:
            return cached
        prepared = self.prepare(run, bundle, group["pages"])
        if prepared["issues"]:
            raise TaskError(
                json.dumps(prepared["issues"], ensure_ascii=False),
                type="original_missing",
                non_retryable=True,
            )
        evidence = [{"image_id": f"original-page-{row['page']}", **row} for row in prepared["pages"]]
        self.outputs.node(
            run_id,
            key,
            kind="model",
            label="原件视觉核验",
            objective=f"对照原件 {group['pages']} 页",
            parent=card["parent_node"],
            details={"model": MODEL},
        )
        verdict = None
        for round_number in range(2):
            if round_number:
                crops = []
                for original in evidence:
                    for index, crop in enumerate(
                        original_crops(self.review.objects.read_bytes(original["reference"]))
                    ):
                        content = crop.pop("content")
                        crops.append(
                            {
                                "image_id": original["image_id"] + f"-crop-{index}",
                                "page": original["page"],
                                "reference": self.review.objects.put_bytes(content, "image/png", run_id=run_id),
                                "original_sha256": original["reference"]["sha256"],
                                **crop,
                            }
                        )
                evidence = crops
            request = {
                "source_sha256": run["source"]["sha256"],
                "images": evidence,
                "items": group["items"],
                "candidate_sha256": fingerprint(card["candidate"]),
                "model": MODEL,
                "policy": POLICY,
                "instructions": prompts.VISION,
                "schema": ImageCheck.model_json_schema(),
            }
            for attempt in range(3):
                request_key = f"{request_prefix}:http-request:{round_number}:{attempt}"
                response_key = f"{request_prefix}:response:{round_number}:{attempt}"
                response = self.outputs.get(run_id, response_key)
                if response is None:
                    # A recorded request with no response is an uncertain external call.
                    # Its allowance remains spent; recovery uses the next bounded attempt.
                    content = []
                    for image in evidence:
                        ref = image["reference"]
                        raw = self.review.objects.read_bytes(ref)
                        content.extend(
                            [
                                {"type": "text", "text": image["image_id"]},
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:{ref['media_type']};base64,"
                                        + base64.b64encode(raw).decode()
                                    },
                                },
                            ]
                        )
                    content.append(
                        {
                            "type": "text",
                            "text": json.dumps(
                                {"items": group["items"], "image_ids": [row["image_id"] for row in evidence]},
                                ensure_ascii=False,
                            ),
                        }
                    )
                    response = self._response(run_id, request_key, response_key, request,
                        [{"role": "system", "content": prompts.VISION}, {"role": "user", "content": content}])
                    if response is None:
                        continue
                try:
                    verdict = ImageCheck.model_validate_json(response["choices"][0]["message"]["content"])
                    identities = [item.item_id for item in verdict.items]
                    if len(identities) != len(group["items"]) or set(identities) != {
                        item["item_id"] for item in group["items"]
                    }:
                        raise ValueError("Incomplete item coverage")
                    if set(verdict.image_ids) != {row["image_id"] for row in evidence}:
                        raise ValueError("Incomplete original-image coverage")
                except (ValueError, KeyError, IndexError):
                    verdict = None
                    continue
                break
            if verdict is None:
                raise TaskError(
                    "视觉核验回执无效或已达到请求上限；原始证据保留。",
                    type="visual_output_invalid",
                    non_retryable=True,
                )
            if all(item.result == "verified" for item in verdict.items) or not any(
                item.result in {"unreadable", "not_located", "not_checked"} for item in verdict.items
            ):
                break
        value = verdict.model_dump(mode="json")
        self.outputs.put(run_id, key, value, request)
        self.outputs.node(
            run_id,
            key,
            kind="model",
            label="原件视觉核验",
            objective=f"对照原件 {group['pages']} 页",
            parent=card["parent_node"],
            state="completed",
            details={"model": MODEL, "verdict": value, "input_sha256": fingerprint(request)},
        )
        return value
