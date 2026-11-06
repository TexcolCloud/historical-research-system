"""Context limits must never masquerade as document completion or omit its total."""

import json

from hrs.codex.mcp import compact_document


def test_long_document_status_retains_global_counts_without_repeating_source_text():
    doc = {
        "pages": [
            {
                "page": p,
                "state": "awaiting_review",
                "ocr": {"markdown": "source must not be repeated", "sha256": "a" * 64},
            }
            for p in range(1, 1001)
        ],
        "tasks": [
            {
                "task_id": str(p),
                "stage": "review",
                "page": p,
                "state": "ready",
                "inputs": {"markdown": "candidate not for coordinator"},
            }
            for p in range(1, 1001)
        ],
        "organization": None,
        "navigation": {"entries": []},
    }
    result = compact_document(doc)
    assert result["page_count"] == 1000 and result["page_counts"] == {"awaiting_review": 1000}
    assert result["task_counts"]["review"]["ready"] == 1000
    assert (
        result["page_list_truncated"]
        and result["task_list_truncated"]
        and not result["all_pages_approved"]
    )
    assert len(json.dumps(result)) < 16000
    assert "must not be repeated" not in json.dumps(
        result
    ) and "candidate not for coordinator" not in json.dumps(result)
