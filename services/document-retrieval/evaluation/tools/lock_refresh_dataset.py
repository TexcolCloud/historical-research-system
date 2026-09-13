"""Lock the two new original-reviewed families before their first search."""

import itertools
import json
from collections import Counter

from lock_dataset import (
    MODULE,
    anchor,
    block_ids,
    digest,
    find_needle,
    identity,
    literal_occurrences,
    load_family,
)

from document_retrieval.records import atomic_json, utc_now


def contexts(source, rows):
    blocks = block_ids(source)
    dependencies = {d for b in source.get("readiness", []) for d in b.get("dependencies", [])}
    selected = [
        s
        for s in rows
        if s["snapshot_id"] == source["snapshot_id"]
        and (identity(s) == identity(source) or block_ids(s) & (blocks | dependencies))
    ]
    # A duplicate note at the same fixed snapshot/page is an alternative copy.
    choices = {}
    for s in selected:
        key = (s["snapshot_id"], s["physical_page"], s["text_sha256"])
        choices.setdefault(key, []).append(anchor(s))
    return [list(values) for values in itertools.product(*choices.values())]


def main():
    original_path = MODULE / "evaluation/development/refresh-original-tasks-v2.json"
    original = json.loads(original_path.read_text("utf-8"))
    output = MODULE / "evaluation/datasets/retrieval-refresh-20260909-v1"
    assert not (output / "lock.json").exists()
    families = {f: load_family(f) for f in ("family-18", "family-19")}
    tasks, references, gaps = [], [], []
    for task in original["tasks"]:
        scopes, groups = [], []
        for family in task["families"]:
            _record, rows, fixed = families[family]
            scopes.append(
                {
                    "family": family,
                    "scope": {
                        "kind": "selected",
                        "sources": [
                            {"snapshot_id": item["snapshot"]["snapshot_id"]} for item in fixed
                        ],
                    },
                }
            )
            pages = task.get("pages_by_family", {}).get(family, task.get("pages", []))
            for needle in task.get("evidence_by_family", {}).get(family, task.get("evidence", [])):
                selected = (
                    rows
                    if task["type"] == "literal"
                    else [s for s in rows if s["physical_page"] in pages]
                )
                found = [
                    (s, m)
                    for s in selected
                    for m in (
                        literal_occurrences(s, needle)
                        if task["type"] == "literal"
                        else [find_needle(s, needle)]
                    )
                    if m
                ]
                if not found:
                    gaps.append(
                        {"id": task["id"], "family": family, "needle": needle, "state": "input_gap"}
                    )
                    continue
                groups.append(
                    {
                        "group_id": f"g{len(groups) + 1:02d}",
                        "family": family,
                        "need": needle,
                        "role": "required_evidence",
                        "alternatives": [
                            {"match": m, "required_context": context}
                            for s, m in found
                            for context in (
                                [[]] if task["type"] == "literal" else contexts(s, rows)
                            )
                        ],
                    }
                )
        driver = {k: task[k] for k in ("id", "type", "question", "query")}
        driver.update(
            {
                "split": "holdout",
                "purpose": "card_input",
                "source_scopes": scopes,
                "mode": {"exact": "exact_quote", "compatible": "compatible_text"}.get(
                    task.get("mode"), "hybrid"
                ),
                "allowed_supplemental": task["type"] == "supplemental",
                "requires_media": False,
                "max_new_searches": 2,
                "max_expansions": 4,
                "max_content_calls": 6,
                "end_condition": "Obtain the stated evidence and source context within the registered bounds; preserve unresolved needs.",
            }
        )
        tasks.append(driver)
        references.append(
            {
                "id": task["id"],
                "split": "holdout",
                "type": task["type"],
                "families": task["families"],
                "groups": groups,
                "tags": [],
                "requires_media": False,
                "source_reviews": {
                    f: families[f][0]["source_review_sha256"] for f in task["families"]
                },
            }
        )
    atomic_json(
        output / "preflight.json",
        {
            "required_positive_count": 12,
            "current_gaps": gaps,
            "checked_at": utc_now(),
            "original_stage_revisions": original["preflight_corrections"],
        },
    )
    assert not gaps, gaps
    assert Counter(t["type"] for t in tasks) == {
        "literal": 2,
        "within_source": 6,
        "supplemental": 4,
    }
    artifacts = {
        "tasks.json": tasks,
        "corpus.json": [f[0] for f in families.values()],
        "references.json": {
            "classification": "machine_reference",
            "original_first": True,
            "reviewer": "active Codex GPT vision model",
            "independent_blind_review": False,
            "human_review": False,
            "sealed_gold": False,
            "scope": "Known original evidence and direct source-block/note context. Not whole-corpus recall or historical truth.",
            "tasks": references,
        },
    }
    hashes = {name: atomic_json(output / name, value) for name, value in artifacts.items()}
    hashes["preflight.json"] = digest(output / "preflight.json")
    registration = MODULE / "evaluation/development/refresh-source-registration-v1.json"
    atomic_json(
        output / "lock.json",
        {
            "dataset_id": output.name,
            "locked_at": utc_now(),
            "files": hashes,
            "task_counts": {"holdout": 12},
            "family_count": 2,
            "original_task_versions": {str(original_path): digest(original_path)},
            "classification": "new_source_family_qualification_after_seen_regression_repairs",
            "heldout_results_seen": False,
            "earlier_40_task_holdout_seen": True,
            "registration": {"path": str(registration), "sha256": digest(registration)},
            "thresholds": {
                "literal_first_batch": "2/2",
                "research_first_batch": "9/10",
                "research_complete": "9/10",
                "within_source_complete": "5/6",
                "supplemental_complete": "4/4",
                "known_evidence_group_coverage": 0.9,
            },
            "capacity": "paused_by_user",
            "source_hashes": {str(p): digest(p) for p in (MODULE / "src").rglob("*.py")},
            "locks": {p.name: digest(p) for p in (MODULE / "uv.lock", MODULE / "pyproject.toml")},
        },
    )
    print(
        json.dumps(
            {
                "dataset": output.name,
                "tasks": len(tasks),
                "groups": sum(len(t["groups"]) for t in references),
                "gaps": len(gaps),
            }
        )
    )


if __name__ == "__main__":
    main()
