"""Terminal coverage counts each page once while machine evidence stays distinct."""


def test_progress_combines_latest_machine_review_and_final_human_coverage():
    from hrs.processing.progress import page_progress

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
        "reviewed": 2,
        "machine_reviewed": 1,
        "approved": 1,
        "pending": 1,
    }


def test_terminal_human_and_machine_review_of_the_same_page_are_not_counted_twice():
    from hrs.processing.progress import page_progress

    document = {
        "pages": [{"page": 1, "state": "approved", "ocr": {"markdown": "source"}}],
        "tasks": [
            {"page": 1, "stage": "review", "state": "completed", "result": {"verdict": "pending"}},
            {
                "page": 1,
                "stage": "human_review",
                "state": "completed",
                "result": {"verdict": "approved"},
            },
        ],
    }
    assert page_progress(document)["reviewed"] == page_progress(document)["machine_reviewed"] == 1
    document["pages"][0]["state"] = "pending"
    document["tasks"][0]["state"] = "superseded"
    assert page_progress(document)["reviewed"] == page_progress(document)["machine_reviewed"] == 0
