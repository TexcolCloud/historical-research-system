"""Apply original-image observations before final narrative approval."""

from hrs_platform.domain import prompts
from hrs_platform.domain.card_rules import (
    card_stage,
    repair_sources,
    validate_check,
    validate_final_candidate,
    visual_groups,
)
from hrs_platform.domain.errors import TaskError
from hrs_platform.domain.generation_contracts import CardDraft
from hrs_platform.services.books import get_run
from hrs_platform.services.cards.reading import (
    card_model,
    check_candidate_coverage,
    passed,
    scoped_check,
)
from hrs_platform.services.runs.lifecycle import RunLifecycle
from hrs_platform.services.runs.outputs import fingerprint


def check_images(services, run_id, batch_key=None):
    from hrs_platform.services.models.vision import VisualReview

    run = get_run(services.engine, run_id)
    generated = services.outputs.get(run_id, batch_key or card_stage(run, "generated-cards"))
    bundle = services.review.read_json(run["conversion"])
    reviewer = VisualReview(services.settings, services.engine)
    for card in generated["cards"]:
        if card.get("retained"):
            continue
        source = services.outputs.get(run_id, card["source_step"])
        units = {row["unit_id"]: row for row in source["units"]}
        for group in visual_groups(card["candidate"], units):
            try:
                reviewer.check(run, bundle, card, group)
            except TaskError as error:
                if error.type not in {"visual_output_invalid", "original_missing"}:
                    raise
                services.outputs.put(
                    run_id,
                    f"visual-error:{card['id']}:{card.get('visual_revision', 'original')}:{group['key']}",
                    {"error_type": error.type, "message": str(error)[:500]},
                    {"card": card},
                )
    return {"run_id": run_id}


