"""Repair legacy review scopes without changing OCR or overwriting human decisions."""

import json
from collections import defaultdict
from uuid import UUID, uuid5

from fastapi import HTTPException
from hrs_runtime.review_scope import concern_ranges, figure_page, locate, split_change
from sqlalchemy import func, insert, select, update

from hrs_platform import models as db
from hrs_platform.schemas import ReviewDecision
from hrs_platform.services.books import get_run
from hrs_platform.services.review import digest


def context(review, run_id):
    run = get_run(review.engine, run_id)
    if run["stage"] != "review" or (run["result"] or {}).get("published"):
        raise ValueError("Only an unpublished book awaiting review can be repaired.")
    bundle = review.read_json(run["conversion"])
    files, manifest = bundle["files"], bundle["manifest"]
    text = review.objects.read_bytes(files[manifest["candidate_markdown"]]).decode("utf-8")
    boundaries = {p["page"]: p for p in review.read_json(files[manifest["page_boundaries"]])["pages"]}
    return run, bundle, text, boundaries


def machine_decision(review, issue, value, evidence):
    if issue.get("draft_text") is not None or issue["state"] != "pending":
        return False
    changed = value != issue["text"]
    if changed and not value:
        return False
    request = ReviewDecision(
        decision_id=uuid5(
            UUID(str(issue["id"])),
            "machine:" + digest(json.dumps(evidence, sort_keys=True)) + ":" + digest(value),
        ),
        expected_revision=issue["revision"],
        expected_text_sha256=issue["text_sha256"],
        action="correct" if changed else "confirm",
        text=value if changed else None,
    )
    try:
        review._decide(issue["id"], request, machine_evidence=evidence)
        return True
    except HTTPException as error:
        if error.status_code != 409:
            raise
        return False


