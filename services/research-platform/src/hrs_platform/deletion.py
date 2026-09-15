"""One book deletion use case: stop producers, collect ownership, then erase outputs.

Only the empty book tombstone and a small receipt survive, so SSE and repeated
DELETE requests remain meaningful. Content-addressed objects referenced by other
books are retained. Cleanup is retryable and never reports success before I/O.
"""

import asyncio
import json
import shutil
import time
from pathlib import Path
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import delete, insert, select, text, update
from temporalio import activity
from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.service import RPCError, RPCStatusCode

from . import schema as db
from .activities import objects_for
from .recovery import workflow_id
from .search import search_client

DELETING = {"deleting", "delete_failed", "deleted"}


def require_active(connection, book_id):
    if book_id is None:
        raise HTTPException(409, "书籍不存在或已删除。")
    state = connection.scalar(select(db.books.c.state).where(db.books.c.id == str(book_id)).with_for_update())
    if state is None or state in DELETING:
        raise HTTPException(409, "本书正在删除或已删除，不能继续处理。")


def require_run_active(connection, run_id):
    """Acquire lifecycle locks in book -> run -> issue order everywhere."""
    book = connection.scalar(select(db.runs.c.book_id).where(db.runs.c.id == str(run_id)))
    require_active(connection, book)


def receipt(engine, book_id):
    with engine.connect() as connection:
        row = (
            connection.execute(select(db.book_deletions).where(db.book_deletions.c.book_id == str(book_id)))
            .mappings()
            .one()
        )
    return {"book_id": row["book_id"], "state": row["state"], "error": row["error"]}


def request_deletion(engine, book_id):
    book_id = str(book_id)
    with engine.begin() as connection:
        book = (
            connection.execute(select(db.books).where(db.books.c.id == book_id).with_for_update())
            .mappings()
            .one_or_none()
        )
        if book is None:
            raise HTTPException(404, "书籍不存在。")
        prior = (
            connection.execute(select(db.book_deletions).where(db.book_deletions.c.book_id == book_id))
            .mappings()
            .one_or_none()
        )
        if prior and prior["state"] != "failed":
            return {"book_id": book_id, "state": prior["state"], "error": prior["error"]}
        if prior:
            connection.execute(
                update(db.book_deletions)
                .where(db.book_deletions.c.book_id == book_id)
                .values(state="pending", error=None, attempt=prior["attempt"] + 1)
            )
        else:
            connection.execute(insert(db.book_deletions).values(book_id=book_id, state="pending"))
        connection.execute(update(db.books).where(db.books.c.id == book_id).values(state="deleting"))
        connection.execute(
            update(db.outbox)
            .where(db.outbox.c.run_id.in_(select(db.runs.c.id).where(db.runs.c.book_id == book_id)))
            .values(delivered=True)
        )
        connection.execute(
            insert(db.events).values(book_id=book_id, kind="book.deleting", payload={"state": "deleting"})
        )
    return {"book_id": book_id, "state": "pending", "error": None}


def remove_cache(root, run_ids):
    root = Path(root).resolve()
    for run_id in run_ids:
        expected = root / str(UUID(run_id))
        target = expected.resolve()
        if target != expected or expected.is_symlink():
            raise ValueError("Task cache escapes the configured cache root")
        if target.exists():
            shutil.rmtree(target)


def references(value):
    if isinstance(value, dict):
        if {"key", "sha256", "byte_length"} <= value.keys():
            digest = value["sha256"]
            if value["key"] == f"hrs/v2/objects/{digest[:2]}/{digest}" and len(digest) == 64:
                yield value
        for child in value.values():
            yield from references(child)
    elif isinstance(value, list):
        for child in value:
            yield from references(child)


