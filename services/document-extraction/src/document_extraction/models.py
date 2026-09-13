from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class OcrResult:
    text: str
    blocks: list[dict[str, Any]]
    confidence: float | None = None
    metadata: dict[str, Any] | None = None


@dataclass
class PageResult:
    page: int
    image: str
    candidate_text: str
    status: str
    similarity: float | None
    ocr: dict[str, str]
    differences: list[dict[str, Any]]
    local_review: dict[str, Any] | None
    vision_review: dict[str, Any] | None
    processing: dict[str, Any]
