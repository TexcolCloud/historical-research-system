"""Small SQL references to immutable S3 outputs and actual execution events."""

import hashlib
import json
from uuid import UUID, uuid5

from sqlalchemy import func, insert, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from hrs.library.errors import ServiceError
from hrs.library.object_ownership import owned_objects
from hrs.library.records import text_digest as digest
from hrs.storage import models as db


class StageInputMismatch(ValueError):
    """An immutable output belongs to another model input."""


def fingerprint(value):
    return digest(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str))


class Outputs:
    def __init__(self, settings, engine):
        self.engine, self.objects = engine, owned_objects(settings, engine)

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
                .values(
                    run_id=run_id,
                    step=step,
                    input_sha256=fingerprint(dependency),
                    reference=reference,
                )
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
        return value

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
                (old or {}).get("details")
                if same_details
                else self.objects.put_bytes(data, run_id=run_id)
            )
            row["finished_at"] = func.now() if row["state"] in {"completed", "failed"} else None
            changed.append(row)
        if changed:
            statement = pg_insert(db.execution_nodes).values(changed)
            columns = ("parent_id", "kind", "label", "objective", "state", "details")
            with self.engine.begin() as connection:
                from hrs.library.deletion import require_run_active

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
                    book_id = connection.scalar(
                        select(db.runs.c.book_id).where(db.runs.c.id == run_id)
                    )
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
        with self.engine.connect().execution_options(
            isolation_level="REPEATABLE READ"
        ) as connection:
            base = db.execution_nodes.c.run_id == str(run_id)
            stages = list(
                connection.execute(
                    select(
                        db.execution_nodes.c.label,
                        db.execution_nodes.c.state,
                        func.count().label("count"),
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
                    db.execution_nodes.c.state.in_(
                        ["running", "leased", "failed", "waiting", "paused"]
                    )
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
