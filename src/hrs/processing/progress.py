"""Page coverage facts, independent of UI labels and final approval authority."""

from typing import TypedDict

PAGE_PROGRESS_POLICY = "final-review-coverage-v2"


class PageProgress(TypedDict):
    total: int
    extracted: int
    reviewed: int
    machine_reviewed: int
    approved: int
    pending: int


def page_progress(document: dict) -> PageProgress:
    latest_reviews = {
        task["page"]: task
        for task in document["tasks"]
        if task["stage"] in {"review", "verify"} and task["state"] != "superseded"
    }
    page_states = {
        page.get("page", index): page["state"] for index, page in enumerate(document["pages"], 1)
    }
    machine_pages = {
        page for page, task in latest_reviews.items() if task.get("result") and page in page_states
    }
    terminal_human_pages = {
        task["page"]
        for task in document["tasks"]
        if task["stage"] == "human_review"
        and task["state"] == "completed"
        and task.get("result", {}).get("verdict") == "approved"
        and page_states.get(task["page"]) == "approved"
    }
    return {
        "total": len(document["pages"]),
        "extracted": sum(bool(page.get("ocr")) for page in document["pages"]),
        "reviewed": len(machine_pages | terminal_human_pages),
        "machine_reviewed": len(machine_pages),
        "approved": sum(page["state"] == "approved" for page in document["pages"]),
        "pending": sum(page["state"] == "pending" for page in document["pages"]),
    }
