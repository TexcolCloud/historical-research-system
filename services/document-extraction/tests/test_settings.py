"""Keep installed model layout and DeepSeek environment routing stable."""

from dataclasses import asdict
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from document_extraction import settings


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / "config.json"
        self.config.write_text(
            json.dumps(
                {"ocr_backends": [{"name": "paddleocr-vl", "kind": "paddleocr-vl"}]}
            )
        )
        self.env = self.root / ".env"
        for context in (
            patch.dict(os.environ, {}, clear=True),
            patch.object(settings, "project_root", return_value=self.root),
        ):
            context.start()
            self.addCleanup(context.stop)

    def test_model_layout_and_environment_overrides_are_preserved(self):
        self.env.write_text(
            "HRS_MODEL_ROOT=local-models\nHRS_OCR_DEVICE=cpu\nHRS_MAX_VRAM_GB=9\nDEEPSEEK_VISION_MODEL=deepseek-fixture\n"
        )
        result = settings.Settings.load(self.config, self.env)
        self.assertEqual(result.model_root, self.root / "local-models")
        self.assertEqual(result.acceleration.device, "cpu")
        self.assertEqual(result.acceleration.max_vram_gb, 9)
        self.assertEqual(result.vision_review.model, "Qwen3-VL-8B-Instruct-Q4_K_M")
        for key, suffix in [
            ("DOCLING_ARTIFACTS_PATH", "docling"),
            ("PADDLE_PDX_CACHE_HOME", "paddleocr"),
            ("HF_HOME", "huggingface"),
            ("HF_HUB_CACHE", "huggingface/hub"),
            ("TORCH_HOME", "torch"),
        ]:
            self.assertEqual(Path(os.environ[key]), result.model_root / suffix)

    def test_deepseek_api_mode_and_explicit_endpoint_routing(self):
        os.environ["DEEPSEEK_API_MODE"] = "chat_completions"
        os.environ["DEEPSEEK_BASE_URL"] = "https://provider.invalid/chat/completions"
        self.assertEqual(
            settings.Settings.load(self.config, self.env).vision_review.endpoint,
            "http://127.0.0.1:18160/v1/chat/completions",
        )
        os.environ["DEEPSEEK_VISION_API_MODE"] = "responses"
        self.assertEqual(
            settings.Settings.load(self.config, self.env).vision_review.endpoint,
            "http://127.0.0.1:18160/v1/chat/completions",
        )
        os.environ["DEEPSEEK_VISION_ENDPOINT"] = "https://vision.invalid/endpoint"
        self.assertEqual(
            settings.Settings.load(self.config, self.env).vision_review.endpoint,
            "http://127.0.0.1:18160/v1/chat/completions",
        )

    def test_only_one_backend_is_accepted(self):
        for backends in ([], [{"kind": "docling"}, {"kind": "paddleocr-vl"}]):
            with self.subTest(backends=backends):
                self.config.write_text(json.dumps({"ocr_backends": backends}))
                with self.assertRaisesRegex(ValueError, "Exactly one"):
                    settings.Settings.load(self.config, self.env)

    def test_shipped_configs_only_contain_active_settings(self):
        config_root = Path(settings.__file__).resolve().parents[2] / "config"
        for name, kind in [
            ("default.json", "paddleocr-vl"),
            ("rapidocr.json", "docling"),
        ]:
            with self.subTest(config=name):
                path = config_root / name
                result = settings.Settings.load(path, self.env)
                self.assertEqual(result.ocr_backends[0]["kind"], kind)
                self.assertLessEqual(
                    json.loads(path.read_text("utf-8")).keys(), asdict(result).keys()
                )
