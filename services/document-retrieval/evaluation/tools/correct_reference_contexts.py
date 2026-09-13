"""Append an original-audited reference correction, without accessing run results."""

import hashlib
import itertools
import json
import shutil
from collections import defaultdict
from pathlib import Path

from lock_dataset import MODULE, anchor

from document_retrieval.records import atomic_json, fingerprint, utc_now


def load(path):
    return json.loads(path.read_text("utf-8"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def content_key(row):
    return row["snapshot_id"], row["physical_page"], row["text_sha256"]


def main():
    old = MODULE / "evaluation/datasets/retrieval-representative-20260908-v6"
    new = old.with_name("retrieval-representative-20260909-v7")
    assert not new.exists()
    new.mkdir()
    previous = load(old / "lock.json")
    for name, digest in previous["files"].items():
        assert sha(old / name) == digest
        shutil.copy2(old / name, new / name)
    aliases = defaultdict(dict)
    for family in load(old / "corpus.json"):
        for fixed in load(Path(family["fixed_inputs_path"])):
            for source in fixed["sources"]:
                aliases[content_key(source)][fingerprint(anchor(source))] = anchor(source)
    references = load(old / "references.json")
    changes = []
    for task in references["tasks"]:
        before = fingerprint(task)
        removed = []
        for group in task["groups"]:
            expanded = {}
            for alternative in group["alternatives"]:
                contexts = []
                for context in alternative["required_context"]:
                    if (
                        task["id"] in ("held-r02", "held-s01")
                        and group["family"] == "family-09"
                        and context["kind"] == "table"
                        and context["physical_page"] in (2, 7)
                    ):
                        removed.append(context)
                    else:
                        contexts.append(context)
                choices = {}
                for context in contexts:
                    matches = list(aliases[content_key(context)].values())
                    duplicate_note = len(matches) > 1 and any(s["kind"] == "note" for s in matches)
                    key = content_key(context) if duplicate_note else fingerprint(context)
                    choices[key] = matches if duplicate_note else [context]
                for context_set in itertools.product(*choices.values()):
                    candidate = {**alternative, "required_context": list(context_set)}
                    expanded[fingerprint(candidate)] = candidate
            group["alternatives"] = list(expanded.values())
        if fingerprint(task) != before:
            changes.append(
                {
                    "task_id": task["id"],
                    "previous_sha256": before,
                    "corrected_sha256": fingerprint(task),
                    "removed_unrelated_table_anchors": list(
                        {fingerprint(a): a for a in removed}.values()
                    ),
                }
            )
    images = [
        "family-16-0002.png",
        "family-09-0003.png",
        "family-09-tables-0004.png",
    ]
    audit = {
        "classification": "machine_review",
        "reviewer": "active Codex GPT vision model",
        "original_first": True,
        "prior_original_reviews_carried_and_pages_rechecked": True,
        "reviewed_at": utc_now(),
        "human_review": False,
        "sealed_gold": False,
        "independent_blind_review": False,
        "supersedes": {
            "path": str(old / "references.json"),
            "sha256": sha(old / "references.json"),
        },
        "original_evidence": [
            {
                "path": str(MODULE / "evaluation/development/original-pages" / name),
                "sha256": sha(MODULE / "evaluation/development/original-pages" / name),
            }
            for name in images
        ],
        "observations": [
            "Original family-16 page 2 has one footnote below the Asian and European population tables. It cites 侨务十五年 and states that totals disagree and were not altered. Identical copies in a page span and a separate note span are alternative representations, not two independent requirements.",
            "Original family-09 pages 3-4 show Table 2, its repeated column headers, totals/percentages, scope footnote 6 and three counting rules. Tables 1 and 3 are not parts of Table 2 and are not necessary context for these two tasks. Their accidental inclusion came from transitive structural dependencies.",
            "Every original evidence group, both complete Table 2 parts, all quoted figures, methodology and scope caveats remain required. Exact duplicate-note alternatives are restricted to the same fixed snapshot, physical page and full-text SHA256.",
            "This tool never opens evaluation run outputs. All affected model variants and original/regression runs must receive the same appended scoring version. Original references and failures are retained.",
        ],
        "changes": changes,
        "scope": "Reference context representation and table scope; no retrieval parameter or frozen extraction change.",
    }
    atomic_json(new / "context-correction-review.json", audit)
    references["context_correction"] = {
        "path": str(new / "context-correction-review.json"),
        "sha256": sha(new / "context-correction-review.json"),
    }
    atomic_json(new / "references.json", references)
    lock = {
        **previous,
        "dataset_id": new.name,
        "created_at": utc_now(),
        "locked_at": utc_now(),
        "heldout_results_seen": True,
        "classification": "corrected_references_for_seen_regression_tasks",
        "reference_correction": "Identical same-page note representations are alternatives; unrelated Tables 1 and 3 are excluded from Table 2 context. All original task facts, Table 2 parts, method and scope remain required. Preserve and uniformly rescore earlier results.",
        "separate_development_functional_queries": "The original holdout has already been run. These corrected references are for seen regression tasks and do not establish fresh unseen validation.",
        "source_hashes": {str(p): sha(p) for p in (MODULE / "src").rglob("*.py")},
        "supersedes": {"dataset_id": previous["dataset_id"], "lock_sha256": sha(old / "lock.json")},
    }
    lock["files"] = {
        name: sha(new / name) for name in [*previous["files"], "context-correction-review.json"]
    }
    atomic_json(new / "lock.json", lock)
    print(
        json.dumps(
            {
                "dataset": new.name,
                "changed_tasks": [r["task_id"] for r in changes],
                "tasks_unchanged": sha(old / "tasks.json") == sha(new / "tasks.json"),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