class DeletionActivities:
    def __init__(self, settings, engine):
        self.settings, self.engine = settings, engine
        self.objects = objects_for(settings)

    def runs(self, book_id):
        with self.engine.connect() as connection:
            return list(connection.execute(select(db.runs).where(db.runs.c.book_id == book_id)).mappings())

    @activity.defn
    async def stop_book(self, book_id: str) -> list[str]:
        client = await Client.connect(
            self.settings.temporal_address, namespace=self.settings.temporal_namespace
        )
        handles = []
        # Cancel every attempt, including manually restarted card tasks.
        for run in await asyncio.to_thread(self.runs, book_id):
            for attempt in range(run["recovery_attempt"] + 1):
                handle = client.get_workflow_handle(workflow_id(run["kind"], run["id"], attempt))
                try:
                    if (await handle.describe()).status == WorkflowExecutionStatus.RUNNING:
                        await handle.cancel()
                        handles.append(handle)
                except RPCError as error:
                    if error.status != RPCStatusCode.NOT_FOUND:
                        raise
        # WAIT_CANCELLATION_COMPLETED in the producer workflows ensures subprocesses
        # and blocking image checks have actually exited before files can be erased.
        while handles:
            activity.heartbeat()
            handles = [
                handle
                for handle in handles
                if (await handle.describe()).status == WorkflowExecutionStatus.RUNNING
            ]
            if handles:
                await asyncio.sleep(1)
        return [run["id"] for run in await asyncio.to_thread(self.runs, book_id)]

    @activity.defn
    def stop_gpu_requests(self, book_id: str) -> None:
        from hrs_runtime.local_vision import request

        request("/cancel-tasks", {"task_ids": [run["id"] for run in self.runs(book_id)]}, timeout=60)

    @activity.defn
    def clean_gpu_cache(self, run_ids: list[str]) -> None:
        remove_cache(self.settings.cache_root, run_ids)
        remove_cache(self.settings.project_root / "state/local-vision/receipts", run_ids)

    def roots(self, book_id, *, others=False):
        with self.engine.connect() as connection:
            run_ids = select(db.runs.c.id).where(
                db.runs.c.book_id != book_id if others else db.runs.c.book_id == book_id
            )
            for table in (
                db.runs,
                db.stage_outputs,
                db.execution_nodes,
                db.review_issues,
                db.chapters,
                db.cards,
            ):
                condition = table.c.id.in_(run_ids) if table is db.runs else table.c.run_id.in_(run_ids)
                for row in connection.execute(select(table).where(condition)).mappings():
                    yield from references(dict(row))

    def closure(self, roots):
        found, pending = {}, list(roots)
        while pending:
            ref = pending.pop()
            if ref["key"] in found:
                continue
            found[ref["key"]] = ref
            if ref.get("media_type") == "application/json":
                value = json.loads(self.objects.read_bytes(ref))
                pending.extend(references(value))
            if activity.in_activity():
                activity.heartbeat()
        return found

    def owned_references(self, book_id, *, others=False):
        with self.engine.connect() as connection:
            query = select(db.object_owners.c.reference).join(db.runs, db.runs.c.id == db.object_owners.c.run_id).where(
                db.runs.c.book_id != book_id if others else db.runs.c.book_id == book_id)
            return {ref["key"]: ref for ref in connection.scalars(query)}

    @activity.defn
    def erase_book(self, book_id: str) -> dict:
        # Serialize deletions of books which share content; the last owner removes
        # the shared object instead of two concurrent cleaners both retaining it.
        with self.engine.begin() as connection:
            deadline = time.monotonic() + 300
            while not connection.scalar(text("SELECT pg_try_advisory_xact_lock(1388427266)")):
                if activity.in_activity():
                    activity.heartbeat()
                if time.monotonic() >= deadline:
                    raise TimeoutError("Another deletion is still running; retry cleanup.")
                time.sleep(.1)
            return self._erase_book(book_id)

    def _erase_book(self, book_id):
        with self.engine.connect() as connection:
            state = connection.scalar(
                select(db.book_deletions.c.state).where(db.book_deletions.c.book_id == book_id)
            )
            if state == "completed":
                return {"book_id": book_id, "state": "completed", "error": None}
            saved = connection.scalar(
                select(db.book_deletions.c.manifest).where(db.book_deletions.c.book_id == book_id)
            )
        if saved is None:
            refs = self.closure(self.roots(book_id))
            # Pins may represent failed/in-progress uploads; do not try to read
            # their JSON. Nested business references are traversed above.
            refs.update(self.owned_references(book_id))
            with self.engine.begin() as connection:
                uploads = list(
                    connection.scalars(select(db.uploads.c.id).where(db.uploads.c.book_id == book_id))
                )
                saved = {"references": list(refs.values()), "uploads": uploads}
                connection.execute(
                    update(db.book_deletions)
                    .where(db.book_deletions.c.book_id == book_id)
                    .values(manifest=saved)
                )
        # Queued uploads have no content-addressed outputs. Do not traverse other
        # books just to remove their own isolated upload prefix.
        shared = self.closure(self.roots(book_id, others=True)) if saved["references"] else {}
        owned = [ref for ref in saved["references"] if ref["key"] not in shared]
        search = search_client(self.settings.opensearch_url)
        if search.indices.exists(index=self.settings.opensearch_index):
            result = search.delete_by_query(
                index=self.settings.opensearch_index,
                body={"query": {"term": {"book_id": book_id}}},
                refresh=True,
                conflicts="proceed",
            )
            if result.get("failures") or result.get("timed_out") or result.get("version_conflicts"):
                raise RuntimeError("检索记录清理未完成。")
        client, bucket = self.objects.client, self.objects.bucket
        for session_id in saved["uploads"]:
            prefix = self.settings.upload_prefix + str(UUID(session_id)) + "/"
            from .upload_cleanup import terminate_upload

            for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    if obj["Key"].endswith(".info"):
                        response = client.get_object(Bucket=bucket, Key=obj["Key"])
                        try:
                            info = json.load(response["Body"])
                        finally:
                            response["Body"].close()
                        terminate_upload(self.settings, info["ID"], session_id)
            for page in client.get_paginator("list_multipart_uploads").paginate(Bucket=bucket, Prefix=prefix):
                for upload in page.get("Uploads", []):
                    client.abort_multipart_upload(
                        Bucket=bucket, Key=upload["Key"], UploadId=upload["UploadId"]
                    )
            for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    client.delete_object(Bucket=bucket, Key=obj["Key"])
        for ref in owned:
            from .storage import object_lock
            with object_lock(self.engine, ref["key"]) as connection:
                other_owner = connection.scalar(select(db.object_owners.c.run_id)
                    .join(db.runs, db.runs.c.id == db.object_owners.c.run_id)
                    .where(db.object_owners.c.key == ref["key"], db.runs.c.book_id != book_id).limit(1))
                if other_owner:
                    continue
                client.delete_object(Bucket=bucket, Key=ref["key"])
            for folder in ("downloads", "review-reader"):
                (self.settings.cache_root / folder / ref["sha256"]).unlink(missing_ok=True)
            if activity.in_activity():
                activity.heartbeat()
        run_ids = [run["id"] for run in self.runs(book_id)]
        remove_cache(self.settings.cache_root, run_ids)
        with self.engine.begin() as connection:
            issue_ids = select(db.review_issues.c.id).where(db.review_issues.c.run_id.in_(run_ids))
            connection.execute(
                delete(db.review_decisions).where(db.review_decisions.c.issue_id.in_(issue_ids))
            )
            for table in (
                db.cards,
                db.chapters,
                db.review_issues,
                db.execution_nodes,
                db.stage_outputs,
                db.outbox,
            ):
                connection.execute(delete(table).where(table.c.run_id.in_(run_ids)))
            connection.execute(update(db.runs).where(db.runs.c.id.in_(run_ids)).values(parent_run_id=None))
            connection.execute(delete(db.runs).where(db.runs.c.id.in_(run_ids)))
            connection.execute(delete(db.uploads).where(db.uploads.c.book_id == book_id))
            connection.execute(delete(db.events).where(db.events.c.book_id == book_id))
            connection.execute(
                update(db.books).where(db.books.c.id == book_id).values(state="deleted", title="")
            )
            connection.execute(
                update(db.book_deletions)
                .where(db.book_deletions.c.book_id == book_id)
                .values(state="completed", manifest=None, error=None)
            )
            connection.execute(
                insert(db.events).values(book_id=book_id, kind="book.deleted", payload={"state": "deleted"})
            )
        return {"book_id": book_id, "state": "completed", "error": None}

    @activity.defn
    def deletion_failed(self, book_id: str) -> None:
        with self.engine.begin() as connection:
            connection.execute(update(db.books).where(db.books.c.id == book_id).values(state="delete_failed"))
            connection.execute(
                update(db.book_deletions)
                .where(db.book_deletions.c.book_id == book_id)
                .values(state="failed", error="停止任务或清理未完成，请重试删除。")
            )
            connection.execute(
                insert(db.events).values(
                    book_id=book_id, kind="book.delete_failed", payload={"state": "delete_failed"}
                )
            )
