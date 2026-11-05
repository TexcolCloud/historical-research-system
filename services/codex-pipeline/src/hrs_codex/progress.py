"""Page coverage facts, independent of UI labels and final approval authority."""

from typing import TypedDict


class PageProgress(TypedDict):
    total: int
    extracted: int
    reviewed: int
    approved: int
    pending: int


def page_progress(document: dict) -> PageProgress:
    latest_reviews = {
        task["page"]: task
        for task in document["tasks"]
        if task["stage"] in {"review", "verify"} and task["state"] != "superseded"
    }
    return {
        "total": len(document["pages"]),
        "extracted": sum(bool(page.get("ocr")) for page in document["pages"]),
        "reviewed": sum(bool(task.get("result")) for task in latest_reviews.values()),
        "approved": sum(page["state"] == "approved" for page in document["pages"]),
        "pending": sum(page["state"] == "pending" for page in document["pages"]),
    }
