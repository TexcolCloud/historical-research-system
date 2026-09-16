import base64
import math
from functools import lru_cache
from pathlib import Path

from tokenizers import Tokenizer

from hrs_platform.domain.errors import Problem
from hrs_platform.domain.records import json_text, read_json, sha256


@lru_cache(maxsize=1)
def tokenizer():
    root = Path(__file__).parent / "data"
    manifest = read_json(root / "tokenizer-manifest.json")
    path = root / "deepseek-v4-tokenizer.json"
    if sha256(path.read_bytes()) != manifest["tokenizer_sha256"]:
        raise Problem("tokenizer_hash_mismatch", "The configured estimate tokenizer failed verification.")
    return Tokenizer.from_file(str(path))


def estimate_request(body):
    images, image_bytes = [], 0

    def without_images(value):
        nonlocal image_bytes
        if isinstance(value, dict):
            if value.get("type") == "input_image":
                url = value.get("image_url", "")
                if not url.startswith("data:") or ";base64," not in url:
                    raise Problem(
                        "image_bytes_required", "Supply locally verified image bytes for research review."
                    )
                raw = base64.b64decode(url.split(",", 1)[1], validate=True)
                image_bytes += len(raw)
                images.append({"sha256": sha256(raw), "byte_length": len(raw), "detail": value.get("detail")})
                return {"type": "input_image", "image_sha256": sha256(raw)}
            return {key: without_images(part) for key, part in value.items()}
        if isinstance(value, list):
            return [without_images(part) for part in value]
        return value

    serialized = json_text(without_images(body))
    raw_tokens = len(tokenizer().encode(serialized, add_special_tokens=False).ids)
    return {
        "input_tokens": math.ceil(raw_tokens * 1.2) + 256 + 384 * len(images),
        "serialized_text_tokens": raw_tokens,
        "images": images,
        "image_bytes": image_bytes,
        "estimate_version": "deepseek-v4-serialized-request-v1",
    }
