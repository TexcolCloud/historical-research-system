"""Shared card use case: run creation, durable adoption, recovery and reading."""

import json
from functools import cached_property
from uuid import UUID, uuid5

from sqlalchemy import func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from hrs_platform import models as db
from hrs_platform.domain.card_rules import card_stage, quote_pages, quote_range, visual_groups
from hrs_platform.domain.errors import ServiceError
from hrs_platform.services.books import get_run
from hrs_platform.services.cards.evidence import CardEvidence
from hrs_platform.services.cards.finalization import check_images, finalize
from hrs_platform.services.cards.generation import generate
from hrs_platform.services.documents.library import Library
from hrs_platform.services.documents.review import Review
from hrs_platform.services.runs.lifecycle import RunLifecycle
from hrs_platform.services.runs.outputs import Outputs, fingerprint


class Cards:
    def __init__(self, settings, engine):
        self.settings, self.engine = settings, engine
        self.outputs = Outputs(settings, engine)
        self.library, self.review = Library(settings, engine), Review(settings, engine)

    def create_run(self, parent_id, request_id=None):
        parent = get_run(self.engine, parent_id)
        if not (parent["result"] or {}).get("published"):
            raise ValueError("Cards require a published, reviewed book.")
        identity = str(uuid5(UUID(parent_id), str(request_id) if request_id else "cards"))
        with self.engine.begin() as connection:
            from hrs_platform.services.deletion import require_active

            require_active(connection, parent["book_id"])
            created = connection.scalar(
                pg_insert(db.runs)
                .values(
                    id=identity,
                    book_id=parent["book_id"],
                    parent_run_id=parent_id,
                    kind="cards",
                    state="queued",
                    stage="planning",
                    source=parent["source"],
                    conversion=parent["conversion"],
                )
                .on_conflict_do_nothing()
                .returning(db.runs.c.id)
            )
            if created:
                connection.execute(
                    insert(db.events).values(
                        book_id=parent["book_id"],
                        run_id=identity,
                        kind="run.created",
                        payload={"run_kind": "cards", "state": "queued", "stage": "planning"},
                    )
                )
            child = (
                connection.execute(select(db.runs).where(db.runs.c.id == identity).with_for_update())
                .mappings()
                .one()
            )
            # A parent retry reuses durable child outputs, but grants one new bounded
            # request epoch. Replayed activity completions cannot grant it twice.
            marker = (child["result"] or {}).get("parent_recovery_attempt", 0)
            attempt = child["recovery_attempt"]
            if request_id is None and child["state"] == "failed" and parent["recovery_attempt"] > marker:
                attempt += 1
                connection.execute(
                    update(db.runs)
                    .where(db.runs.c.id == identity)
                    .values(
                        recovery_attempt=attempt,
                        state="queued",
                        error=None,
                        result={
                            **(child["result"] or {}),
                            "parent_recovery_attempt": parent["recovery_attempt"],
                        },
                    )
                )
        from hrs_platform.services.runs.recovery import workflow_id

        return {
            "run_id": identity,
            "book_id": parent["book_id"],
            "workflow_id": workflow_id("cards", identity, attempt),
        }

    def start(self, book_id, request_id):
        with self.engine.connect() as connection:
            parent = connection.scalar(
                select(db.runs.c.id).where(db.runs.c.book_id == str(book_id), db.runs.c.kind == "book")
            )
        if parent is None:
            raise ServiceError(404, "书籍不存在。")
        if not (get_run(self.engine, parent)["result"] or {}).get("published"):
            raise ServiceError(409, "请先完成本书内容核对与入库。")
        created = self.create_run(parent, request_id)
        with self.engine.begin() as connection:
            connection.execute(
                pg_insert(db.outbox)
                .values(
                    dedup_key=f"cards:{created['run_id']}",
                    run_id=created["run_id"],
                    kind="start_cards",
                    payload={"run_kind": "cards"},
                )
                .on_conflict_do_nothing()
            )
        return get_run(self.engine, created["run_id"])

    @cached_property
    def models(self):
        from hrs_platform.services.models.text import Models

        return Models(self.settings, self.engine)

    @cached_property
    def evidence(self):
        return CardEvidence(self.settings, self.engine, self.outputs, self.library)

    def adopt(self, run_id, batch_key=None):
        from hrs_platform.services.models.vision import result_key

        run = get_run(self.engine, run_id)
        generated = self.outputs.get(
            run_id, batch_key + ":finalized" if batch_key else card_stage(run, "finalized-cards")
        )
        if generated is None:
            raise ValueError(
                "Final text review must incorporate original-image observations before adoption."
            )
        rows = []
        for card in generated["cards"]:
            source = self.outputs.get(run_id, card["source_step"])
            units = {row["unit_id"]: row for row in source["units"]}
            wanted = visual_groups(card["candidate"], units)
            checks = [
                self.outputs.get(run_id, result_key(card["id"], group["key"], card.get("visual_revision")))
                for group in wanted
            ]
            semantic = card["text_check"]
            initial = card.get("initial_text_check", semantic)
            passed = (
                bool(wanted)
                and not card.get("processing_error")
                and initial["conclusion"] == "pass"
                and not initial["unverified_object_ids"]
                and not any(finding["severity"] in {"serious", "material"} for finding in initial["findings"])
                and semantic["conclusion"] == "pass"
                and not semantic["unverified_object_ids"]
                and not any(
                    finding["severity"] in {"serious", "material"} for finding in semantic["findings"]
                )
                and all(
                    check and all(item["result"] == "verified" for item in check["items"]) for check in checks
                )
            )
            content = self.review.objects.put_bytes(
                json.dumps(
                    {"candidate": card["candidate"], "units": source["units"]}, ensure_ascii=False
                ).encode(),
                run_id=run_id,
            )
            verdict = self.review.objects.put_bytes(
                json.dumps(
                    {
                        "text": semantic,
                        "initial_text": card.get("initial_text_check"),
                        "original_candidate": card.get("original_candidate"),
                        "visual": checks,
                        "processing_error": card.get("processing_error"),
                        "machine_approval": passed,
                        "human_approval": False,
                    },
                    ensure_ascii=False,
                ).encode(),
                run_id=run_id,
            )
            rows.append(
                {
                    "id": card["id"],
                    "book_id": run["book_id"],
                    "run_id": run_id,
                    "title": card["candidate"]["title"],
                    "state": "adopted" if passed else "needs_revision",
                    "content": content,
                    "checks": verdict,
                }
            )
        with self.engine.begin() as connection:
            for row in rows:
                connection.execute(
                    pg_insert(db.cards)
                    .values(**row)
                    .on_conflict_do_update(
                        index_elements=["id"],
                        set_={key: row[key] for key in ("title", "state", "content", "checks")},
                        where=db.cards.c.state != "adopted",
                    )
                )
        state = (
            "completed"
            if rows and not generated.get("pending_topics") and all(row["state"] == "adopted" for row in rows)
            else "needs_revision"
        )
        if not generated.get("complete", True):
            state = "processing"
        elif batch_key:
            self.outputs.put(run_id, card_stage(run, "finalized-cards"), generated, {"batch_key": batch_key})
        RunLifecycle(self.engine).transition(
            run_id,
            state,
            "complete" if state == "completed" else "planning" if state == "processing" else "machine_review",
            result={
                **(run.get("result") or {}),
                "pending_topics": generated.get("pending_topics", []),
                "cards": len(rows),
                "adopted": sum(row["state"] == "adopted" for row in rows),
            },
        )
        return {
            "run_id": run_id,
            "state": state,
            "cards": len(rows),
            "adopted": sum(row["state"] == "adopted" for row in rows),
        }

    def schedule_repair(self, run_id, revision):
        """Advance only actionable failures, with durable idempotency and no-progress bounds."""
        run = get_run(self.engine, run_id)
        current = (run.get("result") or {}).get("card_revision", 0)
        if current > revision:
            return {"retry": True}
        if current != revision or run["state"] != "needs_revision":
            return {"retry": False}
        generated = self.outputs.get(run_id, card_stage(run, "finalized-cards"))
        actionable = []
        deferred = []
        for topic in generated.get("pending_topics", []):
            if topic["error_type"] in {
                "model_output_invalid",
                "model_output_limit",
                "card_input_budget",
                "model_request_exhausted",
                "card_evidence_insufficient",
            }:
                actionable.append({k: topic[k] for k in ("unit_ids", "error_type")})
        with self.engine.connect() as connection:
            rows = list(
                connection.execute(
                    select(db.cards).where(db.cards.c.run_id == run_id, db.cards.c.state == "needs_revision")
                ).mappings()
            )
        for row in rows:
            checks = self.review.read_json(row["checks"])
            visual = checks.get("visual", [])
            missing_check = not visual or any(not check for check in visual)
            failure_type = (checks.get("processing_error") or {}).get("error_type")
            if (
                failure_type == "original_missing"
                or (missing_check and failure_type != "visual_output_invalid")
                or any(
                    item["result"] in {"unreadable", "not_located", "not_checked"}
                    for check in visual
                    if check
                    for item in check["items"]
                )
            ):
                deferred.append(str(row["id"]))
                continue
            actionable.append(
                {
                    "id": str(row["id"]),
                    "content": row["content"]["sha256"],
                    "processing_error": checks.get("processing_error"),
                    "findings": [
                        (f.get("object_id"), f.get("code")) for f in checks["text"].get("findings", [])
                    ],
                }
            )
        signature = fingerprint(actionable)
        with self.engine.begin() as connection:
            from hrs_platform.services.deletion import require_run_active

            require_run_active(connection, run_id)
            fresh = (
                connection.execute(select(db.runs).where(db.runs.c.id == run_id).with_for_update())
                .mappings()
                .one()
            )
            result = dict(fresh["result"] or {})
            if result.get("card_revision", 0) > revision:
                return {"retry": True}
            if fresh["state"] != "needs_revision":
                return {"retry": False}
            history = result.get("auto_repair_signatures", [])
            retry = bool(actionable) and len(history) < 2 and signature not in history
            result["auto_repair_status"] = "scheduled" if retry else "manual_required"
            if retry:
                result.update(
                    card_revision=revision + 1,
                    auto_repair_signatures=[*history, signature],
                    auto_repair_revision=revision + 1,
                    auto_repair_deferred_ids=deferred,
                )
            values = {"result": result, "revision": fresh["revision"] + 1, "updated_at": func.now()}
            if retry:
                values.update(state="processing", stage="planning")
            connection.execute(update(db.runs).where(db.runs.c.id == run_id).values(**values))
            connection.execute(
                insert(db.events).values(
                    book_id=fresh["book_id"],
                    run_id=run_id,
                    kind="run.changed",
                    payload={
                        "run_kind": "cards",
                        "state": values.get("state", fresh["state"]),
                        "stage": values.get("stage", fresh["stage"]),
                        "revision": values["revision"],
                    },
                )
            )
        return {"retry": retry}

    def _supersede(self, identity):
        with self.engine.begin() as connection:
            connection.execute(
                update(db.cards)
                .where(db.cards.c.id == identity, db.cards.c.state != "adopted")
                .values(state="superseded")
            )

    def list(self, book_id=None, offset=0, limit=100):
        query = (
            select(db.cards)
            .where(db.cards.c.state != "superseded")
            .order_by(db.cards.c.created_at.desc(), db.cards.c.id)
            .offset(offset)
            .limit(limit)
        )
        if book_id:
            query = query.where(db.cards.c.book_id == str(book_id))
        with self.engine.connect() as connection:
            return list(connection.execute(query).mappings())

    def get(self, card_id):
        with self.engine.connect() as connection:
            row = (
                connection.execute(select(db.cards).where(db.cards.c.id == str(card_id)))
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise ServiceError(404, "卡片不存在。")
        detail = {
            **row,
            **self.review.read_json(row["content"]),
            "verdict": self.review.read_json(row["checks"]),
        }
        units = {unit["unit_id"]: unit for unit in detail["units"]}
        locations = []
        for item in detail["candidate"]["items"]:
            # Older candidates remain exportable without inventing item identities.
            if not item.get("item_id"):
                continue
            for index, selection in enumerate(item.get("selections", [])):
                start, end, pages, issue = None, None, [], None
                unit = units.get(selection["unit_id"])
                if unit is None:
                    issue = "未找到此引文的来源正文。"
                else:
                    try:
                        start, end = quote_range(unit, selection)
                    except ValueError:
                        issue = "引文未能在保存的来源正文中精确定位。"
                    else:
                        try:
                            pages = quote_pages(unit, selection)
                        except (ValueError, KeyError):
                            issue = "引文已定位，但缺少可核实的原件页码。"
                locations.append(
                    {
                        "item_id": item["item_id"],
                        "selection_index": index,
                        "unit_id": selection["unit_id"],
                        "start": start,
                        "end": end,
                        "pages": pages,
                        "issue": issue,
                    }
                )
        return {**detail, "quote_locations": locations}

    async def generate(self, run_id, *, incremental=False):
        return await generate(self, run_id, incremental=incremental)

    def check_images(self, run_id, batch_key=None):
        return check_images(self, run_id, batch_key)

    async def finalize(self, run_id, batch_key=None):
        return await finalize(self, run_id, batch_key)
