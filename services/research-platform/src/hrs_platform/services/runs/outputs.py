"""Small SQL references to immutable S3 outputs and actual execution events."""

import hashlib
import json
import time
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import UUID, uuid5

from sqlalchemy import func, insert, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from hrs_platform import models as db
from hrs_platform.domain.errors import ServiceError, TaskError
from hrs_platform.services.documents.review import digest
from hrs_platform.services.storage import objects_for

execution_parent = ContextVar("execution_parent", default=None)
execution_progress = ContextVar("execution_progress", default=None)


class StageInputMismatch(ValueError):
    """An immutable output belongs to another model input."""


def fingerprint(value):
    return digest(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str))


class Outputs:
    def __init__(self, settings, engine):
        self.engine, self.objects = engine, objects_for(settings, engine)

    def get(self, run_id, step, dependency=None):
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(db.stage_outputs).where(
                        db.stage_outputs.c.run_id == run_id, db.stage_outputs.c.step == step
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            return None
        if dependency is not None and row["input_sha256"] != fingerprint(dependency):
            raise StageInputMismatch("The saved stage belongs to different fixed input.")
        return json.loads(self.objects.read_bytes(row["reference"]))

    def put(self, run_id, step, value, dependency):
        reference = self.objects.put_bytes(
            json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"), run_id=run_id
        )
        with self.engine.begin() as connection:
            connection.execute(
                pg_insert(db.stage_outputs)
                .values(run_id=run_id, step=step, input_sha256=fingerprint(dependency), reference=reference)
                .on_conflict_do_nothing()
            )
            existing = (
                connection.execute(
                    select(db.stage_outputs).where(
                        db.stage_outputs.c.run_id == run_id, db.stage_outputs.c.step == step
                    )
                )
                .mappings()
                .one()
            )
            if (
                existing["input_sha256"] != fingerprint(dependency)
                or existing["reference"]["sha256"] != reference["sha256"]
            ):
                raise RuntimeError("Stage output conflicts with already committed evidence.")
        if progress := execution_progress.get():
            progress.update(step=step, last_commit=time.monotonic())
        return value

    def node(self, run_id, key, *, kind, label, objective, state="running", parent=None, details=None):
        if progress := execution_progress.get():
            progress.update(step=label, state=state)
        return self.nodes(
            run_id,
            [
                dict(
                    key=key,
                    kind=kind,
                    label=label,
                    objective=objective,
                    state=state,
                    parent=parent,
                    details=details,
                )
            ],
        )[0]

    def nodes(self, run_id, records):
        """Project a batch; prepare changed evidence outside the short SQL commit."""
        identities = [str(uuid5(UUID(run_id), row["key"])) for row in records]
        with self.engine.connect() as connection:
            previous = {
                r["id"]: r
                for r in connection.execute(
                    select(db.execution_nodes).where(db.execution_nodes.c.run_id == run_id)
                ).mappings()
            }
        changed = []
        for identity, record in zip(identities, records, strict=True):
            row = dict(
                id=identity,
                run_id=run_id,
                parent_id=record.get("parent"),
                kind=record["kind"],
                label=record["label"],
                objective=record["objective"],
                state=record.get("state", "running"),
            )
            old = previous.get(identity)
            data = (
                json.dumps(record["details"], ensure_ascii=False, default=str).encode("utf-8")
                if record.get("details") is not None
                else None
            )
            sha = hashlib.sha256(data).hexdigest() if data is not None else None
            same_details = data is None or (old and (old["details"] or {}).get("sha256") == sha)
            if old and same_details and all(old[k] == v for k, v in row.items()):
                continue
            row["details"] = (
                (old or {}).get("details") if same_details else self.objects.put_bytes(data, run_id=run_id)
            )
            row["finished_at"] = func.now() if row["state"] in {"completed", "failed"} else None
            changed.append(row)
        if changed:
            statement = pg_insert(db.execution_nodes).values(changed)
            columns = ("parent_id", "kind", "label", "objective", "state", "details")
            with self.engine.begin() as connection:
                from hrs_platform.services.deletion import require_run_active

                require_run_active(connection, run_id)
                applied = connection.scalars(
                    statement.on_conflict_do_update(
                        index_elements=["id"],
                        set_={k: getattr(statement.excluded, k) for k in (*columns, "finished_at")},
                        where=or_(
                            *(
                                getattr(db.execution_nodes.c, k).is_distinct_from(
                                    getattr(statement.excluded, k)
                                )
                                for k in columns
                            )
                        ),
                    ).returning(db.execution_nodes.c.id)
                ).all()
                if applied:
                    book_id = connection.scalar(select(db.runs.c.book_id).where(db.runs.c.id == run_id))
                    connection.execute(
                        insert(db.events).values(
                            book_id=book_id,
                            run_id=run_id,
                            kind="execution.changed",
                            payload={
                                "node_ids": list(applied),
                                **({"node_id": applied[0]} if len(applied) == 1 else {}),
                            },
                        )
                    )
        return identities

    def list_nodes(self, run_id):
        with self.engine.connect() as connection:
            return list(
                connection.execute(
                    select(db.execution_nodes)
                    .where(db.execution_nodes.c.run_id == str(run_id))
                    .order_by(db.execution_nodes.c.started_at)
                ).mappings()
            )

    def window(self, run_id, *, history=False, label=None, offset=0):
        # Summary and selected window share one snapshot; history stays on the server.
        with self.engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
            base = db.execution_nodes.c.run_id == str(run_id)
            stages = list(
                connection.execute(
                    select(
                        db.execution_nodes.c.label, db.execution_nodes.c.state, func.count().label("count")
                    )
                    .where(base)
                    .group_by(db.execution_nodes.c.label, db.execution_nodes.c.state)
                    .order_by(db.execution_nodes.c.label, db.execution_nodes.c.state)
                ).mappings()
            )
            query = select(db.execution_nodes).where(base)
            if label:
                query = query.where(db.execution_nodes.c.label == label)
            if not history:
                query = query.where(
                    db.execution_nodes.c.state.in_(["running", "leased", "failed", "waiting", "paused"])
                )
            matching = connection.scalar(select(func.count()).select_from(query.subquery()))
            records = list(
                connection.execute(
                    query.order_by(db.execution_nodes.c.started_at.desc(), db.execution_nodes.c.id)
                    .offset(offset)
                    .limit(40)
                ).mappings()
            )
            return dict(
                total=sum(s["count"] for s in stages),
                matching=matching,
                stages=stages,
                records=records,
                offset=offset,
                limit=40,
            )

    @contextmanager
    def operation(self, run_id, key, label, *, kind="component", objective=None, parent=None):
        properties = {"kind": kind, "label": label, "objective": objective or label, "parent": parent}
        identity = self.node(run_id, key, **properties)
        token = execution_parent.set(identity)
        try:
            yield identity
        except BaseException as error:
            waiting = isinstance(error, TaskError) and error.type in {
                "model_transport_wait",
                "retrieval_wait",
                "vision_service_wait",
                "card_batch_yield",
            }
            self.node(
                run_id,
                key,
                **properties,
                state="waiting" if waiting else "failed",
                details={
                    "error_type": error.type if isinstance(error, TaskError) else type(error).__name__,
                    **({"recovery": error.details[0]} if waiting and error.details else {}),
                },
            )
            raise
        else:
            self.node(run_id, key, **properties, state="completed")
        finally:
            execution_parent.reset(token)

    def request_count(self, run_id, *, vision=False, connection=None):
        query = (
            select(func.count())
            .select_from(db.stage_outputs)
            .where(
                db.stage_outputs.c.run_id == run_id,
                db.stage_outputs.c.step.contains(":http-request:"),
                db.stage_outputs.c.step.startswith("视觉核对:")
                if vision
                else ~db.stage_outputs.c.step.startswith("视觉核对:"),
            )
        )
        if connection is not None:
            return connection.scalar(query)
        with self.engine.connect() as connection:
            return connection.scalar(query)

    def preflight(self, run_id, minimum_total, maximum):
        used = self.request_count(run_id)
        # Estimates only reject a fresh start. Historical layouts can overestimate
        # remaining work; receipts remain recoverable even at the request limit.
        # reserve_request alone authorizes new paid calls under the database lock.
        if minimum_total > maximum and used == 0:
            raise TaskError(
                f"文本调用预算不足：基础处理预计至少 {minimum_total} 次，已使用 {used} 次，"
                f"上限 {maximum} 次。请调整 PLATFORM_MODEL_MAX_CALLS 后重试；已有结果保留。",
                type="model_request_budget",
                non_retryable=True,
            )
        return {
            "minimum_calls": minimum_total,
            "used_calls": used,
            "maximum_calls": maximum,
            "remaining_calls": maximum - used,
            "estimate_excludes_repairs_and_tool_rounds": True,
        }

    def reserve_request(self, run_id, step, value, maximum):
        reference = self.objects.put_bytes(
            json.dumps(value, ensure_ascii=False).encode("utf-8"), run_id=run_id
        )
        with self.engine.begin() as connection:
            connection.execute(select(db.runs.c.id).where(db.runs.c.id == run_id).with_for_update()).one()
            prior = connection.scalar(
                select(db.stage_outputs.c.step).where(
                    db.stage_outputs.c.run_id == run_id, db.stage_outputs.c.step == step
                )
            )
            if prior:
                return
            count = self.request_count(run_id, vision=step.startswith("视觉核对:"), connection=connection)
            if count >= maximum:
                raise TaskError(
                    "本次运行已达到模型请求预算，已完成的结果和回执保留。",
                    type="model_request_budget",
                    non_retryable=True,
                )
            connection.execute(
                insert(db.stage_outputs).values(
                    run_id=run_id, step=step, input_sha256=fingerprint(value), reference=reference
                )
            )

    def details(self, execution_id):
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(db.execution_nodes).where(db.execution_nodes.c.id == str(execution_id))
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise ServiceError(404, "执行记录不存在。")
        return json.loads(self.objects.read_bytes(row["details"])) if row["details"] else {}
