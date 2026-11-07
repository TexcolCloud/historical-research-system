"""Apply stage-specific results to a validated in-memory snapshot, without I/O."""

from hrs.processing.hashing import digest
from hrs.processing.workflow_rules import new_task, review_verdict


def apply_ocr(document, task, result, receipt):
    doc = document
    page = doc["pages"][task["page"] - 1] if task["page"] is not None else None
    markdown = result.get("markdown")
    if not isinstance(markdown, str):
        raise ValueError("OCR result requires markdown (empty is valid for a blank page)")
    page["ocr"] = {**receipt, "markdown": markdown, "sha256": digest(markdown)}
    if result.get("page_kind") in {
        "toc",
        "text",
        "frontmatter",
        "table",
        "figure",
        "unknown",
    }:
        page["page_kind"] = result["page_kind"]
    page["state"] = "awaiting_review"
    doc["tasks"].append(new_task("review", page["page"]))


def apply_review(document, task, result, receipt):
    doc = document
    page = doc["pages"][task["page"] - 1] if task["page"] is not None else None
    stage = task["stage"]
    automatic_verification = False
    verdict = review_verdict(result)
    if verdict == "approved" and task.get("source_reading", {}).get("reading", {}).get(
        "unresolved"
    ):
        raise ValueError("Unresolved original observations cannot be approved")
    if stage == "verify" and verdict == "corrections":
        raise ValueError("Failed correction verification must remain pending")
    text = page["candidate" if stage == "verify" else "ocr"]
    if verdict == "approved":
        if result.get("markdown", text["markdown"]) != text["markdown"]:
            raise ValueError("Changed text requires corrections and separate verification")
        page["approved"] = {**text, **receipt}
        page["state"] = "approved"
    elif verdict == "pending":
        page["state"] = "pending"
        if (
            stage == "review"
            and doc.get("processing_policy", {}).get("review_failure") == "bounded"
            and result.get("disposition") == "disputed"
            and not page.get("automatic_review_attempts")
        ):
            page["candidate"] = {**text, **receipt, "corrections": []}
            result = {**result, "markdown": text["markdown"], "recheck_unchanged": True}
            page["automatic_review_attempts"] = 1
            page["state"] = "awaiting_verification"
            doc["tasks"].append(new_task("verify", page["page"]))
            automatic_verification = True
    else:
        if (
            stage == "review"
            and doc.get("processing_policy", {}).get("review_failure") == "bounded"
            and task.get("source_reading", {}).get("reading", {}).get("unresolved")
        ):
            raise ValueError("Unclear originals require pending, not automatic corrections")
        if (
            stage == "review"
            and doc.get("processing_policy", {}).get("review_failure") == "bounded"
            and page.get("automatic_review_attempts")
        ):
            raise ValueError("Automatic review is limited to one attempt")
        markdown, corrections = result.get("markdown"), result.get("corrections")
        if not isinstance(markdown, str) or markdown == text["markdown"]:
            raise ValueError("Corrections require changed markdown")
        if not isinstance(corrections, list) or not corrections:
            raise ValueError("Corrections require uniquely located change records")
        spans = []
        for correction in corrections:
            if (
                not isinstance(correction, dict)
                or not all(
                    isinstance(correction.get(k), str) and correction[k]
                    for k in ("before", "reason")
                )
                or not isinstance(correction.get("after"), str)
            ):
                raise ValueError("Each correction requires before, after and reason")
            start = text["markdown"].find(correction["before"])
            if start < 0 or text["markdown"].find(correction["before"], start + 1) >= 0:
                raise ValueError("Correction is not uniquely located in original OCR")
            spans.append((start, start + len(correction["before"]), correction["after"]))
        spans.sort()
        if any(left[1] > right[0] for left, right in zip(spans, spans[1:])):
            raise ValueError("Correction spans overlap in original OCR")
        patched = text["markdown"]
        for start, end, replacement in reversed(spans):
            patched = patched[:start] + replacement + patched[end:]
        if patched != markdown:
            raise ValueError("Change records must explain the entire corrected markdown")
        page["candidate"] = {
            **receipt,
            "markdown": markdown,
            "sha256": digest(markdown),
            "corrections": corrections,
        }
        page["state"] = "awaiting_verification"
        page["automatic_review_attempts"] = 1
        doc["tasks"].append(new_task("verify", page["page"]))
    page["review"] = {
        **receipt,
        "verdict": verdict,
        "issues": result.get("issues", []),
        "normalizations": result.get("normalizations", []),
    }
    return result, automatic_verification


def apply_navigation(document, task, result, receipt):
    if task["inputs"].get("navigation_mode") == "verify_links":
        from hrs.processing.navigation_verification import apply_link_verifications

        return apply_link_verifications(document, task, result, receipt)
    doc = document
    task_id = task["task_id"]
    from hrs.processing.navigation import observation, validate_proposal

    for row in task["inputs"]["pages"]:
        current = doc["pages"][row["page"] - 1]
        if current.get("approved", {}).get("sha256") != row["markdown_sha256"]:
            raise ValueError("Navigation source changed during processing")
    allowed = {p["page"] for p in task["inputs"]["pages"]}
    for row in result.get("page_observations", []):
        if row.get("page") not in allowed:
            raise ValueError("Navigation observation outside its approved snapshot")
        p = doc["pages"][row["page"] - 1]
        if str(row["page"]) not in task.get("image_accesses", {}):
            raise ValueError("Navigation observations require original-image access")
        p["observation"] = observation(p, row)
    entries = validate_proposal(doc, result.get("entries"))
    if any(e["source_page"] not in allowed for e in entries):
        raise ValueError("Directory source outside the claimed snapshot")
    if any(str(e["source_page"]) not in task.get("image_accesses", {}) for e in entries):
        raise ValueError("Navigation worker must inspect directory original images")
    nav = doc.get("navigation") or {}
    nav.update(
        source_entries=entries,
        parsed_toc_sha256=task["inputs"]["toc_sha256"],
        task_id=task_id,
        receipt=receipt,
    )
    doc["navigation"] = nav


def apply_organization(document, task, result, receipt):
    doc = document
    if result.get("verdict") == "pending":
        review_verdict(result)
        doc["state"] = "pending"
    else:
        if result.get("verdict", "approved") != "approved" or result.get("issues"):
            raise ValueError("Organization with unresolved issues must remain pending")
        articles = result.get("articles")
        if not isinstance(articles, list) or not articles:
            raise ValueError("Organization requires nonempty articles")
        covered = set()
        for article in articles:
            if not isinstance(article.get("title"), str) or not article["title"].strip():
                raise ValueError("Article title is required")
            numbers = article.get("pages")
            if (
                not isinstance(numbers, list)
                or not numbers
                or any(type(p) is not int or not 1 <= p <= len(doc["pages"]) for p in numbers)
            ):
                raise ValueError("Article page mapping is invalid")
            covered.update(numbers)
            article["bibliography"] = {
                **article.get("bibliography", {}),
                "status": "unclaimed",
            }
        if covered != set(range(1, len(doc["pages"]) + 1)):
            raise ValueError("Organization must account for every physical page")
        if doc.get("processing_policy"):
            from hrs.processing.organization import article_parts

            article_parts(doc["pages"], articles)
        doc["organization"] = {
            **receipt,
            "articles": articles,
            **(
                {"reading_structure": result["reading_structure"]}
                if "reading_structure" in result
                else {}
            ),
        }
