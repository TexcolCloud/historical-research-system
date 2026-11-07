from copy import deepcopy

import pytest

from hrs.processing.navigation import resolve_navigation, validate_proposal
from hrs.processing.navigation_matching import target_catalog
from tests.unit.processing.test_navigation_spans import split_proposal
from tests.unit.processing.test_store import complete, document


def test_manual_full_title_choice_outside_top_candidates_keeps_all_fragments(tmp_path):
    store, identity = document(tmp_path, 4)
    texts = [
        "目录\n关于甲乙\n1",
        "工作的决定……12\n2",
        "此前正文\n# 关于甲乙\n12",
        "# 工作的决定\n正文\n13",
    ]
    for number, text in enumerate(texts, start=1):
        complete(store, identity, "ocr", f"ocr-{number}", {"markdown": text})
        complete(
            store,
            identity,
            "review",
            f"review-{number}",
            {
                "verdict": "approved",
                "issues": [],
                "page_observation": {
                    "kind": "toc" if number <= 2 else "text",
                    "printed_label": str(number) if number <= 2 else str(number + 9),
                    "headings": []
                    if number <= 2
                    else ["关于甲乙" if number == 3 else "工作的决定"],
                },
            },
        )
    doc = store.status(identity)
    entry = validate_proposal(doc, [split_proposal()])[0]
    doc["navigation"]["source_entries"] = [entry]
    doc["navigation"] = resolve_navigation(doc)
    doc["navigation"]["entries"][0]["candidates"] = []
    store.checkpoints.write(doc)
    candidate = next(
        record["target"] for record in target_catalog(doc) if record["target"].get("title_spans")
    )
    args = {
        "expected_sha256": doc["navigation"]["sha256"],
        "target_page": 3,
        "target_anchor": candidate["anchor"],
        "target_title": candidate["title"],
        "target_spans": candidate["title_spans"],
        "decision_id": "manual-complete-title",
    }
    linked = store.navigation_decision(identity, entry["id"], **args)
    target = linked["decisions"][entry["id"]]["target"]
    assert [span["page"] for span in target["title_spans"]] == [3, 4]
    assert target["title"] == "关于甲乙工作的决定"
    assert store.navigation_decision(identity, entry["id"], **args) == linked
    altered = deepcopy(args)
    altered["target_spans"][1]["markdown_sha256"] = "f" * 64
    with pytest.raises(ValueError, match="different request"):
        store.navigation_decision(identity, entry["id"], **altered)
    altered.update(expected_sha256=linked["sha256"], decision_id="forged-choice")
    with pytest.raises(ValueError, match="changed"):
        store.navigation_decision(identity, entry["id"], **altered)