async def finalize(services, run_id, batch_key=None):
    """Reconcile narrative with original checks; keep quotations, anchors and readings immutable."""
    from hrs_platform.services.models.vision import result_key

    final_stage = (
        batch_key + ":finalized"
        if batch_key
        else card_stage(get_run(services.engine, run_id), "finalized-cards")
    )
    if services.outputs.get(run_id, final_stage):
        return {"run_id": run_id}
    RunLifecycle(services.engine).transition(run_id, "processing", "finalization")
    generated = services.outputs.get(
        run_id, batch_key or card_stage(get_run(services.engine, run_id), "generated-cards")
    )
    sources = {card["id"]: services.outputs.get(run_id, card["source_step"]) for card in generated["cards"]}
    snapshot = services.outputs.get(run_id, "card-reading-sources:v2")
    all_assigned = (
        {unit["unit_id"] for unit in snapshot["units"]}
        if snapshot
        else {identity for source in sources.values() for identity in source["assigned_unit_ids"]}
    )
    finalized = []
    for card in generated["cards"]:
        if card.get("retained"):
            finalized.append(card)
            continue
        source = sources[card["id"]]
        units = {row["unit_id"]: row for row in source["units"]}
        checks = [
            services.outputs.get(run_id, result_key(card["id"], group["key"], card.get("visual_revision")))
            for group in visual_groups(card["candidate"], units)
        ]
        if (
            card["text_check"]["conclusion"] != "pass"
            or not checks
            or not all(
                check and all(item["result"] == "verified" for item in check["items"]) for check in checks
            )
        ):
            failures = [
                services.outputs.get(
                    run_id,
                    f"visual-error:{card['id']}:{card.get('visual_revision', 'original')}:{group['key']}",
                )
                for group in visual_groups(card["candidate"], units)
            ]
            finalized.append({**card, "processing_error": next((error for error in failures if error), None)})
            continue
        final_key = f"final-card:{card['id']}:{fingerprint({'card': card, 'checks': checks})}"
        saved = services.outputs.get(run_id, final_key)
        if saved:
            finalized.append(saved)
            continue
        try:
            original = CardDraft.model_validate(card["candidate"])
            candidate = original
            referenced = {identity for item in original.items for identity in item.source_unit_ids}
            referenced.update(selection.unit_id for item in original.items for selection in item.selections)
            review_units = [
                unit
                for unit in source["units"]
                if unit["unit_id"] in referenced or unit["unit_id"] not in source["assigned_unit_ids"]
            ]
            research_state = {
                "assigned_unit_ids": source["assigned_unit_ids"],
                "available_units_cover_whole_book": all_assigned
                <= {unit["unit_id"] for unit in review_units},
                "scope": "指定分工与实际取得的上下文是不同范围。已提供的邻章可用于回答问题，不得误称未提供。",
            }
            final_receipt = None
            for revision in range(3):
                key = f"原图后定稿v3:{card['id']}:{revision}:{card.get('visual_revision', 'original')}"
                payload = {
                    "candidate": candidate.model_dump(mode="json"),
                    "source_units": review_units,
                    "original_checks": checks,
                    "research_state": research_state,
                    "evidence_research": source.get("evidence_research", {}).get("assessment"),
                }
                final_check = await card_model(
                    services.models,
                    run_id,
                    key,
                    prompts.FINAL_CHECK,
                    payload,
                    scoped_check([item.item_id for item in candidate.items]),
                    model=services.settings.reasoning_model,
                    parent=card["parent_node"],
                    validate=lambda value, candidate=candidate: validate_check(value, candidate),
                )
                final_receipt = final_check.model_dump(mode="json")
                # Revised narrative can drop an unquoted limit. The initial source
                # receipt cannot approve changed claims, even with frozen quotations.
                if passed(final_check) and candidate != original:
                    source_checks = await check_candidate_coverage(
                        services.models,
                        run_id,
                        f"定稿完整来源:{card['id']}",
                        candidate,
                        [unit for unit in source["units"] if unit["unit_id"] in source["assigned_unit_ids"]],
                        card["parent_node"],
                        source.get("topic", {}).get("objective", candidate.title),
                        [
                            unit
                            for unit in source["units"]
                            if unit["unit_id"] not in source["assigned_unit_ids"]
                        ],
                        research_state,
                    )
                    final_receipt["source_checks"] = [
                        result.model_dump(mode="json") for result in source_checks
                    ]
                    if not source_checks or not all(passed(result) for result in source_checks):
                        final_receipt["conclusion"] = "needs_revision"
                if (final_receipt["conclusion"] == "pass" and passed(final_check)) or revision == 2:
                    break
                chosen = {unit["unit_id"] for unit in review_units}
                repair_sources(final_receipt, source["units"], chosen)
                review_units = [unit for unit in source["units"] if unit["unit_id"] in chosen]
                payload["source_units"] = review_units
                candidate = await card_model(
                    services.models,
                    run_id,
                    key + ":修订",
                    prompts.FINALIZE,
                    {**payload, "previous_check": final_receipt},
                    CardDraft,
                    model=services.settings.reasoning_model,
                    parent=card["parent_node"],
                    validate=lambda value, original=original, source=source: validate_final_candidate(
                        value, original, source["units"]
                    ),
                )
            finalized.append(
                {
                    **card,
                    "candidate": candidate.model_dump(mode="json"),
                    "text_check": final_receipt,
                    "initial_text_check": card["text_check"],
                    "original_candidate": card["candidate"],
                }
            )
        except TaskError as error:
            if error.type not in {
                "model_output_invalid",
                "model_output_limit",
                "card_input_budget",
                "model_request_exhausted",
            }:
                raise
            finalized.append(
                {**card, "processing_error": {"error_type": error.type, "message": str(error)[:500]}}
            )
        services.outputs.put(run_id, final_key, finalized[-1], {"card": card, "checks": checks})
    services.outputs.put(
        run_id,
        final_stage,
        {
            "cards": finalized,
            "pending_topics": generated.get("pending_topics", []),
            "complete": generated.get("complete", True),
        },
        {"generated": generated},
    )
    return {"run_id": run_id}