def repair_scopes(review, run_id):
    run, bundle, text, boundaries = context(review, run_id)
    completion = review.read_json(bundle["files"]["completion.json"])
    stats = {"unrelated_closed": 0, "figure_items_consolidated": 0, "page_items_consolidated": 0}
    by_page = defaultdict(list)
    for row in review.list(run_id):
        by_page[row["page"]].append(row["id"])
    for page in completion["pages"]:
        issues = [review.get(identity) for identity in by_page[page["page"]]]
        issues = [issue for issue in issues if issue["state"] == "pending"]
        if not issues or any(i["draft_text"] is not None or len(i["pages"]) != 1 for i in issues):
            continue
        boundary = boundaries[page["page"]]
        raw = text[boundary["start"] : boundary["end"]]
        is_figure = figure_page(raw)
        unlocated = any(
            c["kind"] not in {"citation", "normalization"} and locate(raw, c.get("excerpt", "")) is None
            for c in page["concerns"]
        )
        if (is_figure or unlocated) and len(issues) > 1:
            with review.engine.connect() as connection:
                all_rows = (
                    connection.execute(
                        select(db.review_issues).where(
                            db.review_issues.c.run_id == run_id, db.review_issues.c.page == page["page"]
                        )
                    )
                    .mappings()
                    .all()
                )
            if any(r["state"] != "pending" or r["draft"] is not None for r in all_rows):
                continue
            body = {
                "kind": "figure" if is_figure else "page",
                "text": raw,
                "start": boundary["start"],
                "end": boundary["end"],
                "pages": [page["page"]],
                "images": [
                    next(p["image"] for p in bundle["manifest"]["pages"] if p["page"] == page["page"])
                ],
                "reasons": [
                    "本页为插图/地图，请核对图像与图说。图内标签保留在原图，无需逐个转成正文。",
                    "原图核验尚未完成；Image 是导出占位标记，不是原书文字。",
                ]
                if is_figure
                else [
                    "模型未能将问题可靠定位到具体段落，已合并为一个页级待办；不代表每段都有错误。",
                    *dict.fromkeys(
                        c["explanation"]
                        for c in page["concerns"]
                        if c["kind"] not in {"citation", "normalization"}
                    ),
                ],
                "previous_scopes": [{"id": str(r["id"]), "content": r["content"]} for r in all_rows],
            }
            ref = review.objects.put_bytes(json.dumps(body, ensure_ascii=False).encode(), run_id=run_id)
            with review.engine.begin() as connection:
                current = (
                    connection.execute(select(db.runs).where(db.runs.c.id == run_id).with_for_update())
                    .mappings()
                    .one()
                )
                rows = (
                    connection.execute(
                        select(db.review_issues)
                        .where(db.review_issues.c.run_id == run_id, db.review_issues.c.page == page["page"])
                        .with_for_update()
                    )
                    .mappings()
                    .all()
                )
                if any(r["state"] != "pending" or r["draft"] is not None for r in rows) or {
                    str(r["id"]): r["revision"] for r in rows
                } != {str(r["id"]): r["revision"] for r in all_rows}:
                    continue
                keep = str(issues[0]["id"])
                connection.execute(
                    update(db.review_issues)
                    .where(db.review_issues.c.id == keep)
                    .values(content=ref, text_sha256=digest(raw), revision=db.review_issues.c.revision + 1)
                )
                connection.execute(
                    update(db.review_issues)
                    .where(
                        db.review_issues.c.run_id == run_id,
                        db.review_issues.c.page == page["page"],
                        db.review_issues.c.id != keep,
                    )
                    .values(state="superseded", revision=db.review_issues.c.revision + 1)
                )
                count = connection.scalar(
                    select(func.count())
                    .select_from(db.review_issues)
                    .where(db.review_issues.c.run_id == run_id, db.review_issues.c.state == "pending")
                )
                revision = current["revision"] + 1
                connection.execute(
                    update(db.runs)
                    .where(db.runs.c.id == run_id)
                    .values(pending_count=count, revision=revision)
                )
                connection.execute(
                    insert(db.events).values(
                        book_id=run["book_id"],
                        run_id=run_id,
                        kind="run.changed",
                        payload={
                            "revision": revision,
                            "pending_count": count,
                            "stage": "review",
                            "state": "awaiting_review",
                        },
                    )
                )
                stats["figure_items_consolidated" if is_figure else "page_items_consolidated"] += (
                    len(rows) - 1
                )
            continue
        if not page.get("receipts") or page["receipts"][-1]["verdict"].get("review_state") != "completed":
            continue
        concerns = [c for c in page["concerns"] if c["kind"] not in {"normalization", "citation"}]
        ranges = [locate(raw, c.get("excerpt", "")) for c in concerns]
        if not ranges or any(r is None for r in ranges):
            continue
        evidence = {
            "kind": "normalized-scope-repair",
            "conversion_sha256": run["conversion"]["sha256"],
            "page": page["page"],
            "original_receipt_sha256": digest(
                json.dumps(page["receipts"][-1], sort_keys=True, ensure_ascii=False)
            ),
            "affected_ranges": ranges,
            "machine_review": True,
            "human_review": False,
        }
        for issue in issues:
            a, b = issue["start"] - boundary["start"], issue["end"] - boundary["start"]
            if not any(a < end and b > start for start, end in ranges):
                stats["unrelated_closed"] += machine_decision(review, issue, issue["text"], evidence)
    return stats


def apply_verified_page(review, issues, page, boundary, evidence):
    """Adopt only verified edits wholly owned by still-pending, unchanged scopes."""
    if not page.get("verified"):
        return 0
    changes = [edit for change in page.get("changes", []) for edit in split_change(change)]
    approved = 0
    for issue in issues:
        start, end = issue["start"] - boundary["start"], issue["end"] - boundary["start"]
        overlap = [
            c
            for c in changes
            if c["start_before"] <= end
            and c["end_before"] >= start
            and (c["start_before"] == c["end_before"] or c["start_before"] < end and c["end_before"] > start)
        ]
        if any(c["start_before"] < start or c["end_before"] > end for c in overlap):
            continue
        if any(c["start_before"] == c["end_before"] and c["start_before"] in {start, end} for c in overlap):
            continue  # Boundary insertion has no unique owner; do not duplicate it into adjacent issues.
        value = issue["text"]
        # Immutable conversion offsets, not offsets after earlier replacements.
        for change in sorted(overlap, key=lambda c: c["start_before"], reverse=True):
            a, b = change["start_before"] - start, change["end_before"] - start
            if value[a:b] != change["before"]:
                break
            value = value[:a] + change["after"] + value[b:]
        else:
            approved += machine_decision(review, issue, value, evidence)
    return approved


