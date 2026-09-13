"""Lock original-first references and public fixed inputs before evaluated queries.

The run driver reads tasks.json only. It never imports this reference-building tool.
Reference phrases come from the recorded GPT original review, not search candidates.
"""

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from document_retrieval.records import atomic_json, text_hash, utc_now
from document_retrieval.text import display_text, source_kind
from document_retrieval.upstream import field_allowed

MODULE = Path(__file__).resolve().parents[2]
DATASET = "retrieval-representative-20260908-v6"

# Original-reviewed context requirements that cannot be reduced to a short quote.
EXTRA = {
    "held-r02": {"family-09": ["本表统计对象", "电文往来单位"]},
    "held-r03": {"family-16": ["特此说明", "侨务十五年"]},
    "held-r04": {"family-16": ["特此说明", "侨务十五年"]},
    "held-r13": {"primary-new-fourth-army": ["群众的拥护", "全福建", "闽西南军政委", "原件无标题"]},
    "held-s01": {"family-09": ["本表统计对象", "电文往来单位"]},
    "held-s03": {"family-16": ["星港来客话香港"]},
    "held-s08": {"family-16": ["星港来客话香港"]},
}


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def anchor(source, start=None, end=None):
    start = source["start"] if start is None else start
    end = source["end"] if end is None else end
    value = source["text"][start - source["start"] : end - source["start"]]
    return {
        "snapshot_id": source["snapshot_id"],
        "source_span_ref": source["source_span_ref"],
        "offset_unit": "unicode_code_point",
        "start": start,
        "end": end,
        "text_sha256": text_hash(value),
        "text": value,
        "physical_page": source["physical_page"],
        "kind": source_kind(source),
    }


def identity(source):
    return tuple(source[k] for k in ("snapshot_id", "source_span_ref", "start", "end"))


def block_ids(source):
    return {b["block_id"] for b in source.get("readiness", [])}


def required_context(source, rows):
    selected = {identity(source): source}
    queue = [source]
    while queue:
        current = queue.pop()
        blocks = block_ids(current)
        dependencies = {d for b in current.get("readiness", []) for d in b.get("dependencies", [])}
        for other in rows:
            if other["snapshot_id"] != source["snapshot_id"]:
                continue
            if block_ids(other) & (blocks | dependencies) and identity(other) not in selected:
                selected[identity(other)] = other
                queue.append(other)
    return [
        anchor(s) for s in sorted(selected.values(), key=lambda s: (s["physical_page"], s["start"]))
    ]


def find_needle(source, needle):
    raw = source["text"]
    if needle in raw:
        start = raw.index(needle)
        return anchor(source, source["start"] + start, source["start"] + start + len(needle))
    displayed = display_text(raw)
    if needle in displayed.text:
        start = displayed.text.index(needle)
        left, right = displayed.original_range(start, start + len(needle))
        return anchor(source, source["start"] + left, source["start"] + right)
    return None


def literal_occurrences(source, needle):
    raw = source["text"]
    matches = list(re.finditer(re.escape(needle), raw))
    if matches:
        return [
            anchor(source, source["start"] + m.start(), source["start"] + m.end()) for m in matches
        ]
    displayed = display_text(raw)
    return [
        anchor(source, source["start"] + left, source["start"] + right)
        for m in re.finditer(re.escape(needle), displayed.text)
        for left, right in [displayed.original_range(m.start(), m.end())]
    ]


