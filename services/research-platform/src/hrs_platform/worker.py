import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from google.protobuf.duration_pb2 import Duration
from sqlalchemy import select, update
from temporalio.api.workflowservice.v1 import RegisterNamespaceRequest
from temporalio.client import Client
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode
from temporalio.worker import Worker

from . import schema as db
from .activities import Activities
from .database import engine_for
from .deletion import DELETING, DeletionActivities
from .pipeline_activities import PipelineActivities
from .recovery import workflow_id
from .workflows import BookWorkflow, CardWorkflow, ConversionWorkflow, DeleteBookWorkflow

logger = logging.getLogger(__name__)


async def connect(settings):
    return await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)


async def register_namespace(settings):
    client = await connect(settings)
    try:
        await client.workflow_service.register_namespace(
            RegisterNamespaceRequest(
                namespace=settings.temporal_namespace,
                workflow_execution_retention_period=Duration(seconds=2592000),
            )
        )
    except RPCError as error:
        if error.status != RPCStatusCode.ALREADY_EXISTS:
            raise


async def dispatch_once(engine, client, settings, *, conversion_only=False):
    # This is a transactional notification bridge, not a second book scheduler.
    # Crashing after start but before marking delivered safely repeats the same workflow ID.
    def pending():
        with engine.connect() as connection:
            return list(
                connection.execute(
                    select(db.outbox)
                    .where(db.outbox.c.delivered.is_(False))
                    .order_by(db.outbox.c.id)
                    .limit(50)
                ).mappings()
            )

    for event in await asyncio.to_thread(pending):
        # Serialize dispatch with DELETE's book lock; no stale outbox snapshot may
        # start a producer after cancellation has already been acknowledged.
        with engine.begin() as dispatch_connection:
            owner = dispatch_connection.scalar(
                select(db.runs.c.book_id).where(db.runs.c.id == event["run_id"])
            )
            state = dispatch_connection.scalar(
                select(db.books.c.state).where(db.books.c.id == owner).with_for_update()
            )
            if state is None or state in DELETING:
                continue
            await dispatch_event(engine, client, settings, event, conversion_only=conversion_only)

    with engine.connect() as connection:
        deletions = list(
            connection.execute(
                select(db.book_deletions).where(db.book_deletions.c.state == "pending")
            ).mappings()
        )
    for deletion in deletions:
        with engine.connect() as connection:
            host_required = any(
                row["stage"] != "verify_upload"
                for row in connection.execute(
                    select(db.runs.c.stage).where(db.runs.c.book_id == deletion["book_id"])
                ).mappings()
            )
        try:
            await client.start_workflow(
                DeleteBookWorkflow.run,
                {
                    "book_id": deletion["book_id"],
                    "control_queue": settings.gpu_queue + "-control",
                    "host_required": host_required,
                },
                id=f"delete-book/{deletion['book_id']}/{deletion['attempt']}",
                task_queue=settings.task_queue,
                id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
            )
        except WorkflowAlreadyStartedError:
            pass
        with engine.begin() as connection:
            connection.execute(
                update(db.book_deletions)
                .where(
                    db.book_deletions.c.book_id == deletion["book_id"],
                    db.book_deletions.c.state == "pending",
                    db.book_deletions.c.attempt == deletion["attempt"],
                )
                .values(state="running")
            )


async def dispatch_event(engine, client, settings, event, *, conversion_only=False):
    try:
        if event["kind"] == "review_changed":
            with engine.connect() as connection:
                attempt = connection.scalar(
                    select(db.runs.c.recovery_attempt).where(db.runs.c.id == event["run_id"])
                )
            await client.get_workflow_handle(workflow_id("book", event["run_id"], attempt)).signal(
                BookWorkflow.review_changed, event["payload"]["revision"]
            )
        elif event["kind"] in {"retry_run", "start_cards"}:
            kind = event["payload"]["run_kind"]
            await client.start_workflow(
                CardWorkflow.run if kind == "cards" else BookWorkflow.run,
                {"run_id": event["run_id"], "gpu_queue": settings.gpu_queue},
                id=workflow_id(kind, event["run_id"], event["payload"].get("recovery_attempt", 0)),
                task_queue=settings.task_queue,
                id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                execution_timeout=timedelta(days=365),
            )
        elif event["kind"] == "start_book":
            await client.start_workflow(
                ConversionWorkflow.run if conversion_only else BookWorkflow.run,
                {"run_id": event["run_id"], "gpu_queue": settings.gpu_queue},
                id=f"{'book-conversion' if conversion_only else 'book'}/{event['run_id']}",
                task_queue=settings.task_queue,
                id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                execution_timeout=timedelta(days=365),
            )
        else:
            logger.error("Unknown committed event kind: %s", event["kind"])
            return
    except WorkflowAlreadyStartedError:
        # A duplicate delivery may arrive after this exact workflow already completed.
        pass
    except RPCError as error:
        if event["kind"] != "review_changed" or error.status != RPCStatusCode.NOT_FOUND:
            raise
        # A duplicated notification can arrive after the book workflow completed.
        # Missing initial dispatch is not acknowledged: the book event must retry first.
        with engine.connect() as connection:
            started = connection.scalar(
                select(db.outbox.c.delivered).where(
                    db.outbox.c.run_id == event["run_id"], db.outbox.c.kind == "start_book"
                )
            )
        if not started:
            return

    def acknowledge(event_id=event["id"]):
        with engine.begin() as connection:
            connection.execute(update(db.outbox).where(db.outbox.c.id == event_id).values(delivered=True))

    await asyncio.to_thread(acknowledge)


async def relay(engine, client, settings):
    while True:
        try:
            await dispatch_once(engine, client, settings)
        except Exception:
            logger.exception("Temporal notification pending; committed business records are retained")
        await asyncio.sleep(1)


async def run_worker(settings, gpu=False):
    client = await connect(settings)
    engine = engine_for(settings)
    activities = Activities(settings, engine)
    pipeline = PipelineActivities(settings, engine)
    deletion = DeletionActivities(settings, engine)
    queue = settings.gpu_queue if gpu else settings.task_queue
    with ThreadPoolExecutor(max_workers=1 if gpu else 4) as executor:
        worker = Worker(
            client,
            task_queue=queue,
            workflows=[] if gpu else [BookWorkflow, CardWorkflow, ConversionWorkflow, DeleteBookWorkflow],
            activities=[activities.convert_document, pipeline.check_card_images]
            if gpu
            else [
                activities.verify_upload,
                activities.record_conversion_failure,
                pipeline.initialize_review,
                pipeline.review_status,
                pipeline.organize_book,
                pipeline.publish_book,
                pipeline.index_book,
                pipeline.create_card_run,
                pipeline.generate_cards,
                pipeline.adopt_cards,
                pipeline.record_pipeline_failure,
                pipeline.finish_book,
            ],
            activity_executor=executor,
            max_concurrent_activities=1 if gpu else 4,
            graceful_shutdown_timeout=timedelta(seconds=45),
        )
        try:
            async with worker:
                # Control stays available even when every compute slot is busy.
                with ThreadPoolExecutor(max_workers=2) as control_executor:
                    async with Worker(
                        client,
                        task_queue=queue + "-control",
                        activities=[deletion.stop_gpu_requests, deletion.clean_gpu_cache]
                        if gpu
                        else [deletion.stop_book, deletion.erase_book, deletion.deletion_failed],
                        activity_executor=control_executor,
                        max_concurrent_activities=2,
                    ):
                        if gpu:
                            await asyncio.Event().wait()
                        else:
                            await relay(engine, client, settings)
        finally:
            engine.dispose()
