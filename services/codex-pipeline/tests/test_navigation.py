import pytest
from test_store import complete, document

from hrs_codex.navigation import (
    fragment_navigation,
    observation,
    resolve_navigation,
    validate_proposal,
)
from hrs_codex.store import digest


def page(number, text, kind, label=None, headings=None):
    p = {
        "page": number,
        "image_sha256": str(number) * 64,
        "approved": {"markdown": text, "sha256": digest(text)},
    }
    p["observation"] = observation(
        p, {"kind": kind, "printed_label": label, "headings": headings or []}
    )
    return p


def test_exact_navigation_and_conflict_do_not_guess_a_fixed_page_offset():
    doc = {
        "source": {"sha256": "a" * 64},
        "pages": [
            page(1, "目录\n第一章起源 1\n第二章发展 2", "toc"),
            page(2, "# 第一章起源\n\n1", "text", "1", ["第一章起源"]),
            page(3, "# 第二章发展\n\n3", "text", "3", ["第二章发展"]),
        ],
    }
    entries = validate_proposal(
        doc,
        [
            {
                "source_page": 1,
                "title": "第一章起源",
                "target_label": "1",
                "source_anchor": "第一章起源 1",
            },
            {
                "source_page": 1,
                "title": "第二章发展",
                "target_label": "2",
                "source_anchor": "第二章发展 2",
            },
        ],
    )
    doc["navigation"] = {"source_entries": entries}
    result = resolve_navigation(doc)
    assert result["entries"][0]["target"]["physical_page"] == 2
    assert result["entries"][1]["status"] == "conflict"
    assert result["entries"][1]["target"] is None
    assert result["entries"][1]["candidates"][0]["printed_label"] == "3"
    with pytest.raises(ValueError, match="unique source anchor"):
        validate_proposal(
            doc,
            [
                {
                    "source_page": 1,
                    "title": "无出处标题",
                    "target_label": "9",
                    "source_anchor": "缺少",
                }
            ],
        )


def test_directory_parses_before_book_complete_and_targets_resolve_later(tmp_path):
    store, doc_id = document(tmp_path, 2)
    complete(store, doc_id, "ocr", "ocr1", {"markdown": "目录\n起源 1", "page_kind": "toc"})
    complete(
        store,
        doc_id,
        "review",
        "review1",
        {"verdict": "approved", "issues": [], "page_observation": {"kind": "toc", "headings": []}},
    )
    task = store.claim_task(doc_id, "navigation", "nav1")
    assert [p["page"] for p in task["inputs"]["pages"]] == [1]
    result = {
        "input_sha256": task["input_sha256"],
        "entries": [
            {"source_page": 1, "title": "起源", "target_label": "1", "source_anchor": "起源 1"}
        ],
    }
    with pytest.raises(ValueError, match="original images"):
        store.submit_task(doc_id, task["task_id"], task["lease_token"], result)
    store.original_page(doc_id, 1, task_id=task["task_id"], lease_token=task["lease_token"])
    store.submit_task(doc_id, task["task_id"], task["lease_token"], result)
    assert store.status(doc_id)["navigation"]["entries"][0]["status"] == "waiting_evidence"
    complete(store, doc_id, "ocr", "ocr2", {"markdown": "# 起源\n正文[^1]\n[^1]: 注释\n1"})
    complete(
        store,
        doc_id,
        "review",
        "review2",
        {
            "verdict": "approved",
            "issues": [],
            "page_observation": {"kind": "text", "headings": ["起源"], "printed_label": "1"},
        },
    )
    doc = store.status(doc_id)
    assert doc["navigation"]["entries"][0]["target"]["physical_page"] == 2
    assert any(t["stage"] == "organization" and t["state"] == "ready" for t in doc["tasks"])
    entry = doc["navigation"]["entries"][0]
    with pytest.raises(ValueError, match="changed"):
        store.navigation_decision(
            doc_id, entry["id"], expected_sha256="0" * 64, target_page=2, target_anchor="起源"
        )
    nav = store.navigation_decision(
        doc_id,
        entry["id"],
        expected_sha256=doc["navigation"]["sha256"],
        target_page=2,
        target_anchor="起源",
    )
    assert nav["entries"][0]["status"] == "matched_manual"
    assert nav["entries"][0]["decision"]["content_approval"] is False


def test_same_page_multiple_headings_do_not_assign_wrong_directory_owner():
    nav = {
        "entries": [
            {"id": "a", "level": 1, "path": ["甲"], "target": {"physical_page": 2, "start": 0}},
            {"id": "b", "level": 1, "path": ["乙"], "target": {"physical_page": 2, "start": 50}},
        ]
    }
    assert fragment_navigation(nav, [2], [(2, 10, 40)])["toc_entry_id"] == "a"
    assert fragment_navigation(nav, [2], [(2, 55, 80)])["toc_entry_id"] == "b"
    assert fragment_navigation(nav, [2], [(2, 10, 80)])["toc_entry_id"] is None


def test_printed_range_uses_literal_start_label_and_retains_end():
    doc = {
        "source": {"sha256": "a" * 64},
        "pages": [
            page(1, "目录\n起源 1～3", "toc"),
            page(2, "# 起源\n正文\n1", "text", "1", ["起源"]),
        ],
    }
    entries = validate_proposal(
        doc,
        [{"source_page": 1, "title": "起源", "target_label": "1～3", "source_anchor": "起源 1～3"}],
    )
    assert entries[0]["target_label"] == "1" and entries[0]["target_end_label"] == "3"
    doc["navigation"] = {"source_entries": entries}
    assert resolve_navigation(doc)["entries"][0]["target"]["physical_page"] == 2
    entries[0]["target_label"] = "1～3"
    assert resolve_navigation(doc)["entries"][0]["target"]["physical_page"] == 2
