"""Cross-page navigation keeps literal page fragments instead of rewriting source text."""

from copy import deepcopy

import pytest

from hrs.processing.navigation import resolve_navigation, validate_proposal
from hrs.processing.navigation_matching import target_catalog
from hrs.processing.navigation_verification import (
    apply_link_verifications,
    freeze_link_observations,
    prepare_link_verification,
    verification_key,
)
from tests.unit.processing.test_navigation import page


def cross_page_document():
    return {
        "id": "split-source",
        "source": {"sha256": "a" * 64},
        "pages": [
            page(1, "目录\n关于甲乙\n1", "toc", "1"),
            page(2, "工作的决定……12\n2", "toc", "2"),
            page(3, "此前正文\n\n# 关于甲乙\n12", "text", "12", ["关于甲乙"]),
            page(4, "# 工作的决定\n（一九四五年九月十日）\n正文\n13", "text", "13", ["工作的决定"]),
        ],
        "tasks": [],
    }


def split_proposal():
    return {
        "title": "关于甲乙工作的决定",
        "target_label": "12",
        "source_spans": [
            {"page": 1, "anchor": "关于甲乙"},
            {"page": 2, "anchor": "工作的决定……12"},
        ],
    }


def test_toc_title_and_label_can_use_adjacent_source_fragments():
    doc = cross_page_document()
    before = deepcopy(doc["pages"])
    entry = validate_proposal(doc, [split_proposal()])[0]
    assert entry["title"] == "关于甲乙工作的决定"
    assert entry["source_page"] == 1 and entry["source_anchor"] == "关于甲乙"
    assert [span["page"] for span in entry["source_spans"]] == [1, 2]
    for span in entry["source_spans"]:
        source = doc["pages"][span["page"] - 1]
        assert source["approved"]["markdown"][span["start"] : span["end"]] == span["anchor"]
        assert span["markdown_sha256"] == source["approved"]["sha256"]
        assert span["image_sha256"] == source["image_sha256"]
    assert doc["pages"] == before


def test_cross_page_fragments_allow_roman_folios_and_an_explicit_toc_running_header():
    doc = cross_page_document()
    doc["pages"][0] = page(1, "目录\n关于甲乙\nIII", "toc", "III")
    doc["pages"][1] = page(2, "目录（续）\n工作的决定……12\nIV", "toc", "IV")
    entry = validate_proposal(doc, [split_proposal()])[0]
    assert [span["anchor"] for span in entry["source_spans"]] == ["关于甲乙", "工作的决定……12"]


def test_cross_page_fragments_allow_only_source_bound_decorated_folios():
    doc = cross_page_document()
    doc["pages"][0] = page(1, "目录\n关于甲乙\n· 1 ·", "toc", "1")
    doc["pages"][1] = page(2, "· 2 ·\n工作的决定……12", "toc", "2")
    assert len(validate_proposal(doc, [split_proposal()])[0]["source_spans"]) == 2


@pytest.mark.parametrize(
    ("text", "label", "line"),
    [
        ("目录\n· 1 ·\n甲……1", "1", "· 1 ·"),
        ("目录\n· 1 ·", None, "· 1 ·"),
        ("目录\n· 1 ·", "2", "· 1 ·"),
        ("目录\n……1", "1", "……1"),
        ("目录\n甲……1", "1", "甲……1"),
    ],
)
def test_page_folio_requires_boundary_full_line_and_current_verified_label(text, label, line):
    from hrs.processing.navigation_spans import is_page_folio

    p = page(1, text, "toc")
    p["observation"]["printed_label"] = label
    start = text.index(line)
    assert not is_page_folio(p, start, start + len(line))


@pytest.mark.parametrize("mutation", ["gap", "offset", "hash", "interior", "order"])
def test_source_fragments_reject_unbound_or_unrelated_splicing(mutation):
    doc = cross_page_document()
    proposal = split_proposal()
    if mutation == "gap":
        proposal["source_spans"][1]["page"] = 4
    elif mutation == "offset":
        proposal["source_spans"][0]["start"] = 1
    elif mutation == "hash":
        proposal["source_spans"][1]["markdown_sha256"] = "b" * 64
    elif mutation == "interior":
        doc["pages"][0] = page(1, "目录\n关于甲乙\n另一条目录……3\n1", "toc", "1")
    else:
        proposal["source_spans"].reverse()
    with pytest.raises(ValueError):
        validate_proposal(doc, [proposal])


def test_full_body_title_shared_catalog_uses_both_pages_and_needs_original_verification():
    doc = cross_page_document()
    entry = validate_proposal(doc, [split_proposal()])[0]
    doc["navigation"] = {"source_entries": [entry]}
    full = [row for row in target_catalog(doc) if row["title"] == "关于甲乙工作的决定"]
    assert len(full) == 1
    target = full[0]["target"]
    assert target["physical_page"] == 3 and target["anchor"] == "关于甲乙"
    assert target["title"] == entry["title"]
    assert [span["page"] for span in target["title_spans"]] == [3, 4]
    row = resolve_navigation(doc)["entries"][0]
    assert row["status"] == "conflict"
    assert any(candidate.get("title") == entry["title"] for candidate in row["candidates"])


def test_plain_title_fragments_are_recalled_only_for_a_retained_complete_title():
    doc = cross_page_document()
    doc["pages"][2] = page(3, "此前正文\n\n关于甲乙\n12", "text", "12")
    doc["pages"][3] = page(4, "工作的决定\n正文\n13", "text", "13")
    assert not any(row["target"].get("title_spans") for row in target_catalog(doc))
    entry = validate_proposal(doc, [split_proposal()])[0]
    doc["navigation"] = {"source_entries": [entry]}
    found = [row for row in target_catalog(doc) if row["target"].get("title_spans")]
    assert len(found) == 1
    assert found[0]["target"]["anchor"] == "关于甲乙"


