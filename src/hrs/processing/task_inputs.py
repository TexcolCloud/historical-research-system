"""Hash-bound inputs for OCR, independent review, navigation and organization."""

from hrs.processing.hashing import digest


def task_inputs(repository, doc: dict, task: dict) -> dict:
    if task["stage"] == "navigation" and task.get("inputs"):
        return task["inputs"]
    if task["stage"] == "navigation" and task.get("verification_inputs"):
        from copy import deepcopy

        return deepcopy(task["verification_inputs"])
    inputs = {
        "document_id": doc["id"],
        "source_sha256": doc["source"]["sha256"],
        "stage": task["stage"],
    }
    if doc.get("revision") and ("inputs" not in task or "revision" in task["inputs"]):
        inputs["revision"] = doc["revision"]
        inputs["revision_reason"] = doc["revision_history"][-1]["reason"]
    if task["page"] is not None:
        page = doc["pages"][task["page"] - 1]
        if "extraction_plan" in page:
            plan = page["extraction_plan"]
            # Keep already-claimed input contracts stable across an upgrade.
            compact = "inputs" not in task or "extraction_plan_sha256" in task["inputs"]
            inputs["extraction_plan"] = (
                {k: v for k, v in plan.items() if k != "probe_evidence"} if compact else plan
            )
            if compact:
                inputs["extraction_plan_sha256"] = digest(plan)
        inputs.update(
            page=page["page"],
            image=str(repository.evidence_path(doc, page["image"])),
            image_sha256=page["image_sha256"],
        )
        if task["stage"] in {"review", "verify"}:
            text = page["candidate" if task["stage"] == "verify" else "ocr"]
            inputs.update(
                markdown=text["markdown"],
                markdown_sha256=text["sha256"],
                prior_worker_id=text["worker_id"],
            )
            if task["stage"] == "verify":
                inputs["original_markdown"] = page["ocr"]["markdown"]
                inputs["corrections"] = page["candidate"]["corrections"]
    elif task["stage"] == "navigation":
        from hrs.processing.navigation import fingerprint, toc_evidence
        from hrs.processing.navigation_coverage import context_sha256, parse_plan

        inputs["toc_sha256"] = fingerprint(toc_evidence(doc))
        plan = parse_plan(doc, retry_unresolved=task.get("retry_unresolved", False))
        inputs["coverage_contract_version"] = 2
        inputs["toc_candidate_pages"] = plan["core"]
        inputs["toc_context_pages"] = plan["context"]
        inputs["coverage_contexts"] = {
            str(number): context_sha256(doc, number) for number in plan["core"]
        }
        inputs["pages"] = [
            {
                "page": p["page"],
                "image_sha256": p["image_sha256"],
                "markdown_sha256": p["approved"]["sha256"],
                "observation": p.get("observation"),
                "kind": p.get("observation", {}).get("kind", p.get("page_kind", "unknown")),
            }
            for p in doc["pages"]
            if p["page"] in plan["pages"]
        ]
    else:
        inputs["pages"] = [
            {
                "page": p["page"],
                "image": str(repository.evidence_path(doc, p["image"])),
                "image_sha256": p["image_sha256"],
                "markdown": p["approved"]["markdown"],
                "markdown_sha256": p["approved"]["sha256"],
            }
            for p in doc["pages"]
        ]
    return inputs
