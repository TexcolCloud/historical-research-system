"""Original-first local visual verdicts, reusable responses and native-pixel retry."""

import base64
import json

from hrs_runtime.local_vision import MODEL, POLICY, chat

from .domain import prompts
from .domain.generation_contracts import ImageCheck
from .domain.vision import original_crops
from .outputs import Outputs, fingerprint
from .review import Review


def result_key(card_id, group_key):
    rules = fingerprint(
        {
            "instructions": prompts.VISION,
            "schema": ImageCheck.model_json_schema(),
            "model": MODEL,
            "policy": POLICY,
        }
    )
    return f"视觉核对:{card_id}:{group_key}:rules-{rules[:16]}"


class VisualReview:
    def __init__(self, settings, engine):
        self.settings, self.outputs, self.review = (
            settings,
            Outputs(settings, engine),
            Review(settings, engine),
        )

    def check(self, run, bundle, card, group):
        try:
            return self._check(run, bundle, card, group)
        except Exception as error:
            self.outputs.node(
                run["id"],
                result_key(card["id"], group["key"]),
                kind="model",
                label="原件视觉核验",
                objective=f"对照原件 {group['pages']} 页",
                parent=card["parent_node"],
                state="failed",
                details={"error_type": type(error).__name__},
            )
            raise

    def _check(self, run, bundle, card, group):
        run_id = run["id"]
        key = result_key(card["id"], group["key"])
        request_prefix = f"{key}:recovery:{run['recovery_attempt']}" if run["recovery_attempt"] else key
        cached = self.outputs.get(run_id, key)
        if cached:
            return cached
        pages = {row["page"]: row for row in bundle["manifest"]["pages"]}
        evidence = [
            {
                "image_id": f"original-page-{number}",
                "page": number,
                "reference": bundle["files"][pages[number]["image"]],
            }
            for number in group["pages"]
        ]
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
                                "reference": self.review.objects.put_bytes(content, "image/png"),
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
                    if self.outputs.get(run_id, request_key):
                        continue
                    self.outputs.reserve_request(run_id, request_key, request, self.settings.model_max_calls)
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
                    response = chat(
                        [{"role": "system", "content": prompts.VISION}, {"role": "user", "content": content}],
                        schema=ImageCheck.model_json_schema(),
                        max_tokens=8000,
                    )
                    self.outputs.put(run_id, response_key, response, request)
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
                raise ValueError("视觉核验回执无效或已达到请求上限；原始证据保留。")
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