def test_verification_freezes_every_source_and_target_fragment():
    doc = cross_page_document()
    entry = validate_proposal(doc, [split_proposal()])[0]
    doc["navigation"] = {"source_entries": [entry]}
    doc["navigation"] = resolve_navigation(doc)
    assert prepare_link_verification(doc, requested=True)
    task = doc["tasks"][0]
    task["inputs"] = deepcopy(task["verification_inputs"])
    assert [p["page"] for p in task["inputs"]["pages"]] == [1, 2, 3, 4]
    task["image_accesses"] = {
        str(p["page"]): {"receipt": f"read-{p['page']}", "image_sha256": p["image_sha256"]}
        for p in task["inputs"]["pages"]
    }
    readings = [
        {
            "page": p["page"],
            "image_receipt": f"read-{p['page']}",
            "image_sha256": p["image_sha256"],
            "observations": ["先看原页片段"],
            "unresolved": [],
        }
        for p in task["inputs"]["pages"]
    ]
    freeze_link_observations(doc, task, readings)
    candidate = next(c for c in task["inputs"]["entries"][0]["candidates"] if c.get("title_spans"))
    result = {
        "links": [{"entry_id": entry["id"], "target_page": 3, "target_anchor": candidate["anchor"]}]
    }
    apply_link_verifications(
        doc, task, result, {"approval_type": "machine", "human_approval": False}
    )
    assert len(doc["navigation"]["link_verifications"][entry["id"]]["target"]["title_spans"]) == 2
    assert resolve_navigation(doc)["entries"][0]["status"] == "matched_verified"
    # Structural recovery can reveal a stronger-looking candidate without changing
    # the original source/target pixels inspected by the independent reviewer.
    doc["pages"].append(
        page(5, "# 关于甲乙工作的决定\n正文\n12", "text", "12", ["关于甲乙工作的决定"])
    )
    reranked = resolve_navigation(doc)["entries"][0]
    assert reranked["status"] == "matched_verified"
    assert reranked["target"]["physical_page"] == 3
    changed_source = deepcopy(doc)
    changed_source["pages"][1] = page(2, "目录（续）\n工作的决定……12\n2", "toc", "2")
    changed_source["navigation"]["source_entries"] = validate_proposal(
        changed_source, [split_proposal()]
    )
    assert resolve_navigation(changed_source)["entries"][0]["status"] != "matched_verified"
    changed = deepcopy(doc)
    changed["pages"][3]["image_sha256"] = "f" * 64
    with pytest.raises(ValueError, match="changed"):
        apply_link_verifications(changed, task, result, {})
    assert verification_key(entry, [candidate]) != verification_key(
        entry,
        [
            {
                **candidate,
                "title_spans": [{**s, "image_sha256": "f" * 64} for s in candidate["title_spans"]],
            }
        ],
    )


def test_legacy_single_page_entry_identity_and_manual_decision_survive():
    doc = cross_page_document()
    single = {
        "source_page": 2,
        "title": "工作的决定",
        "target_label": "12",
        "source_anchor": "工作的决定……12",
    }
    previous = validate_proposal(doc, [single])[0]
    doc["navigation"] = {
        "source_entries": [previous],
        "decisions": {previous["id"]: {"final": True}},
    }
    again = validate_proposal(doc, [single])[0]
    assert again["id"] == previous["id"]
    assert doc["navigation"]["decisions"][previous["id"]] == {"final": True}


def test_reparse_preserves_a_current_long_source_anchor_and_its_identity():
    doc = cross_page_document()
    doc["pages"][1] = page(2, "工作的决定……12\n2", "toc", "2")
    proposal = {
        "source_page": 2,
        "source_anchor": "工作的决定……12\n2",
        "title": "工作的决定",
        "target_label": "12",
    }
    old = validate_proposal(doc, [proposal])[0]
    doc["navigation"] = {"source_entries": [old]}
    new = validate_proposal(doc, [{**proposal, "source_anchor": "工作的决定……12"}])[0]
    assert new["id"] == old["id"]
    assert new["source_anchor"] == old["source_anchor"]
    assert new["source_spans"] == old["source_spans"]


def test_full_cross_page_source_does_not_expand_a_prior_human_partial_title_scope():
    doc = cross_page_document()
    doc["pages"][0] = page(1, "目录\n关于第一\n1", "toc", "1")
    doc["pages"][1] = page(2, "项工作的决定……一\n2", "toc", "2")
    old = validate_proposal(
        doc,
        [
            {
                "source_page": 1,
                "source_anchor": "关于第一",
                "title": "关于第",
                "target_label": "一",
            }
        ],
    )[0]
    decision = {"source_sha256": old["source_sha256"], "target": None, "final": True}
    doc["navigation"] = {"source_entries": [old], "decisions": {old["id"]: deepcopy(decision)}}
    new = validate_proposal(
        doc,
        [
            {
                "title": "关于第一项工作的决定",
                "target_label": "一",
                "source_spans": [
                    {"page": 1, "anchor": "关于第一"},
                    {"page": 2, "anchor": "项工作的决定……一"},
                ],
            }
        ],
    )[0]
    assert new["id"] != old["id"]
    assert doc["navigation"]["source_entries"][0] == old
    assert doc["navigation"]["decisions"][old["id"]] == decision
