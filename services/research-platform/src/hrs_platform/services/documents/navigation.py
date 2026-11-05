"""Shared native navigation and user execution controls for the existing workbench."""

from copy import deepcopy
from functools import lru_cache
from uuid import UUID, uuid5

from hrs_codex.store import Store
from hrs_codex.workspace import resolve_workspace

from hrs_platform.domain.errors import ServiceError
from hrs_platform.services.books import get_run
from hrs_platform.services.deletion import require_run_active


class Navigation:
    def __init__(self, settings, engine):
        self.settings, self.engine = settings, engine

    def native(self, run_id):
        run = get_run(self.engine, str(run_id))
        doc_id = (run["result"] or {}).get("codex_document_id")
        if not doc_id:
            raise ServiceError(409, "原生处理任务尚未准备好。")
        return Store(resolve_workspace(self.settings.codex_root)), doc_id

    def status(self, run_id):
        store, doc_id = self.native(run_id)
        stat = (store.document_directory(doc_id) / "document.json").stat()
        return deepcopy(
            navigation_snapshot(
                store.root, doc_id, str(run_id), (stat.st_ino, stat.st_mtime_ns, stat.st_size)
            )
        )

    def decide(self, run_id, entry_id, request):
        with self.engine.begin() as connection:
            require_run_active(connection, str(run_id))
        store, doc_id = self.native(run_id)
        try:
            store.navigation_decision(doc_id, entry_id, **request.model_dump())
        except (ValueError, StopIteration) as error:
            raise ServiceError(409, str(error)) from error
        return self.status(run_id)

    def control(self, run_id, action):
        with self.engine.begin() as connection:
            require_run_active(connection, str(run_id))
        store, doc_id = self.native(run_id)
        if action == "resume":
            from sqlalchemy import or_, select

            from hrs_platform import models as db

            with self.engine.connect() as connection:
                protected = list(
                    connection.scalars(
                        select(db.review_issues.c.page).where(
                            db.review_issues.c.run_id == str(run_id),
                            or_(
                                db.review_issues.c.draft.is_not(None), db.review_issues.c.state == "approved"
                            ),
                        )
                    )
                )
            store.upgrade_review_policy(doc_id, protected_pages=protected)
        result = store.pause_document(doc_id) if action == "pause" else store.request_run(doc_id, resume=True)
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
    result["coverage_complete"] = bool(nav.get("coverage_complete"))
    result["page_count"] = len(doc["pages"])
    result["entries"] = [
        {
            **e,
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
        from hrs_codex.organization import article_parts

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
    result["approved_pages"] = [
        {
            "page": p["page"],
            "headings": p.get("observation", {}).get("headings", []),
            "printed_label": p.get("observation", {}).get("printed_label"),
            "image": f"/api/v2/runs/{run_id}/artifacts/{p['image']}",
        }
        for p in doc["pages"]
        if p.get("approved")
    ]
    return result
