"""Publish reviewed source as coherent chapters with physical-page provenance."""

from uuid import UUID, uuid5

from sqlalchemy import select

from hrs.library.books import get_run
from hrs.library.chapter_content import chapter_content
from hrs.library.errors import ServiceError
from hrs.library.review import Review, digest
from hrs.library.runs.outputs import Outputs, fingerprint
from hrs.library.structure import POLICY as STRUCTURE_POLICY
from hrs.library.structure import describe_structure
from hrs.storage import models as db


def reviewed_spans(markdown, pages, corrections, run_id):
    """Apply explicit human revisions while retaining their original-page ranges."""
    corrections = sorted(corrections, key=lambda row: row["start"])
    if any(a["end"] > b["start"] for a, b in zip(corrections, corrections[1:])):
        raise ValueError("Overlapping human revisions require conflict resolution.")
    cuts = sorted({point for row in [*pages, *corrections] for point in (row["start"], row["end"])})
    result, offset = [], 0
    for start, end in zip(cuts, cuts[1:]):
        edit = next((row for row in corrections if row["start"] <= start < row["end"]), None)
        if edit and start != edit["start"]:
            continue
        if edit:
            end = edit["end"]
        original_pages = [
            page["page"] for page in pages if page["start"] < end and page["end"] > start
        ]
        if not original_pages:
            if result and not markdown[start:end].strip():
                gap = markdown[start:end]
                result[-1]["selected_text"] += gap
                result[-1]["end_offset"] += len(gap)
                result[-1]["text_sha256"] = digest(result[-1]["selected_text"])
                offset += len(gap)
            continue
        value = edit["text"] if edit else markdown[start:end]
        if not value:
            continue
        # Page boundaries supply provenance; they never imply a chapter boundary.
        result.append(
            {
                "id": str(uuid5(UUID(run_id), f"span:{start}:{end}")),
                "start_offset": offset,
                "end_offset": offset + len(value),
                "selected_text": value,
                "text_sha256": digest(value),
                "source_record": {
                    "page": original_pages[0],
                    "pages": original_pages,
                    "original_start": start,
                    "original_end": end,
                    "human_decision_id": edit.get("decision_id")
                    if edit and edit.get("reviewer", "human-ui") == "human-ui"
                    else None,
                    "machine_decision_id": edit.get("decision_id")
                    if edit and edit.get("reviewer", "human-ui") != "human-ui"
                    else None,
                },
            }
        )
        offset += len(value)
    return result


