"""Exercise production request/response parsing and source-bound review cache."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
import urllib.error
import urllib.request

from document_extraction import semantic_completion as semantic
from document_extraction.settings import ReviewSettings
from test_single_ocr_completion import verdict


class ReviewTransportTests(unittest.TestCase):
    def setUp(self):
        source = patch.object(semantic, 'source_reading', return_value={
            'reading': {'numeric_lines': [], 'tables': [], 'unclear': []}, 'usage': {}})
        source.start()
        self.addCleanup(source.stop)
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.image = self.root / "page.jpg"
        self.image.write_bytes(b"original-image-fixture")
        self.settings = ReviewSettings(
            True,
            10,
            "fixture-key",
            "responses",
            "https://review.invalid/responses",
            "Qwen3-VL-8B-Instruct-Q4_K_M",
            cache_path=self.root / "cache",
        )
        self.packet = {
            "target": {"page": 1, "text": "1938年开始生产。"},
            "context": [],
            "image_order": [1],
        }

    def response(self, mode="responses", *, model="Qwen3-VL-8B-Instruct-Q4_K_M", target=1):
        value = verdict()
        value["target_page"] = target
        raw = json.dumps(value, ensure_ascii=False)
        body = (
            {"choices": [{"message": {"content": raw}}]}
            if mode == "chat_completions"
            else {
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": raw}],
                    }
                ]
            }
        )
        return {**body, "model": model, "usage": {"total_tokens": 42}}

    def test_both_api_modes_send_original_image_and_read_deepseek_verdict(self):
        for mode in ("responses", "chat_completions"):
            with (
                self.subTest(mode=mode),
                patch.object(
                    semantic, "_request_json", return_value=self.response(mode)
                ) as request,
            ):
                result = semantic.review_page(
                    self.packet, [self.image], replace(self.settings, api_mode=mode)
                )
                self.assertEqual(result["review_state"], "completed")
                self.assertEqual(result["usage"]["total_tokens"], 42)
                payload = json.loads(request.call_args.args[0].data)
                parts = payload["input" if mode == "responses" else "messages"][0][
                    "content"
                ]
                self.assertEqual(parts[1]["text"], "TARGET: physical page 1")
                image_url = parts[2]["image_url"]
                if isinstance(image_url, dict):
                    image_url = image_url["url"]
                self.assertTrue(image_url.startswith("data:image/jpeg;base64,"))

    def test_cache_hit_and_invalidations_for_image_text_model_and_endpoint(self):
        with patch.object(
            semantic, "_request_json", return_value=self.response()
        ) as request:
            semantic.review_page(self.packet, [self.image], self.settings)
            self.assertTrue(
                semantic.review_page(self.packet, [self.image], self.settings)[
                    "cache_hit"
                ]
            )
            self.assertEqual(request.call_count, 1)
            self.image.write_bytes(b"changed-image")
            self.assertFalse(
                semantic.review_page(self.packet, [self.image], self.settings)[
                    "cache_hit"
                ]
            )
            self.packet["target"]["text"] = "1939年开始生产。"
            semantic.review_page(self.packet, [self.image], self.settings)
            with self.assertRaises(ValueError):
                semantic.review_page(self.packet, [self.image], replace(self.settings, model="deepseek-other"))
            semantic.review_page(
                self.packet,
                [self.image],
                replace(self.settings, endpoint="https://other.invalid/responses"),
            )
            self.assertEqual(request.call_count, 4)

    def test_corrupt_review_cache_is_recomputed(self):
        with patch.object(
            semantic, "_request_json", return_value=self.response()
        ) as request:
            semantic.review_page(self.packet, [self.image], self.settings)
            next((self.root / "cache/single-draft").glob("*.json")).write_text(
                "{broken", encoding="utf-8"
            )
            result = semantic.review_page(self.packet, [self.image], self.settings)
            self.assertEqual(result["review_state"], "completed")
            self.assertEqual(request.call_count, 2)

    def test_wrong_page_or_non_deepseek_response_remains_pending_after_retry(self):
        for body in (
            self.response(target=2),
            self.response(model="unconfigured-model"),
        ):
            with (
                self.subTest(body=body),
                patch.object(semantic, "_request_json", return_value=body) as request,
            ):
                result = semantic.review_page(self.packet, [self.image], self.settings)
                self.assertEqual(result["review_state"], "unavailable")
                self.assertEqual(request.call_count, 2)
        self.assertFalse((self.root / "cache/single-draft").exists())

    def test_non_deepseek_configuration_is_rejected_before_network(self):
        with (
            patch.object(semantic, "_request_json") as request,
            self.assertRaisesRegex(ValueError, "local Qwen"),
        ):
            semantic.review_page(
                self.packet, [self.image], replace(self.settings, model="other-model")
            )
        request.assert_not_called()

    def test_transient_transport_failure_retries_then_returns_json(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'{"ok": true}'
        with (
            patch.object(
                urllib.request,
                "urlopen",
                side_effect=[urllib.error.URLError("offline"), response],
            ) as call,
            patch.object(semantic.time, "sleep"),
        ):
            self.assertEqual(
                semantic._request_json(
                    urllib.request.Request("https://example.invalid"), 1, attempts=2
                ),
                {"ok": True},
            )
        self.assertEqual(call.call_count, 2)

    def test_exhausted_transport_retries_propagate_failure(self):
        with (
            patch.object(
                urllib.request, "urlopen", side_effect=TimeoutError("offline")
            ) as call,
            patch.object(semantic.time, "sleep"),
            self.assertRaises(TimeoutError),
        ):
            semantic._request_json(
                urllib.request.Request("https://example.invalid"), 1, attempts=2
            )
        self.assertEqual(call.call_count, 2)

    def test_independent_read_failure_cannot_become_a_clean_page_pass(self):
        with patch.object(semantic, 'source_reading', side_effect=ValueError('incomplete')), patch.object(semantic, '_request_json') as request:
            result = semantic.review_page(self.packet, [self.image], self.settings)
        self.assertEqual(result['review_state'], 'unavailable')
        request.assert_not_called()
