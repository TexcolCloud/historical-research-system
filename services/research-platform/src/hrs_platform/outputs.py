"""Small SQL references to immutable S3 outputs and actual execution events."""

import json
import time
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import UUID, uuid5

from sqlalchemy import func, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from temporalio.exceptions import ApplicationError

from . import schema as db
from .activities import objects_for
from .review import digest

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
        reference = self.objects.put_bytes(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"), run_id=run_id)
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
        identity = str(uuid5(UUID(run_id), key))
        if progress := execution_progress.get():
            progress.update(step=label, state=state)
        reference = (
            self.objects.put_bytes(json.dumps(details, ensure_ascii=False, default=str).encode("utf-8"), run_id=run_id)
            if details is not None
            else None
        )
        with self.engine.begin() as connection:
            connection.execute(
                pg_insert(db.execution_nodes)
                .values(
                    id=identity,
                    run_id=run_id,
                    parent_id=parent,
                    kind=kind,
                    label=label,
                    objective=objective,
                    state=state,
                    details=reference,
                )
                .on_conflict_do_update(
                    index_elements=["id"],
                    set_={
                        "state": state,
                        "finished_at": func.now() if state in {"completed", "failed"} else None,
                        **({"details": reference} if reference else {}),
                    },
                )
            )
            book_id = connection.scalar(select(db.runs.c.book_id).where(db.runs.c.id == run_id))
            connection.execute(
                insert(db.events).values(
                    book_id=book_id,
                    run_id=run_id,
                    kind="execution.changed",
                    payload={
                        "node_id": identity,
                        "parent_id": parent,
                        "kind": kind,
                        "label": label,
                        "state": state,
                    },
                )
            )
        return identity

    def list_nodes(self, run_id):
        with self.engine.connect() as connection:
            return list(
                connection.execute(
                    select(db.execution_nodes)
                    .where(db.execution_nodes.c.run_id == str(run_id))
                    .order_by(db.execution_nodes.c.started_at)
                ).mappings()
            )

    @contextmanager
    def operation(self, run_id, key, label, *, kind="component", objective=None, parent=None):
        properties = {"kind": kind, "label": label, "objective": objective or label, "parent": parent}
        identity = self.node(run_id, key, **properties)
        token = execution_parent.set(identity)
        try:
            yield identity
        except BaseException as error:
            waiting = isinstance(error, ApplicationError) and error.type in {"model_transport_wait", "retrieval_wait", "vision_service_wait", "card_batch_yield"}
            self.node(
                run_id,
                key,
                **properties,
                state="waiting" if waiting else "failed",
                details={
                    "error_type": error.type if isinstance(error, ApplicationError) else type(error).__name__,
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
            raise ApplicationError(
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
        reference = self.objects.put_bytes(json.dumps(value, ensure_ascii=False).encode("utf-8"), run_id=run_id)
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
                raise ApplicationError(
                    "本次运行已达到模型请求预算，已完成的结果和回执保留。",
                    type="model_request_budget",
                    non_retryable=True,
                )
            connection.execute(
                insert(db.stage_outputs).values(
                    run_id=run_id, step=step, input_sha256=fingerprint(value), reference=reference
                )
            )
