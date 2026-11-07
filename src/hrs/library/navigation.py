"""Shared native navigation and user execution controls for the existing workbench."""

from copy import deepcopy
from functools import lru_cache
from uuid import UUID, uuid5

from hrs.library.books import get_run
from hrs.library.deletion import require_run_active
from hrs.library.errors import ServiceError
from hrs.processing.navigation import navigation_review_summary
from hrs.processing.store import Store
from hrs.processing.workspace import resolve_workspace


class Navigation:
    def __init__(self, settings, engine):
        self.settings, self.engine = settings, engine

    def native(self, run_id):
        run = get_run(self.engine, str(run_id))
        doc_id = (run["result"] or {}).get("codex_document_id")
        if not doc_id:
            if (run["result"] or {}).get("processing_backend") == "codex":
                raise ServiceError(409, "原件仍在准备中，完成后即可操作原生处理任务。")
            raise ServiceError(
                409, "旧执行链已退役，原件与历史结果保留；请将原件导入原生流程后继续操作。"
            )
        from hrs.library.native_workspace import require_workspace

        store = Store(resolve_workspace(self.settings.codex_root))
        require_workspace(self.engine, run, store)
        return store, doc_id

    def status(self, run_id):
        store, doc_id = self.native(run_id)
        result = deepcopy(
            navigation_snapshot(store.root, doc_id, str(run_id), store.checkpoints.revision(doc_id))
        )
        from hrs.library.catalog import Library

        targets = Library(self.settings, self.engine).navigation_targets(run_id, result)
        for entry in result["entries"]:
            entry["reading_target"] = targets.get(entry["id"])
            entry["target_chapter_id"] = (
                targets[entry["id"]]["chapter_id"] if entry["id"] in targets else None
            )
        return navigation_review_summary(result)

    def decide(self, run_id, entry_id, request):
        with self.engine.begin() as connection:
            require_run_active(connection, str(run_id))
        store, doc_id = self.native(run_id)
        try:
            store.navigation_decision(doc_id, entry_id, **request.model_dump())
        except (ValueError, StopIteration) as error:
            raise ServiceError(409, str(error)) from error
        return self.status(run_id)

    def refresh(self, run_id):
        """Recompute metadata and queue native link verification without granting content approval."""
        from hrs.library.errors import Problem
        from hrs.library.navigation_suggestions import supplemental_candidates
        from hrs.processing.navigation_verification import prepare_link_verification

        with self.engine.begin() as connection:
            require_run_active(connection, str(run_id))
        store, doc_id = self.native(run_id)
        snapshot = store.status(doc_id)
        store.verify_evidence(snapshot, pages=[])
        warning = None
        try:
            suggestions = supplemental_candidates(
                snapshot, store.artifact_path(snapshot, snapshot["source"]["path"]), self.settings
            )
        except (Problem, OSError, ValueError, RuntimeError):
            suggestions = {}
            warning = "本地检索暂不可用，已保留页码、标题和人工校准推荐。可稍后更新推荐。"
        with store.context.verified(doc_id) as doc:
            nav = doc.setdefault("navigation", {})
            nav.setdefault("supplemental_candidates", {}).update(suggestions)
            store.navigation.refresh_document(doc, retry_unresolved=True)
            prepare_link_verification(doc, requested=True)
            queued = any(
                t["state"] == "ready" and t.get("verification_inputs") for t in doc["tasks"]
            )
            store.checkpoints.write(doc)
        if queued:
            store.request_run(doc_id)
        return {**self.status(run_id), "assistance_warning": warning}

    def control(self, run_id, action):
        with self.engine.begin() as connection:
            require_run_active(connection, str(run_id))
        store, doc_id = self.native(run_id)
        if action == "resume":
            from sqlalchemy import or_, select

            from hrs.storage import models as db

            with self.engine.connect() as connection:
                protected = list(
                    connection.scalars(
                        select(db.review_issues.c.page).where(
                            db.review_issues.c.run_id == str(run_id),
                            or_(
                                db.review_issues.c.draft.is_not(None),
                                db.review_issues.c.state == "approved",
                            ),
                        )
                    )
                )
            store.upgrade_review_policy(doc_id, protected_pages=protected)
        result = (
            store.pause_document(doc_id)
            if action == "pause"
            else store.request_run(doc_id, resume=True)
        )
        return {"document_id": doc_id, "state": result["state"]}


