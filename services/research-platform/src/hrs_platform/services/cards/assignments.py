"""Schedule independent readers and preserve ordered per-assignment checkpoints."""

import asyncio

from hrs_platform.domain.errors import TaskError
from hrs_platform.services.cards.reading import (
    coverage_batches,
    neighbor_context,
    read_batch,
)
from hrs_platform.services.runs.outputs import fingerprint


async def read_assignments(services, run_id, plan, all_units, main, policy, incremental, state):
    semaphore = asyncio.Semaphore(2)
    remaining_reads = [8] if incremental else None

    async def read_assignment(number, assignment):
        group = f"研究分工:v2:{number}"
        async with semaphore:
            with services.outputs.operation(
                run_id,
                group,
                assignment.name,
                kind="group",
                objective=assignment.objective,
                parent=main,
            ) as branch:
                units = [row for row in all_units if row["chapter_id"] in assignment.chapter_ids]
                research_state = state(units)
                readings = []
                batch_key = f"{group}:batches"
                batches = services.outputs.get(run_id, batch_key)
                if batches is None:
                    # Existing plans may already have two-unit reading checkpoints.
                    # Freeze that layout once; new plans use token-packed structural units.
                    groups = (
                        [units[i : i + 2] for i in range(0, len(units), 2)]
                        if policy["legacy_pairs"]
                        else list(coverage_batches(units))
                    )
                    batches = services.outputs.put(
                        run_id,
                        batch_key,
                        [[u["unit_id"] for u in batch] for batch in groups],
                        {"units": units},
                    )
                by_id = {u["unit_id"]: u for u in units}
                offset = 0
                for identities in batches:
                    batch = [by_id[identity] for identity in identities]
                    reading_key = f"{group}:completed-batch:{offset}"
                    reading = services.outputs.get(run_id, reading_key)
                    if reading is None:
                        if remaining_reads is not None:
                            if remaining_reads[0] == 0:
                                raise TaskError(
                                    "阅读进度已保存，交还队列。", type="card_batch_yield", non_retryable=True
                                )
                            remaining_reads[0] -= 1
                        reading = await read_batch(
                            services.models,
                            run_id,
                            f"{group}:阅读:{offset}",
                            batch,
                            assignment.objective,
                            branch,
                            readings[-1] if readings else None,
                            context=neighbor_context(all_units, batch),
                            research_state=research_state,
                        )
                        services.outputs.put(
                            run_id,
                            reading_key,
                            reading,
                            {"units": batch, "assignment": assignment.model_dump()},
                        )
                    readings.append(reading)
                    offset += len(batch)
                services.outputs.put(
                    run_id,
                    f"{group}:readings:{fingerprint(readings)}",
                    {"readings": readings},
                    {"units": units, "assignment": assignment.model_dump()},
                )
                return readings

    # Only independent assignments overlap. Their own batches retain previous-reading order.
    async def guarded_assignment(number, assignment):
        try:
            return await read_assignment(number, assignment)
        except TaskError as error:
            if error.type not in {"model_transport_wait", "model_transport_exhausted", "card_batch_yield"}:
                raise
            # Drain siblings already in flight. Models blocks new provider
            # sends until the workflow's cooldown, without faking readings.
            return error

    try:
        async with asyncio.TaskGroup() as tasks:
            jobs = [
                tasks.create_task(guarded_assignment(i, assignment))
                for i, assignment in enumerate(plan.assignments)
            ]
    except ExceptionGroup as failure:
        # Do not serialize the giant sibling ExceptionGroup into Temporal.
        error = failure.exceptions[0]
        while isinstance(error, ExceptionGroup):
            error = error.exceptions[0]
        raise error from None
    deferred = [
        job.result()
        for job in jobs
        if isinstance(job.result(), TaskError) and job.result().type != "card_batch_yield"
    ]
    if deferred:
        terminal = next((error for error in deferred if error.type == "model_transport_exhausted"), None)
        error = terminal or max(deferred, key=lambda error: error.details[0]["retry_at"])
        raise error from None
    if any(isinstance(job.result(), TaskError) for job in jobs):
        return None
    readings = [record for job in jobs for record in job.result()]
    return readings
