"""Active conversion, local visual review and device configuration."""

from __future__ import annotations
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def project_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "AGENTS.md").exists():
            return parent
    raise RuntimeError("无法定位主项目根目录")


def load_dotenv(path: Path | None = None) -> Path:
    target = path or Path(os.getenv("HRS_ENV_FILE", project_root() / ".env"))
    if not target.exists():
        return target
    for raw_line in target.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))
    return target


def configure_model_storage(root: Path) -> None:
    paths = {
        "DOCLING_ARTIFACTS_PATH": root / "docling",
        "PADDLE_PDX_CACHE_HOME": root / "paddleocr",
        "HF_HOME": root / "huggingface",
        "HF_HUB_CACHE": root / "huggingface" / "hub",
        "TORCH_HOME": root / "torch",
    }
    root.mkdir(parents=True, exist_ok=True)
    for key, path in paths.items():
        path.mkdir(parents=True, exist_ok=True)
        os.environ[key] = str(path)


@dataclass(frozen=True)
class ReviewSettings:
    enabled: bool
    timeout_seconds: int
    api_key: str | None
    api_mode: str
    endpoint: str
    model: str
    cache_enabled: bool = True
    cache_path: Path | None = None
    review_mode: str = "risk_based"
    confidence_threshold: float = 0.98

    def __post_init__(self):
        if self.review_mode not in {'risk_based', 'full', 'conversion_only'}:
            raise ValueError('review_mode must be risk_based, full or conversion_only')
        if not 0 < self.confidence_threshold <= 1:
            raise ValueError('confidence_threshold must be in (0, 1]')


@dataclass(frozen=True)
class RiskSettings:
    enabled: bool = True
    auto_accept: bool = True


@dataclass(frozen=True)
class AccelerationSettings:
    device: str
    device_id: int
    dtype: str
    attention: str
    fast_min_vram_gb: float
    cpu_threads: int
    cpu_fallback: bool
    max_vram_gb: float
    enforce_vram_limit: bool = True


@dataclass(frozen=True)
class Settings:
    render_dpi: int
    ocr_backends: list[dict[str, Any]]
    vision_review: ReviewSettings
    risk: RiskSettings
    acceleration: AccelerationSettings
    env_file: Path
    model_root: Path

    @classmethod
    def load(cls, config_path: Path, env_path: Path | None = None) -> "Settings":
        env_file = load_dotenv(env_path)
        raw_model_root = Path(os.getenv("HRS_MODEL_ROOT", "models"))
        model_root = (
            raw_model_root
            if raw_model_root.is_absolute()
            else project_root() / raw_model_root
        ).resolve()
        configure_model_storage(model_root)
        config = json.loads(config_path.read_text(encoding="utf-8"))
        review = config.get("vision_review", {})
        risk = config.get("risk", {})
        acceleration = config.get("acceleration", {})
        from hrs_runtime.local_vision import MODEL, endpoint as local_endpoint
        api_mode = 'chat_completions'
        endpoint = local_endpoint() + '/v1/chat/completions'
        raw_cache_path = Path(
            os.getenv("HRS_VISION_CACHE")
            or review.get("cache_path", "state/vision-review-cache")
        )
        cache_path = (
            raw_cache_path
            if raw_cache_path.is_absolute()
            else Path(__file__).resolve().parents[2] / raw_cache_path
        ).resolve()
        if len(config.get("ocr_backends", [])) != 1:
            raise ValueError("Exactly one OCR backend is required; dual OCR is retired")
        return cls(
            render_dpi=int(config.get("render_dpi", 300)),
            ocr_backends=config.get("ocr_backends", []),
            vision_review=ReviewSettings(
                enabled=bool(review.get("enabled", True)),
                timeout_seconds=int(review.get("timeout_seconds", 900)),
                api_key='local',
                api_mode=api_mode,
                endpoint=endpoint,
                model=MODEL,
                cache_enabled=bool(review.get("cache_enabled", True)),
                cache_path=cache_path,
                review_mode=str(review.get('review_mode', 'risk_based')),
                confidence_threshold=float(review.get('confidence_threshold', 0.98)),
            ),
            risk=RiskSettings(
                enabled=bool(risk.get("enabled", True)),
                auto_accept=bool(risk.get("auto_accept", True)),
            ),
            acceleration=AccelerationSettings(
                device=os.getenv("HRS_OCR_DEVICE")
                or str(acceleration.get("device", "auto")),
                device_id=int(
                    os.getenv("HRS_OCR_DEVICE_ID") or acceleration.get("device_id", 0)
                ),
                dtype=str(acceleration.get("dtype", "float16")),
                attention=str(acceleration.get("attention", "auto")),
                fast_min_vram_gb=float(acceleration.get("fast_min_vram_gb", 12)),
                cpu_threads=int(acceleration.get("cpu_threads", 8)),
                cpu_fallback=bool(acceleration.get("cpu_fallback", True)),
                max_vram_gb=float(
                    os.getenv("HRS_MAX_VRAM_GB")
                    or acceleration.get("max_vram_gb", 15.0)
                ),
                enforce_vram_limit=os.getenv("HRS_ENFORCE_VRAM_LIMIT", "").lower()
                not in {"0", "false", "no", "off"}
                if os.getenv("HRS_ENFORCE_VRAM_LIMIT") is not None
                else bool(acceleration.get("enforce_vram_limit", True)),
            ),
            env_file=env_file,
            model_root=model_root,
        )
