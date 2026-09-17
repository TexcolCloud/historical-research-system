"""Resume the topic queue, isolate failures and persist bounded splits."""

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID, uuid5

from hrs_platform.domain.card_rules import (
    MAX_CARD_TOPICS,
    CardPlan,
    CardTopic,
    ResearchPlan,
    split_topic,
    visual_groups,
)
from hrs_platform.domain.errors import TaskError
from hrs_platform.services.cards.synthesis import synthesize_topic
from hrs_platform.services.runs.lifecycle import RunLifecycle
from hrs_platform.services.runs.outputs import fingerprint


@dataclass(frozen=True)
class TopicBatch:
    """Frozen research inputs shared by resumable topic generation."""

    run_id: str
    run: dict
    generated_key: str
    card_plan: CardPlan
    main: str
    revision: int
    prior_cards: dict
    prior_pending: dict
    adopted_ids: set[str]
    all_units: list[dict]
    state: Callable
    readings: list[dict]
    plan: ResearchPlan
    corpus: dict


async def generate_topics(services, batch, *, incremental=False):
    produced, pending = [], []
    queue = deque((number, "", topic, batch.main) for number, topic in enumerate(batch.card_plan.topics))
    total_topics = len(queue)
    processed = 0
    # Reserve all persisted descendants before permitting any new split. A
    # later sibling's old division must not consume an allowance twice.
    known = deque((f"卡片主题:v2:{number}", "") for number in range(len(batch.card_plan.topics)))
    while known:
        root, path = known.popleft()
        key = root + (f":split:{path}" if path else "")
        division = services.outputs.get(batch.run_id, f"{key}:division")
        if division:
            total_topics += len(division["topics"]) - 1
            known.extend((root, f"{path}.{i}" if path else str(i)) for i in range(len(division["topics"])))
    while queue:
        if incremental and processed >= 1:
            break
        number, path, topic, parent = queue.popleft()
        group = f"卡片主题:v2:{number}" + (f":split:{path}" if path else "")
        base_group = group
        identity = str(uuid5(UUID(batch.run_id), base_group))
        checkpoint = f"{base_group}:result:{batch.revision}"
        saved = services.outputs.get(batch.run_id, checkpoint)
        if saved:
            produced.append(saved)
            continue
        saved_pending = services.outputs.get(batch.run_id, f"{base_group}:pending-result:{batch.revision}")
        if saved_pending:
            pending.append(saved_pending)
            continue
        prior_card = batch.prior_cards.get(identity)
        repair = batch.run.get("result") or {}
        deferred = (
            repair.get("auto_repair_deferred_ids", [])
            if repair.get("auto_repair_revision") == batch.revision
            else []
        )
        if prior_card and (identity in batch.adopted_ids or identity in deferred):
            produced.append({**prior_card, "retained": True})
            continue
        division = services.outputs.get(batch.run_id, f"{base_group}:division")
        if division:
            if prior_card:
                services._supersede(identity)
            children = [CardTopic.model_validate(row) for row in division["topics"]]
            queue.extend(
                (number, f"{path}.{i}" if path else str(i), child, str(uuid5(UUID(batch.run_id), base_group)))
                for i, child in enumerate(children)
            )
            continue
        if batch.revision:
            group += f":repair:{batch.revision}"
        processed += 1
        if prior_card:
            from hrs_platform.services.models.vision import result_key

            prior_source = services.outputs.get(batch.run_id, prior_card["source_step"])
            prior_units = {row["unit_id"]: row for row in prior_source["units"]}
            prior_card["repair_visual_checks"] = [
                services.outputs.get(
                    batch.run_id, result_key(identity, window["key"], prior_card.get("visual_revision"))
                )
                for window in visual_groups(
                    prior_card.get("original_candidate", prior_card["candidate"]), prior_units
                )
            ]
        try:
            with services.outputs.operation(
                batch.run_id,
                group,
                topic.name,
                kind="group",
                objective=topic.objective,
                parent=parent,
            ) as branch:
                card, previous, source = await synthesize_topic(
                    services,
                    batch,
                    topic=topic,
                    group=group,
                    branch=branch,
                    prior_card=prior_card,
                    number=number,
                    path=path,
                )
                available = source["units"]
                source_step = f"{base_group}:sources:{fingerprint(source)}"
                services.outputs.put(
                    batch.run_id,
                    source_step,
                    source,
                    {"topic": topic.model_dump(), "units": available, "corpus": batch.corpus},
                )
                produced.append(
                    {
                        "id": identity,
                        "topic_index": number,
                        "topic_path": path,
                        "candidate": card.model_dump(mode="json"),
                        "text_check": previous,
                        "source_step": source_step,
                        "visual_revision": fingerprint(
                            {
                                "candidate": card.model_dump(mode="json"),
                                "source": batch.run.get("source"),
                                "conversion": batch.run.get("conversion"),
                                "revision": batch.revision,
                            }
                        ),
                        "parent_node": branch,
                    }
                )
                services.outputs.put(
                    batch.run_id,
                    checkpoint,
                    produced[-1],
                    {"topic": topic.model_dump(), "revision": batch.revision},
                )
        except TaskError as error:
            if error.type not in {
                "model_output_invalid",
                "model_output_limit",
                "card_input_budget",
                "card_evidence_insufficient",
                "model_request_exhausted",
            }:
                raise
            depth = len(path.split(".")) if path else 0
            children = (
                split_topic(topic, batch.all_units)
                if error.type in {"model_output_limit", "card_input_budget"}
                and total_topics < MAX_CARD_TOPICS
                else []
            )
            if children:
                division = {
                    "topics": [child.model_dump() for child in children],
                    "reason": error.type,
                    "source_unit_ids": topic.unit_ids,
                    "depth": depth,
                }
                services.outputs.put(
                    batch.run_id, f"{base_group}:division", division, {"topic": topic.model_dump()}
                )
                split_parent = services.outputs.node(
                    batch.run_id,
                    base_group,
                    kind="group",
                    label=topic.name,
                    objective="原主题超限，已拆分，子主题尚须独立核验。",
                    parent=parent,
                    state="completed",
                    details=division,
                )
                if prior_card:
                    services._supersede(identity)
                queue.extend(
                    (number, f"{path}.{i}" if path else str(i), child, split_parent)
                    for i, child in enumerate(children)
                )
                total_topics += len(children) - 1
                continue
            pending.append(
                {
                    "topic_index": number,
                    "topic_path": path,
                    "name": topic.name,
                    "error_type": error.type,
                    "message": str(error)[:500],
                    "evidence_questions": list(error.details),
                    "unit_ids": topic.unit_ids,
                }
            )
            services.outputs.put(
                batch.run_id,
                f"{base_group}:pending-result:{batch.revision}",
                pending[-1],
                {"topic": topic.model_dump()},
            )
    receipt = {"cards": produced, "pending_topics": pending, "complete": not queue}
    batch_key = batch.generated_key if not queue else batch.generated_key + ":batch:" + fingerprint(receipt)
    services.outputs.put(
        batch.run_id,
        batch_key,
        receipt,
        {"reading_plan": batch.plan.model_dump(), "card_plan": batch.card_plan.model_dump()},
    )
    RunLifecycle(services.engine).transition(batch.run_id, "processing", "vision")
    return {"run_id": batch.run_id, "candidates": len(produced), "batch_key": batch_key, "more": bool(queue)}