def refresh_unresolved(review, issues, page, boundary, evidence):
    """Keep current model concerns attached to their actual paragraphs."""
    initial_checks = [r for r in page['receipts'] if r.get('phase', 'initial') in {'initial', 'repair-review'}]
    if not initial_checks or initial_checks[-1]['verdict'].get('review_state') != 'completed':
        return 0
    concerns = [c for c in page["concerns"] if c["kind"] not in {"citation", "normalization"}]
    groups = [(concern_ranges(page["text"], c), c) for c in concerns]
    if not groups or any(scopes is None for scopes, _ in groups):
        return 0
    localized = [(scope, c) for scopes, c in groups for scope in scopes]
    approved = 0
    for issue in issues:
        start, end = issue["start"] - boundary["start"], issue["end"] - boundary["start"]
        matching = [c for (a, b), c in localized if start < b and end > a]
        if not matching:
            approved += machine_decision(review, issue, issue["text"], evidence)
            continue
        body = review.read_json(issue["content"])
        updated = {
            **body,
            "reasons": list(dict.fromkeys(c["explanation"] for c in matching)),
            "machine_recheck": evidence,
            "previous_content": issue["content"],
        }
        if updated["reasons"] == body["reasons"]:
            continue
        ref = review.objects.put_bytes(json.dumps(updated, ensure_ascii=False).encode(), run_id=issue["run_id"])
        with review.engine.begin() as connection:
            connection.execute(
                update(db.review_issues)
                .where(
                    db.review_issues.c.id == str(issue["id"]),
                    db.review_issues.c.state == "pending",
                    db.review_issues.c.draft.is_(None),
                    db.review_issues.c.revision == issue["revision"],
                )
                .values(content=ref, revision=issue["revision"] + 1)
            )
    return approved


def recheck_pending(review, run_id, settings, output, *, pages=None, progress=print):
    """Reuse conversion review for existing pending pages, without rerunning OCR."""
    from pathlib import Path

    from document_extraction.semantic_completion import complete_document

    run, bundle, text, boundaries = context(review, run_id)
    output = Path(output)
    by_page = defaultdict(list)
    for row in review.list(run_id):
        if pages is None or row["page"] in pages:
            by_page[row["page"]].append(row["id"])
    source_pages = [
        {
            **p,
            "text": text[boundaries[p["page"]]["start"] : boundaries[p["page"]]["end"]],
            "image_path": output / p["image"],
            "changes": [],
            "concerns": [],
            "receipts": [],
            "verified": False,
        }
        for p in bundle["manifest"]["pages"]
    ]
    stats = {"pages_checked": 0, "issues_machine_approved": 0, "pages_pending": 0}
    for number, identities in sorted(by_page.items()):
        issues = [review.get(identity) for identity in identities]
        issues = [
            i
            for i in issues
            if i["state"] == "pending" and i["draft_text"] is None and i["pages"] == [number]
        ]
        if not issues:
            continue
        target = next(p for p in source_pages if p['page'] == number)
        target['review_scope'] = [{'text': i['text'], 'start':i['start']-boundaries[number]['start'],
                                  'end':i['end']-boundaries[number]['start'],
                                  'reasons': review.read_json(i['content']).get('reasons', [])}
                                  for i in issues]
        for p in source_pages:
            if abs(p["page"] - number) <= 1:
                review.objects.materialize(bundle["files"][p["image"]], p["image_path"])
        checked = complete_document(
            source_pages, settings, output / f"page-{number:03d}", target_pages={number}
        )
        page = next(p for p in checked["pages"] if p["page"] == number)
        current = get_run(review.engine, run_id)
        if current['conversion'] != run['conversion']:
            raise ValueError('The formal conversion changed during review; retained results were not applied.')
        receipt = {
            "page": number,
            "conversion_sha256": run["conversion"]["sha256"],
            "receipts": page["receipts"],
            "changes": page["changes"],
            "concerns": page["concerns"],
            "verified": page["verified"],
            "machine_review": True,
            "human_review": False,
        }
        saved = review.objects.put_bytes(json.dumps(receipt, ensure_ascii=False).encode(), run_id=run_id)
        approved = apply_verified_page(
            review, issues, page, boundaries[number], {"kind": "local-original-recheck", "review": saved}
        )
        if not page["verified"]:
            approved += refresh_unresolved(
                review, issues, page, boundaries[number], {"kind": "local-original-recheck", "review": saved}
            )
        stats["pages_checked"] += 1
        stats["issues_machine_approved"] += approved
        stats["pages_pending"] += not page["verified"]
        progress(
            json.dumps(
                {
                    "page": number,
                    "verified": page["verified"],
                    "approved": approved,
                    "remaining_pages": len(by_page) - stats["pages_checked"],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    return stats