def load_family(family):
    folder = MODULE / "state/real-corpus" / family
    split = "calibration" if family.startswith("family-") and int(family[7:]) <= 8 else "holdout"
    fixed_path = folder / "fixed-inputs-v4.json"
    fixed = json.loads(fixed_path.read_text("utf-8"))
    receipt = json.loads((folder / "input.json").read_text("utf-8"))
    review_path = folder / "gpt-source-review-v3.json"
    review = json.loads(review_path.read_text("utf-8"))
    assert review["original_first"] and not review["human_review"] and not review["sealed_gold"]
    assert (
        digest(Path(receipt["source"]))
        == receipt["source_sha256"]
        == review["original_source_sha256"]
    )
    for image in review["original_evidence"]:
        assert digest(Path(image["path"])) == image["sha256"]
    allowed = set()
    for item in fixed:
        for batch in item["evaluation_usage"]["card_input"]:
            for decision in batch["items"]:
                if field_allowed(decision, ["text", "provenance"]):
                    allowed.add(identity(decision["reference"]))
    sources = [s for item in fixed for s in item["sources"] if identity(s) in allowed]
    assert all(text_hash(s["text"]) == s["text_sha256"] for s in sources)
    record = {
        "family": family,
        "split": split,
        "source": receipt["source"],
        "source_sha256": receipt["source_sha256"],
        "package_archive_sha256": receipt["archive_sha256"],
        "fixed_inputs_path": str(fixed_path),
        "fixed_inputs_sha256": digest(fixed_path),
        "source_review_path": str(review_path),
        "source_review_sha256": digest(review_path),
        "original_evidence": review["original_evidence"],
        "snapshots": [
            {
                "snapshot_id": item["snapshot"]["snapshot_id"],
                "occurrence_id": item["snapshot"].get("occurrence_id"),
                "adopted_physical_pages": item["evaluation_adopted_pages"],
                "qualified_physical_pages": sorted({s["physical_page"] for s in item["sources"]}),
                "qualified_range_count": len(item["sources"]),
            }
            for item in fixed
        ],
        "human_review": False,
        "sealed_gold": False,
    }
    return record, sources, fixed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=MODULE / "evaluation/datasets" / DATASET)
    args = parser.parse_args()
    assert not (args.output / "lock.json").exists(), "A locked dataset must not be overwritten"
    original_paths = [
        MODULE / "evaluation/development" / n
        for n in ("calibration-original-tasks-v3.json", "holdout-original-tasks-v2.json")
    ]
    documents = [json.loads(p.read_text("utf-8")) for p in original_paths]
    variants_path = MODULE / "evaluation/development/source-query-variants-v1.json"
    variants = json.loads(variants_path.read_text("utf-8"))
    originals = [t for d in documents for t in d["tasks"]]
    assert len(originals) == 60
    families = {f: load_family(f) for f in sorted({f for t in originals for f in t["families"]})}
    assert len(families) >= 20
    tasks, references, gaps = [], [], []
    for task in originals:
        required = []
        scopes = []
        for family in task["families"]:
            _, rows, fixed = families[family]
            pages = task.get("pages_by_family", {}).get(family, task.get("pages", []))
            selected_snapshots = [
                item["snapshot"]["snapshot_id"]
                for item in fixed
                if set(item["evaluation_adopted_pages"]) & set(pages)
            ]
            assert selected_snapshots, (task["id"], family)
            scopes.append(
                {
                    "family": family,
                    "scope": {
                        "kind": "selected",
                        "sources": [{"snapshot_id": s} for s in selected_snapshots],
                    },
                }
            )
            needed = task.get("evidence_by_family", {}).get(family, task.get("evidence", []))
            extras = EXTRA.get(task["id"], {}).get(family, [])
            for needle in needed + extras:
                selected = (
                    rows
                    if needle in extras and family == "family-16"
                    else [s for s in rows if s["physical_page"] in pages]
                )
                if task["type"] == "literal":
                    # The accepted criterion permits any verified occurrence of the
                    # literal within the task's selected source, not just seed pages.
                    selected = [s for s in rows if s["snapshot_id"] in selected_snapshots]
                    found = [
                        (s, match) for s in selected for match in literal_occurrences(s, needle)
                    ]
                else:
                    found = [(s, find_needle(s, needle)) for s in selected]
                found = [(s, match) for s, match in found if match]
                if not found:
                    gaps.append(
                        {
                            "id": task["id"],
                            "family": family,
                            "needle": needle,
                            "state": "input_gap",
                            "reason": "No qualified fixed source contains the original reference.",
                        }
                    )
                    continue
                # For headings repeated in body, all verified occurrences are alternatives.
                required.append(
                    {
                        "group_id": f"g{len(required) + 1:02d}",
                        "family": family,
                        "need": needle,
                        "role": "necessary_context" if needle in extras else "required_evidence",
                        "alternatives": [
                            {
                                "match": match,
                                "required_context": []
                                if task["type"] == "literal"
                                else required_context(s, rows),
                            }
                            for s, match in found
                        ],
                    }
                )
            if task["id"] in ("held-r02", "held-s01") and family == "family-09":
                for s in rows:
                    if s["physical_page"] in (3, 4) and source_kind(s) == "table":
                        required.append(
                            {
                                "group_id": f"g{len(required) + 1:02d}",
                                "family": family,
                                "need": "Original full table part, with all column headers and units",
                                "role": "necessary_table_context",
                                "alternatives": [
                                    {
                                        "match": anchor(s),
                                        "required_context": required_context(s, rows),
                                    }
                                ],
                            }
                        )
        split = families[task["families"][0]][0]["split"]
        assert all(families[f][0]["split"] == split for f in task["families"])
        driver_task = {k: task[k] for k in ("id", "type", "question", "query")}
        if task["id"] in variants["queries"]:
            assert len(scopes) == len(variants["queries"][task["id"]])
            for scope, query in zip(scopes, variants["queries"][task["id"]], strict=True):
                scope["query"] = query
        driver_task["question"] = variants["question_clarifications"].get(
            task["id"], task["question"]
        )
        driver_task.update(
            {
                "split": split,
                "purpose": "card_input",
                "source_scopes": scopes,
                "mode": {"exact": "exact_quote", "compatible": "compatible_text"}.get(
                    task.get("mode"), "hybrid"
                ),
                "allowed_supplemental": task["type"] == "supplemental",
                "requires_media": "media" in task.get("tags", []),
                "max_new_searches": 2,
                "max_expansions": 4,
                "max_content_calls": 6,
                "end_condition": "Obtain the task's stated evidence and source context within the bounded tool sequence; preserve any unresolved issue.",
            }
        )
        tasks.append(driver_task)
        references.append(
            {
                "id": task["id"],
                "split": split,
                "type": task["type"],
                "families": task["families"],
                "tags": task.get("tags", []),
                "groups": required,
                "requires_media": driver_task["requires_media"],
                "source_reviews": {
                    f: families[f][0]["source_review_sha256"] for f in task["families"]
                },
            }
        )
    old_gaps = [
        a for d in documents for a in d.get("corrections", []) if a["classification"] == "input_gap"
    ]
    atomic_json(
        args.output / "preflight.json",
        {
            "required_positive_count": 60,
            "current_gaps": gaps,
            "registered_original_input_gaps": old_gaps,
            "checked_at": utc_now(),
        },
    )
    assert not gaps, gaps
    assert Counter(t["split"] for t in tasks) == {"calibration": 20, "holdout": 40}
    for split, literal, within, supplemental in (("calibration", 8, 8, 4), ("holdout", 12, 16, 12)):
        assert Counter(t["type"] for t in tasks if t["split"] == split) == {
            "literal": literal,
            "within_source": within,
            "supplemental": supplemental,
        }
    held = [t for t in tasks if t["split"] == "holdout" and t["type"] == "literal"]
    assert Counter(t["mode"] for t in held) == {"exact_quote": 6, "compatible_text": 6}
    artifacts = {
        "tasks.json": tasks,
        "corpus.json": [entry[0] for entry in families.values()],
        "references.json": {
            "classification": "machine_reference",
            "reviewer": "active Codex GPT vision model",
            "original_first": True,
            "independent_blind_review": False,
            "human_review": False,
            "sealed_gold": False,
            "scope": "Known original evidence groups only, not whole-corpus recall or historical-truth adjudication.",
            "tasks": references,
        },
    }
    hashes = {name: atomic_json(args.output / name, value) for name, value in artifacts.items()}
    hashes["preflight.json"] = digest(args.output / "preflight.json")
    lock = {
        "dataset_id": DATASET,
        "locked_at": utc_now(),
        "files": hashes,
        "task_counts": dict(Counter(t["split"] for t in tasks)),
        "family_count": len(families),
        "original_task_versions": {str(p): digest(p) for p in original_paths},
        "calibration_results_seen": True,
        "heldout_results_seen": False,
        "supersedes_dataset": "retrieval-representative-20260908-v5",
        "reference_correction": "Literal tasks accept any original-reviewed, purpose-qualified occurrence of the same registered phrase within the unchanged selected snapshots. Seed physical pages do not impose an unstated unique-location requirement. All literal tasks and all compared models receive the same rule; research context requirements are unchanged.",
        "preceding_attempt": "evaluation/runs/cal-bge-default-20260908-v2/run.json",
        "source_query_variants": {"path": str(variants_path), "sha256": digest(variants_path)},
        "separate_development_functional_queries": "Linux six-task and engineering checks are separately registered; no held-out retrieval has run.",
        "capacity": "paused_by_user",
        "source_hashes": {str(p): digest(p) for p in (MODULE / "src").rglob("*.py")},
        "locks": {p.name: digest(p) for p in (MODULE / "uv.lock", MODULE / "pyproject.toml")},
    }
    atomic_json(args.output / "lock.json", lock)
    print(
        json.dumps(
            {
                "dataset": DATASET,
                "families": len(families),
                "tasks": len(tasks),
                "required_groups": sum(len(t["groups"]) for t in references),
                "input_gaps_preserved": len(old_gaps),
            }
        )
    )


if __name__ == "__main__":
    main()