@lru_cache(maxsize=8)
def navigation_snapshot(root, doc_id, run_id, version):
    """Read-only view, invalidated by every atomic native commit; never grants approval."""
    store = Store(root)
    doc = store.status(doc_id)
    nav = doc.get("navigation") or {
        "state": "not_detected",
        "entries": [],
        "page_labels": [],
        "version": 0,
        "sha256": "",
    }
    result = {k: nav.get(k) for k in ("state", "entries", "page_labels", "version", "sha256")}
    result["page_mappings"] = nav.get("page_mappings", [])
    for key in (
        "coverage_complete",
        "pages_complete",
        "toc_pages",
        "missing_pages",
        "unresolved_pages",
        "page_coverage",
    ):
        if key in nav:
            result[key] = nav[key]
    result.setdefault("coverage_complete", False)
    result["page_count"] = len(doc["pages"])

    def span_image(span):
        return {
            **span,
            "image": f"/api/v2/runs/{run_id}/artifacts/{doc['pages'][span['page'] - 1]['image']}",
        }

    def target_images(target):
        if not target:
            return target
        return {
            **target,
            **(
                {"title_spans": [span_image(s) for s in target["title_spans"]]}
                if target.get("title_spans")
                else {}
            ),
        }

    result["entries"] = [
        {
            **e,
            **(
                {"source_spans": [span_image(s) for s in e["source_spans"]]}
                if e.get("source_spans")
                else {}
            ),
            "target": target_images(e.get("target")),
            "candidates": [target_images(c) for c in e.get("candidates", [])],
            "source_image": f"/api/v2/runs/{run_id}/artifacts/{doc['pages'][e['source_page'] - 1]['image']}",
            "target_url": f"/api/v2/runs/{run_id}/artifacts/original.pdf#page={e['target']['physical_page']}"
            if e.get("target")
            else None,
            "target_image": f"/api/v2/runs/{run_id}/artifacts/{doc['pages'][e['target']['physical_page'] - 1]['image']}"
            if e.get("target")
            else None,
        }
        for e in result["entries"]
    ]
    if doc.get("publication") and doc.get("organization"):
        from hrs.processing.organization import article_parts

        groups = article_parts(doc["pages"], doc["organization"]["articles"])
        for entry in result["entries"]:
            target = entry.get("target")
            if target:
                owner = next(
                    (
                        i
                        for i, parts in enumerate(groups)
                        if any(
                            p["page"] == target["physical_page"]
                            and p["original_start"] <= target["start"] < p["original_end"]
                            for p in parts
                        )
                    ),
                    None,
                )
                if owner is not None:
                    entry["target_chapter_id"] = str(uuid5(UUID(str(run_id)), f"chapter:{owner}"))
    from hrs.processing.navigation_matching import target_catalog

    titles = {}
    for row in target_catalog(doc):
        target = row["target"]
        titles.setdefault(target["physical_page"], []).append(
            target_images(
                {
                    **target,
                    "title": target.get("title") or target["anchor"],
                }
            )
        )
    result["approved_pages"] = [
        {
            "page": p["page"],
            "headings": p.get("observation", {}).get("headings", []),
            "titles": titles.get(p["page"], []),
            "printed_label": p.get("observation", {}).get("printed_label"),
            "image": f"/api/v2/runs/{run_id}/artifacts/{p['image']}",
        }
        for p in doc["pages"]
        if p.get("approved")
    ]
    return result
