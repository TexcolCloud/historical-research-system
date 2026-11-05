"""Machine review coverage and final human approval are distinct facts."""


def test_progress_uses_latest_live_reviews_without_conflating_human_approval():
    from hrs_codex.progress import page_progress

    document = {
        "pages": [
            {"ocr": {}, "state": "approved"},
            {"ocr": {"markdown": "text"}, "state": "pending"},
        ],
        "tasks": [
            {
                "stage": "review",
                "state": "superseded",
                "page": 1,
                "result": {"verdict": "approved"},
            },
            {
                "stage": "human_review",
                "state": "completed",
                "page": 1,
                "result": {"verdict": "approved"},
            },
            {"stage": "review", "state": "completed", "page": 2, "result": {"verdict": "pending"}},
        ],
    }
    assert page_progress(document) == {
        "total": 2,
        "extracted": 1,
        "reviewed": 1,
        "approved": 1,
        "pending": 1,
    }
