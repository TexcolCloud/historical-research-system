"""Source-bound local decisions; a click never rebuilds the book."""

import json
from uuid import UUID, uuid5

from sqlalchemy import func, insert, select, update

from hrs.library.errors import ServiceError
from hrs.library.footnotes import resolve_footnotes
from hrs.library.object_ownership import owned_objects
from hrs.library.records import text_digest as digest
from hrs.processing.acceptance import decision_scope as resolve_decision_scope
from hrs.processing.workspace import resolve_workspace
from hrs.storage import models as db


class Review:
    def __init__(self, settings, engine):
        self.settings, self.engine = settings, engine
        self.objects = owned_objects(settings, engine)

    def read_json(self, reference):
        return json.loads(self.objects.read_bytes(reference))

    def read_cached(self, reference):
        from hrs.storage.service import cached_bytes

        return cached_bytes(self.objects, reference)

    def ocr_draft(self, run):
        from hrs.library.runs.outputs import Outputs

        saved = Outputs(self.settings, self.engine).get(run["id"], "ocr-evidence")
        if saved is None:
            return None
        files = saved["files"]
        checkpoint = json.loads(self.read_cached(files["ocr-checkpoint.json"]))
        if checkpoint["source_sha256"] != (run["source"] or {}).get("sha256"):
            raise ServiceError(409, "识别底稿与当前原件不符，请恢复文档转换。")
        return files, checkpoint

    def page(self, run_id, page):
        """Read the immutable conversion draft without opening the ingestion gate."""
        from hrs.library.books import get_run

        run = get_run(self.engine, run_id)
        if not run["conversion"]:
            draft = self.ocr_draft(run)
            if draft is None:
                raise ServiceError(409, "识别底稿尚未保存，请等待 OCR 完成。")
            _, checkpoint = draft
            original = next((row for row in checkpoint["pages"] if row["page"] == page), None)
            if original is None:
                raise ServiceError(404, "原件页码超出范围。")
            return {
                "page": page,
                "page_count": len(checkpoint["pages"]),
                "text": original["text"],
                "image": f"/api/v2/runs/{run_id}/artifacts/{original['image_path']}",
                "machine_status": "unreviewed-source",
                "extraction_method": original.get("extraction_method", "ocr"),
                "footnotes": resolve_footnotes(
                    original["text"], [{"text": original["text"], "source": {"pages": [page]}}]
                ),
            }

        read = self.read_cached
        bundle = json.loads(read(run["conversion"]))
        files, manifest = bundle["files"], bundle["manifest"]
        if not 1 <= page <= manifest["page_count"]:
            raise ServiceError(404, "原件页码超出范围。")
        original = next(row for row in manifest["pages"] if row["page"] == page)
        if manifest.get("page_markdown"):
            text = read(files[original["markdown"]]).decode("utf-8")
        else:
            boundary = next(
                row
                for row in json.loads(read(files[manifest["page_boundaries"]]))["pages"]
                if row["page"] == page
            )
            text = read(files[manifest["candidate_markdown"]]).decode("utf-8")[
                boundary["start"] : boundary["end"]
            ]
        return {
            "page": page,
            "page_count": manifest["page_count"],
            "text": text,
            "image": f"/api/v2/runs/{run_id}/artifacts/{original['image']}",
            "machine_status": original["status"],
            "extraction_method": original.get("extraction_method", "ocr"),
            "acceptance_basis": original.get("acceptance_basis"),
            "footnotes": resolve_footnotes(
                text,
                [{"text": text, "source": {"pages": [page]}}],
            ),
        }

    def markdown(self, bundle):
        """Portable whole-book Markdown is assembled only when requested or published."""
        files, manifest = bundle["files"], bundle["manifest"]
        if manifest.get("page_markdown") and manifest["candidate_markdown"] not in files:
            return "".join(
                self.read_cached(files[p["markdown"]]).decode("utf-8") for p in manifest["pages"]
            )
        return self.objects.read_bytes(files[manifest["candidate_markdown"]]).decode("utf-8")

    def initialize(self, run_id):
        from hrs.library.books import get_run

        run = get_run(self.engine, run_id)
        if (run["result"] or {}).get("review_initialized"):
            return self.status(run_id)
        if not run["conversion"]:
            raise ValueError("Conversion evidence is required before review.")
        bundle = self.read_json(run["conversion"])
        files, manifest = bundle["files"], bundle["manifest"]
        markdown = self.markdown(bundle)
        cards = self.read_json(files[manifest["review_cards"]])["cards"]
        by_scope = {}
        covered_pages = set()
        for card in cards:
            region = card["region"]
            scope = region.get("block_id") or f"page-{card['page']}"
            if scope not in by_scope:
                by_scope[scope] = {
                    "kind": region["kind"],
                    "text": region.get("text", ""),
                    "start": region.get("start"),
                    "end": region.get("end"),
                    "pages": [],
                    "images": [],
                    "reasons": [],
                }
            value = by_scope[scope]
            value["pages"].append(card["page"])
            value["images"].append(card["page_image"])
            value["reasons"].extend(card["reasons"])
            covered_pages.add(card["page"])
        # Failed/unreviewed pages must remain visible even if there is no localized block.
        boundaries = {
            row["page"]: row for row in self.read_json(files[manifest["page_boundaries"]])["pages"]
        }
        for page in manifest["pages"]:
            if page["status"] == "release-accepted" or page["page"] in covered_pages:
                continue
            scope = f"page-{page['page']}"
            boundary = boundaries[page["page"]]
            by_scope[scope] = {
                "kind": "page",
                "text": markdown[boundary["start"] : boundary["end"]],
                "start": boundary["start"],
                "end": boundary["end"],
                "pages": [page["page"]],
                "images": [page["image"]],
                "reasons": ["本页尚未获得有效核验结果，请对照原件确认。"],
            }
        rows = []
        for scope, value in by_scope.items():
            for name in ("pages", "images", "reasons"):
                value[name] = list(dict.fromkeys(value[name]))
            if value["start"] is None:
                boundary = boundaries[value["pages"][0]]
                value.update(
                    start=boundary["start"],
                    end=boundary["end"],
                    text=markdown[boundary["start"] : boundary["end"]],
                )
            rows.append(
                {
                    "id": str(uuid5(UUID(run_id), scope)),
                    "run_id": run_id,
                    "scope_key": scope,
                    "page": min(value["pages"]),
                    "text_sha256": digest(value["text"]),
                    "content": self.objects.put_bytes(
                        json.dumps(value, ensure_ascii=False).encode("utf-8"), run_id=run_id
                    ),
                }
            )
        with self.engine.begin() as connection:
            from hrs.library.deletion import require_active

            require_active(connection, run["book_id"])
            current = (
                connection.execute(select(db.runs).where(db.runs.c.id == run_id).with_for_update())
                .mappings()
                .one()
            )
            if (current["result"] or {}).get("review_initialized"):
                return {"pending_count": current["pending_count"], "revision": current["revision"]}
            if rows:
                connection.execute(insert(db.review_issues), rows)
            state = "awaiting_review" if rows else "ready_for_ingestion"
            revision = current["revision"] + 1
            connection.execute(
                update(db.runs)
                .where(db.runs.c.id == run_id)
                .values(
                    pending_count=len(rows),
                    state=state,
                    stage="review",
                    revision=revision,
                    result={**(current["result"] or {}), "review_initialized": True},
                    updated_at=func.now(),
                )
            )
            connection.execute(
                update(db.books).where(db.books.c.id == run["book_id"]).values(state=state)
            )
            connection.execute(
                insert(db.events).values(
                    book_id=run["book_id"],
                    run_id=run_id,
                    kind="run.changed",
                    payload={
                        "state": state,
                        "stage": "review",
                        "revision": revision,
                        "pending_count": len(rows),
                    },
                )
            )
        return {"pending_count": len(rows), "revision": revision}

    def status(self, run_id):
        with self.engine.connect() as connection:
            run = (
                connection.execute(
                    select(db.runs.c.pending_count, db.runs.c.revision, db.runs.c.result).where(
                        db.runs.c.id == run_id
                    )
                )
                .mappings()
                .one()
            )
            if not (run["result"] or {}).get("review_initialized"):
                raise ValueError("Review initialization has not completed.")
            return {"pending_count": run["pending_count"], "revision": run["revision"]}

    def list(self, run_id=None, pending=True, offset=0, limit=1000):
        query = select(db.review_issues)
        if run_id:
            query = query.where(db.review_issues.c.run_id == str(run_id))
        if pending:
            query = query.where(db.review_issues.c.state == "pending")
        with self.engine.connect() as connection:
            return list(
                connection.execute(
                    query.order_by(db.review_issues.c.page, db.review_issues.c.id)
                    .offset(offset)
                    .limit(limit)
                ).mappings()
            )

    def get(self, issue_id):
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(db.review_issues).where(db.review_issues.c.id == str(issue_id))
                )
                .mappings()
                .one_or_none()
            )
        if not row:
            raise ServiceError(404, "核对问题不存在。")
        body = self.read_json(row["content"])
        from hrs.processing.review_locations import locate_issues

        # Derived view only: old immutable packets/drafts retain their original evidence.
        previous_locations = body.get("locations") or []
        body["locations"] = locate_issues(
            body["text"],
            body["reasons"],
            page=row["page"],
            image_sha256=previous_locations[0].get("image_sha256", "")
            if previous_locations
            else "",
        )
        draft_text = self.objects.read_bytes(row["draft"]).decode("utf-8") if row["draft"] else None
        draft_locations = (row["draft"] or {}).get("review_locations")
        draft_locations_hash = (row["draft"] or {}).get("review_locations_text_sha256")
        draft_base = (row["draft"] or {}).get("base_text_sha256")
        draft_conflict = bool(
            draft_text is not None and draft_base and draft_base != row["text_sha256"]
        )
        if draft_text is None or draft_locations_hash != digest(draft_text) or draft_conflict:
            draft_locations, draft_locations_hash = None, None
        resolutions = [] if draft_conflict else (row["draft"] or {}).get("review_resolutions", [])
        if body.get("codex_input_sha256"):
            current_ids = {location["id"] for location in body["locations"]}
            resolutions = [
                resolution for resolution in resolutions if resolution["id"] in current_ids
            ]
        return {
            **row,
            **body,
            "review_input_sha256": body.get("codex_input_sha256"),
            "footnotes": resolve_footnotes(
                body["text"], [{"text": body["text"], "source": {"pages": body["pages"]}}]
            ),
            "draft_text": draft_text,
            "draft_conflict": draft_conflict,
            "draft_base_text_sha256": draft_base,
            "draft_locations": draft_locations,
            "draft_locations_text_sha256": draft_locations_hash,
            "resolutions": resolutions,
            "images": [f"/api/v2/runs/{row['run_id']}/artifacts/{name}" for name in body["images"]],
        }

    def issue_run(self, issue_id):
        with self.engine.connect() as connection:
            run_id = connection.scalar(
                select(db.review_issues.c.run_id).where(db.review_issues.c.id == str(issue_id))
            )
        if run_id is None:
            raise ServiceError(404, "核对问题不存在。")
        return run_id

    def require_workspace(self, run_id):
        from hrs.library.books import get_run
        from hrs.library.native_workspace import require_workspace
        from hrs.processing.store import Store

        return require_workspace(
            self.engine,
            get_run(self.engine, run_id),
            Store(resolve_workspace(self.settings.codex_root)),
        )

    def save_draft(self, issue_id, request):
        self.require_workspace(self.issue_run(issue_id))
        from hrs.library.draft_locations import normalize_draft_locations

        locations = None
        if request.locations is not None:
            issue = self.get(issue_id)
            locations = normalize_draft_locations(
                request.text, request.locations, issue["locations"]
            )
        reference = self.objects.put_bytes(
            request.text.encode("utf-8"),
            "text/markdown; charset=utf-8",
            run_id=self.issue_run(issue_id),
        )
        resolutions = [r.model_dump() for r in request.resolutions]
        reference = {**reference, "review_resolutions": resolutions}
        if locations is not None:
            reference.update(
                review_locations=locations, review_locations_text_sha256=digest(request.text)
            )
        receipt = {
            "draft_text": request.text,
            "resolutions": resolutions,
            "locations": locations,
            "locations_text_sha256": digest(request.text) if locations is not None else None,
        }
        with self.engine.begin() as connection:
            from hrs.library.deletion import require_run_active

            require_run_active(connection, self.issue_run(issue_id))
            row = (
                connection.execute(
                    select(db.review_issues)
                    .where(db.review_issues.c.id == str(issue_id))
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise ServiceError(404, "核对问题不存在。")
            if row["state"] != "pending":
                raise ServiceError(409, "本项已审核，不能再保存草稿。")
            reference = {
                **reference,
                "base_text_sha256": row["text_sha256"],
                "base_content": row["content"],
            }
            if row["revision"] == request.expected_revision + 1 and row["draft"] == reference:
                return {"revision": row["revision"], **receipt}
            if row["revision"] != request.expected_revision:
                raise ServiceError(409, "草稿版本发生变化，请重新读取当前问题。")
            revision = row["revision"] + 1
            connection.execute(
                update(db.review_issues)
                .where(db.review_issues.c.id == str(issue_id))
                .values(draft=reference, revision=revision)
            )
        return {"revision": revision, **receipt}

    def decide(self, issue_id, request):
        from hrs.library.books import get_run

        run = get_run(self.engine, self.issue_run(issue_id))
        if not (run["result"] or {}).get("codex_document_id"):
            raise ServiceError(
                409, "旧执行链已退役，原件、历史结果和草稿保留；请将原件导入原生流程后继续核对。"
            )
        self.require_workspace(run["id"])
        issue_id = str(issue_id)
        payload = request.model_dump(mode="json")
        if payload.get("decision_scope") is None:
            payload.pop("decision_scope", None)
        request_hash = digest(json.dumps(payload, sort_keys=True, ensure_ascii=False))
        if (request.action == "correct") != (request.text is not None):
            raise ServiceError(422, "修改并确认需要提交正文；直接放行不能附带未确认的修改。")
        replacement = (
            self.objects.put_bytes(
                request.text.encode("utf-8"),
                "text/markdown; charset=utf-8",
                run_id=self.issue_run(issue_id),
            )
            if request.text is not None
            else None
        )
        with self.engine.begin() as connection:
            existing = (
                connection.execute(
                    select(db.review_decisions).where(
                        db.review_decisions.c.id == str(request.decision_id)
                    )
                )
                .mappings()
                .one_or_none()
            )
            if existing:
                if existing["issue_id"] != issue_id or existing["request_sha256"] != request_hash:
                    raise ServiceError(409, "同一决定标识不能用于不同内容。")
                return existing["receipt"]
            try:
                scope = resolve_decision_scope(
                    resolve_workspace(self.settings.codex_root), request.decision_scope
                )
            except ValueError as error:
                raise ServiceError(409, str(error)) from error
            # Lock the parent first for consistent ordering across simultaneous decisions.
            run_id = connection.scalar(
                select(db.review_issues.c.run_id).where(db.review_issues.c.id == issue_id)
            )
            if run_id is None:
                raise ServiceError(404, "核对问题不存在。")
            from hrs.library.deletion import require_run_active

            require_run_active(connection, run_id)
            run = (
                connection.execute(select(db.runs).where(db.runs.c.id == run_id).with_for_update())
                .mappings()
                .one()
            )
            issue = (
                connection.execute(
                    select(db.review_issues)
                    .where(db.review_issues.c.id == issue_id)
                    .with_for_update()
                )
                .mappings()
                .one()
            )
            if (
                issue["state"] != "pending"
                or issue["revision"] != request.expected_revision
                or issue["text_sha256"] != request.expected_text_sha256
            ):
                # An exact retransmission can have waited for the first transaction's parent lock.
                repeated = (
                    connection.execute(
                        select(db.review_decisions).where(
                            db.review_decisions.c.id == str(request.decision_id)
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if (
                    repeated
                    and repeated["request_sha256"] == request_hash
                    and repeated["issue_id"] == issue_id
                ):
                    return repeated["receipt"]
                raise ServiceError(409, "该内容已被处理或版本已变化，请刷新当前问题。")
            if request.resolutions is not None:
                body = self.read_json(issue["content"])
                expected = {r["id"] for r in body.get("locations", [])}
                if not expected:
                    from hrs.processing.review_locations import locate_issues

                    expected = {
                        r["id"]
                        for r in locate_issues(
                            body["text"], body["reasons"], page=issue["page"], image_sha256=""
                        )
                    }
                resolved = {r.id for r in request.resolutions if r.action != "defer"}
                if resolved != expected or len(request.resolutions) != len(expected):
                    raise ServiceError(409, "请逐项处理疑点；暂不确定的内容仍需保留待审。")
            connection.execute(
                update(db.review_issues)
                .where(db.review_issues.c.id == issue_id)
                .values(state="approved", revision=issue["revision"] + 1, replacement=replacement)
            )
            pending_count = connection.scalar(
                select(func.count())
                .select_from(db.review_issues)
                .where(db.review_issues.c.run_id == run_id, db.review_issues.c.state == "pending")
            )
            next_issue = connection.scalar(
                select(db.review_issues.c.id)
                .where(db.review_issues.c.run_id == run_id, db.review_issues.c.state == "pending")
                .order_by(db.review_issues.c.page, db.review_issues.c.id)
                .limit(1)
            )
            revision = run["revision"] + 1
            state = "awaiting_review" if pending_count else "ready_for_ingestion"
            receipt = {
                "decision_id": str(request.decision_id),
                "issue_id": issue_id,
                "run_id": run_id,
                "revision": revision,
                "pending_count": pending_count,
                "next_issue_id": next_issue,
                "state": state,
            }
            connection.execute(
                insert(db.review_decisions).values(
                    id=str(request.decision_id),
                    issue_id=issue_id,
                    request_sha256=request_hash,
                    receipt={
                        **receipt,
                        "reviewer": "human-ui",
                        "action": request.action,
                        "resolutions": [r.model_dump() for r in request.resolutions or []],
                        "resolved_text_sha256": digest(request.text)
                        if request.text is not None
                        else issue["text_sha256"],
                        "decision_scope": scope,
                        "review_content": issue["content"],
                        "replacement": replacement,
                        "codex_input_sha256": issue["content"].get("codex_input_sha256"),
                        "application_state": "pending",
                        **(
                            {
                                "reviewer": "user-authorized-workflow-test",
                                "human_review": False,
                                "workflow_test": True,
                                "content_verified": False,
                            }
                            if scope == "workflow_test"
                            else {}
                        ),
                    },
                )
            )
            connection.execute(
                update(db.runs)
                .where(db.runs.c.id == run_id)
                .values(
                    pending_count=pending_count,
                    state=state,
                    revision=revision,
                    updated_at=func.now(),
                )
            )
            connection.execute(
                update(db.books).where(db.books.c.id == run["book_id"]).values(state=state)
            )
            connection.execute(
                insert(db.events).values(
                    book_id=run["book_id"],
                    run_id=run_id,
                    kind="run.changed",
                    payload={**receipt, "stage": "review"},
                )
            )
            connection.execute(
                insert(db.outbox).values(
                    dedup_key=f"review:{request.decision_id}",
                    run_id=run_id,
                    kind="review_changed",
                    payload={"revision": revision},
                )
            )
        return receipt

    def artifact(self, run_id, name):
        from hrs.library.books import get_run

        run = get_run(self.engine, run_id)
        if name == "original.pdf":
            reference = run["source"] if run["source"].get("sha256") else None
        elif run["conversion"]:
            bundle = self.read_json(run["conversion"])
            reference = bundle["files"].get(name)
            if name == "document.md" and not reference and bundle["manifest"].get("page_markdown"):
                reference = self.objects.put_bytes(
                    self.markdown(bundle).encode("utf-8"),
                    "text/markdown; charset=utf-8",
                    run_id=run_id,
                )
        else:
            draft = self.ocr_draft(run)
            reference = draft[0].get(name) if draft else None
        if reference is None:
            raise ServiceError(404, "原件或附件尚未生成。")
        return reference