class Library:
    def __init__(self, settings, engine):
        self.settings, self.engine = settings, engine
        self.outputs = Outputs(settings, engine)
        self.review = Review(settings, engine)

    def prepare(self, run_id):
        cached = self.outputs.get(run_id, "reviewed-source")
        if cached is not None:
            return cached
        status = self.review.status(run_id)
        if status["pending_count"]:
            raise ValueError("Human review must finish before ingestion or partitioning.")
        run = get_run(self.engine, run_id)
        if (run["result"] or {}).get("codex_document_id") and not (run["result"] or {}).get(
            "published"
        ):
            raise ValueError(
                "Native Codex processing has not completed its whole-document publication gate."
            )
        bundle = self.review.read_json(run["conversion"])
        spans = self._source_spans(run_id, bundle)
        if not spans:
            raise ValueError("No usable source text; the source remains preserved.")
        return self.outputs.put(
            run_id,
            "reviewed-source",
            {
                "spans": spans,
                "source_sha256": run["source"]["sha256"],
                "review_revision": status["revision"],
                "page_count": bundle["manifest"]["page_count"],
            },
            {"conversion": run["conversion"]["sha256"], "review_revision": status["revision"]},
        )

    def _source_spans(self, run_id, bundle):
        files, manifest = bundle["files"], bundle["manifest"]
        markdown = self.review.markdown(bundle)
        pages = self.review.read_json(files[manifest["page_boundaries"]])["pages"]
        with self.engine.connect() as connection:
            rows = (
                connection.execute(
                    select(
                        db.review_issues,
                        db.review_decisions.c.id.label("decision_id"),
                        db.review_decisions.c.receipt.label("decision_receipt"),
                    )
                    .join(
                        db.review_decisions, db.review_decisions.c.issue_id == db.review_issues.c.id
                    )
                    .where(
                        db.review_issues.c.run_id == run_id,
                        db.review_issues.c.replacement.is_not(None),
                    )
                )
                .mappings()
                .all()
            )
        corrections = []
        for row in rows:
            # JSON null passes SQL IS NOT NULL; unchanged confirmations have no replacement.
            if row["replacement"] is None:
                continue
            body = self.review.read_json(row["content"])
            corrections.append(
                {
                    "start": body["start"],
                    "end": body["end"],
                    "decision_id": row["decision_id"],
                    "reviewer": row["decision_receipt"].get("reviewer", "human-ui"),
                    "text": self.review.objects.read_bytes(row["replacement"]).decode("utf-8"),
                }
            )
        return reviewed_spans(markdown, pages, corrections, run_id)

    def structure(self, run_id):
        """Reuse retained structure and current decisions; no OCR or model calls."""
        run = get_run(self.engine, run_id)
        status = self.review.status(run_id)
        dependency = {
            "policy": STRUCTURE_POLICY,
            "conversion": run["conversion"],
            "review_revision": status["revision"],
        }
        key = "reading-structure:" + fingerprint(dependency)
        if run["conversion"]:
            cached = self.outputs.get(run_id, key, dependency)
            if cached is not None:
                return cached
            bundle = self.review.read_json(run["conversion"])
            files = bundle["files"]
            spans = self._source_spans(run_id, bundle)
            parts = [
                {
                    "span_id": s["id"],
                    "start": s["start_offset"],
                    "end": s["end_offset"],
                    "text": s["selected_text"],
                    "source": s["source_record"],
                }
                for s in spans
            ]
            completion = (
                self.review.read_json(files["completion.json"])["pages"]
                if "completion.json" in files
                else []
            )
            pages = {p["page"] for p in bundle["manifest"]["pages"]}
            if (run["result"] or {}).get("codex_document_id"):
                approved = {
                    p["page"]
                    for p in bundle["manifest"]["pages"]
                    if p["status"] == "release-accepted"
                }
            elif (run["result"] or {}).get("review_initialized"):
                with self.engine.connect() as connection:
                    pending = (
                        connection.execute(
                            select(db.review_issues.c.page, db.review_issues.c.content).where(
                                db.review_issues.c.run_id == run_id,
                                db.review_issues.c.state == "pending",
                            )
                        )
                        .mappings()
                        .all()
                    )
                unresolved = {
                    p
                    for row in pending
                    for p in self.review.read_json(row["content"]).get("pages", [row["page"]])
                }
                approved = pages - unresolved
            else:
                approved = {
                    p["page"]
                    for p in bundle["manifest"]["pages"]
                    if p["status"] == "release-accepted"
                }
        else:
            draft = self.review.ocr_draft(run)
            if draft is None:
                return {"policy": STRUCTURE_POLICY, "available": False, "items": []}
            files, checkpoint = draft
            parts = [
                {"text": p["text"] + "\n\n", "source": {"pages": [p["page"]]}}
                for p in checkpoint["pages"]
            ]
            completion, approved = [], set()
        # Older immutable publications retain the former artifact name.
        structure_file = files.get("reading-structure.json") or files.get("paddle-restructure.json")
        metadata = self.review.read_json(structure_file) if structure_file else {}
        document = {"text": "".join(p["text"] for p in parts), "parts": parts}
        report = describe_structure(document, metadata, completion, approved)
        return self.outputs.put(run_id, key, report, dependency) if run["conversion"] else report

    def chapters(self, book_id):
        with self.engine.connect() as connection:
            return list(
                connection.execute(
                    select(db.chapters)
                    .where(db.chapters.c.book_id == str(book_id))
                    .order_by(db.chapters.c.position)
                ).mappings()
            )

    def amend(self, run_id, amendments):
        """Apply located, same-length errata after local original-image verification.

        This narrow operation preserves all existing coordinates and human decisions.
        Larger edits require a new source revision and repartitioning, not offset guessing.
        """
        run = get_run(self.engine, run_id)
        if not run["result"].get("published") or run["pending_count"]:
            raise ValueError("Errata require a published book with no pending review.")
        if run["result"].get("codex_document_id"):
            from hrs.processing.store import Store
            from hrs.processing.workspace import resolve_workspace

            store = Store(resolve_workspace(self.settings.codex_root))
            changes = []
            for request in amendments:
                chapter = self.chapter(request["chapter_id"])
                if str(chapter["run_id"]) != str(run_id):
                    raise ValueError("Errata belong to another run")
                for edit in request["changes"]:
                    start, before, after = edit["start"], edit["before"], edit["after"]
                    if (
                        not before
                        or len(before) != len(after)
                        or chapter["text"][start : start + len(before)] != before
                    ):
                        raise ValueError("Errata must match a nonempty same-length source range.")
                    offset = 0
                    for part in chapter["parts"]:
                        end = offset + len(part["text"])
                        if (
                            offset <= start < start + len(before) <= end
                            and len(part["source"]["pages"]) == 1
                        ):
                            changes.append(
                                {
                                    "page": part["source"]["pages"][0],
                                    "start": part["source"]["original_start"] + start - offset,
                                    "before": before,
                                    "after": after,
                                }
                            )
                            break
                        offset = end
                    else:
                        raise ValueError("Erratum crosses source ownership boundaries.")
            return store.propose_revision(run["result"]["codex_document_id"], changes)
        raise ServiceError(409, "旧资料请先导入原生处理流程，再提交勘误修订。")

    def chapter(self, chapter_id):
        with self.engine.connect() as connection:
            row = (
                connection.execute(select(db.chapters).where(db.chapters.c.id == str(chapter_id)))
                .mappings()
                .one_or_none()
            )
        if not row:
            raise ServiceError(404, "章节尚未发布。")
        chapter = {**row, **chapter_content(self.settings, row["content"])}
        from hrs.library.reading_locations import toc_links

        navigation = chapter.get("navigation", {})
        if any(e.get("source_page") in chapter["pages"] for e in navigation.get("entries", [])):
            targets = self.navigation_targets(row["run_id"], navigation)
            chapter["toc_links"] = toc_links(chapter, targets)
        else:
            chapter["toc_links"] = []
        return chapter

    def navigation_targets(self, run_id, navigation):
        """Resolve only targets supported by the currently published chapter objects."""
        from hrs.library.reading_locations import target_in_chapter

        entries = [
            e
            for e in navigation.get("entries", [])
            if e.get("status") in {"matched", "matched_manual", "matched_verified"}
            and e.get("target")
        ]
        if not entries:
            return {}
        with self.engine.connect() as connection:
            rows = (
                connection.execute(select(db.chapters).where(db.chapters.c.run_id == str(run_id)))
                .mappings()
                .all()
            )
        pages = {e["target"]["physical_page"] for e in entries}
        chapters = [
            {**row, **chapter_content(self.settings, row["content"])}
            for row in rows
            if pages.intersection(row["pages"])
        ]
        by_page = {}
        for chapter in chapters:
            for page in chapter["pages"]:
                by_page.setdefault(page, []).append(chapter)
        targets = {}
        for entry in entries:
            matches = [
                target
                for chapter in by_page.get(entry["target"]["physical_page"], [])
                if (target := target_in_chapter(chapter, entry["target"]))
            ]
            if len(matches) == 1:
                targets[entry["id"]] = matches[0]
        return targets

    def reading_navigation(self, book_id):
        """Read the navigation frozen with the visible publication, including during revision."""
        from copy import deepcopy

        from hrs.processing.navigation import navigation_review_summary

        rows = self.chapters(book_id)
        empty = {
            "state": "not_published",
            "entries": [],
            "version": 0,
            "sha256": "",
            "pending_count": 0,
            "coverage_complete": False,
            "page_labels": [],
            "approved_pages": [],
        }
        if not rows:
            return empty
        run_ids = {str(row["run_id"]) for row in rows}
        if len(run_ids) != 1:
            # Mixed generations cannot define a reliable book-wide navigation map.
            return {**empty, "state": "unavailable"}
        run_id = next(iter(run_ids))
        navigation = deepcopy(
            chapter_content(self.settings, rows[0]["content"]).get("navigation", {})
        )
        targets = self.navigation_targets(run_id, navigation)
        run = get_run(self.engine, run_id)
        images = {}
        if run["conversion"]:
            bundle = self.review.read_json(run["conversion"])
            images = {page["page"]: page["image"] for page in bundle["manifest"]["pages"]}
        for entry in navigation.get("entries", []):
            source_page = entry["source_page"]
            entry["source_image"] = (
                f"/api/v2/runs/{run_id}/artifacts/{images[source_page]}"
                if source_page in images
                else ""
            )
            target = entry.get("target")
            page = target["physical_page"] if target else None
            entry["target_image"] = (
                f"/api/v2/runs/{run_id}/artifacts/{images[page]}" if page in images else None
            )
            entry["target_url"] = (
                f"/api/v2/runs/{run_id}/artifacts/original.pdf#page={page}" if page else None
            )
            entry["reading_target"] = targets.get(entry["id"])
            entry["target_chapter_id"] = (
                targets[entry["id"]]["chapter_id"] if entry["id"] in targets else None
            )
        return navigation_review_summary(
            {
                **empty,
                **navigation,
                "entries": navigation.get("entries", []),
                "page_count": len(images) or None,
            }
        )
