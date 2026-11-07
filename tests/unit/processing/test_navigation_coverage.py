from copy import deepcopy

import pytest

from hrs.processing.hashing import digest
from hrs.processing.navigation import observation, validate_proposal
from hrs.processing.task_results import apply_navigation


def page(number, text, kind="toc"):
    value = {
        "page": number,
        "image_sha256": str(number) * 64,
        "approved": {"markdown": text, "sha256": digest(text)},
    }
    value["observation"] = observation(value, {"kind": kind})
    return value


def task(doc, numbers):
    return {
        "task_id": "parser",
        "inputs": {
            "toc_sha256": "old",
            "coverage_contract_version": 1,
            "toc_candidate_pages": numbers,
            "pages": [
                {"page": p["page"], "markdown_sha256": p["approved"]["sha256"]}
                for p in doc["pages"]
                if p["page"] in numbers
            ],
        },
        "image_accesses": {str(n): {} for n in numbers},
    }


def proposal(number, title, label):
    return {
        "source_page": number,
        "source_anchor": f"{title}……{label}",
        "title": title,
        "target_label": label,
    }


def test_directory_parse_cannot_mark_omitted_pages_complete():
    doc = {"source": {"sha256": "a" * 64}, "pages": [page(1, "目录\n甲……1"), page(2, "乙……2")]}
    with pytest.raises(ValueError, match="coverage"):
        apply_navigation(doc, task(doc, [1, 2]), {"entries": []}, {})


def test_partial_parse_preserves_existing_human_binding():
    doc = {"source": {"sha256": "a" * 64}, "pages": [page(1, "目录\n甲……1"), page(2, "乙……2")]}
    first = validate_proposal(doc, [proposal(1, "甲", "1")])[0]
    doc["navigation"] = {
        "source_entries": [first],
        "decisions": {
            first["id"]: {"source_sha256": first["source_sha256"], "target": None, "final": True}
        },
    }
    before = deepcopy(doc["navigation"]["decisions"])
    apply_navigation(doc, task(doc, [2]), {"entries": [proposal(2, "乙", "2")]}, {})
    assert len(doc["navigation"]["source_entries"]) == 2
    assert doc["navigation"]["source_entries"][0]["id"] == first["id"]
    assert doc["navigation"]["decisions"] == before


def test_unknown_gap_and_explicit_unresolved_are_distinct_from_complete():
    from hrs.processing.navigation_coverage import coverage_report

    doc = {
        "pages": [page(1, "目录\n甲……1"), page(2, "乙……2", "unknown"), page(3, "丙……3")],
        "tasks": [],
    }
    report = coverage_report(doc)
    assert report["missing_pages"] == [1, 2, 3]
    assert report["coverage_complete"] is False
    assert report["pages_complete"] is True


def test_partial_entry_cannot_hide_other_visible_labels_or_mark_them_empty():
    doc = {"source": {"sha256": "a" * 64}, "pages": [page(1, "目录\n甲……1\n乙……2\n9")]}
    with pytest.raises(ValueError, match="omits a visible target"):
        apply_navigation(doc, task(doc, [1]), {"entries": [proposal(1, "甲", "1")]}, {})
    with pytest.raises(ValueError, match="visible entries empty"):
        apply_navigation(
            doc,
            task(doc, [1]),
            {"entries": [], "page_coverage": [{"page": 1, "status": "empty", "reason": "空白"}]},
            {},
        )


def test_source_anchor_does_not_need_to_consume_page_footer_whitespace():
    from hrs.processing.navigation_coverage import coverage_report

    doc = {"source": {"sha256": "a" * 64}, "pages": [page(1, "目录\n甲……1\n9")]}
    apply_navigation(doc, task(doc, [1]), {"entries": [proposal(1, "甲", "1")]}, {})
    assert coverage_report(doc)["coverage_complete"] is True


@pytest.mark.parametrize("number", [1, 2, 3])
def test_decorated_verified_folio_is_not_a_directory_target(number):
    from hrs.processing.navigation_coverage import coverage_report

    p = page(1, f"目录\n甲……431\n· {number} ·")
    p["observation"] = observation(p, {"kind": "toc", "printed_label": str(number)})
    doc = {"source": {"sha256": "a" * 64}, "pages": [p]}
    apply_navigation(doc, task(doc, [1]), {"entries": [proposal(1, "甲", "431")]}, {})
    assert coverage_report(doc)["coverage_complete"] is True


@pytest.mark.parametrize("second_label", ["2", "1"])
def test_one_source_entry_cannot_hide_another_visible_target(second_label):
    text = f"目录\n甲方案……1\n乙方案……{second_label}"
    doc = {"source": {"sha256": "a" * 64}, "pages": [page(1, text)]}
    with pytest.raises(ValueError, match="visible target"):
        apply_navigation(
            doc,
            task(doc, [1]),
            {
                "entries": [
                    {**proposal(1, "甲方案", "1"), "source_anchor": text.removeprefix("目录\n")}
                ]
            },
            {},
        )


def test_omitted_target_error_exposes_exact_source_position():
    text = "目录\n甲……1\n乙……2"
    doc = {"source": {"sha256": "a" * 64}, "pages": [page(1, text)]}
    with pytest.raises(ValueError, match="omits a visible target") as captured:
        apply_navigation(doc, task(doc, [1]), {"entries": [proposal(1, "甲", "1")]}, {})
    assert captured.value.details == {
        "page": 1,
        "start": text.index("……2"),
        "end": len(text),
        "label": "2",
        "anchor": "乙……2",
    }


def test_author_between_leader_and_page_number_cannot_hide_a_missing_entry():
    text = "目录\n甲方案……甲作者（431）\n乙方案……乙作者（434）\n· 1 ·"
    p = page(1, text)
    p["observation"] = observation(p, {"kind": "toc", "printed_label": "1"})
    doc = {"source": {"sha256": "a" * 64}, "pages": [p]}
    with pytest.raises(ValueError, match="omits a visible target") as captured:
        apply_navigation(
            doc,
            task(doc, [1]),
            {
                "entries": [
                    {**proposal(1, "甲方案", "431"), "source_anchor": "甲方案……甲作者（431）"}
                ]
            },
            {},
        )
    assert captured.value.details["label"] == "434"


def test_source_entry_must_own_the_complete_visible_target_range():
    text = "目录\n甲方案……12—15"
    doc = {"source": {"sha256": "a" * 64}, "pages": [page(1, text)]}
    with pytest.raises(ValueError, match="visible target"):
        apply_navigation(
            doc,
            task(doc, [1]),
            {"entries": [{**proposal(1, "甲方案", "12"), "source_anchor": "甲方案……12—15"}]},
            {},
        )
    apply_navigation(
        doc,
        task(doc, [1]),
        {"entries": [{**proposal(1, "甲方案", "12—15"), "source_anchor": "甲方案……12—15"}]},
        {},
    )


def test_explicit_unresolved_does_not_requeue_or_unlock_organization(tmp_path):
    from tests.unit.processing.test_store import complete, document

    store, identity = document(tmp_path)
    complete(store, identity, "ocr", "ocr", {"markdown": "目录\n甲……1"})
    complete(
        store,
        identity,
        "review",
        "review",
        {"verdict": "approved", "issues": [], "page_observation": {"kind": "toc"}},
    )
    parser = store.claim_task(identity, "navigation", "parser")
    store.original_page(identity, 1, task_id=parser["task_id"], lease_token=parser["lease_token"])
    store.submit_task(
        identity,
        parser["task_id"],
        parser["lease_token"],
        {
            "input_sha256": parser["input_sha256"],
            "entries": [],
            "page_coverage": [{"page": 1, "status": "unresolved", "reason": "目录页码原件不清"}],
        },
    )
    for _ in range(2):
        store.refresh_navigation(identity)
    doc = store.status(identity)
    assert doc["navigation"]["unresolved_pages"] == [1]
    assert doc["navigation"]["missing_pages"] == []
    assert doc["navigation"]["coverage_complete"] is False
    assert not any(
        t["state"] == "ready" and t["stage"] in {"navigation", "organization"} for t in doc["tasks"]
    )
    assert store.claim_task(identity, "organization", "organizer") is None
    with store.context.verified(identity) as current:
        store.navigation.refresh_document(current, retry_unresolved=True)
        store.checkpoints.write(current)
    ready = [
        t
        for t in store.status(identity)["tasks"]
        if t["stage"] == "navigation" and t["state"] == "ready"
    ]
    assert len(ready) == 1


def test_roman_directory_labels_cannot_be_discarded_as_an_empty_page():
    doc = {"source": {"sha256": "a" * 64}, "pages": [page(1, "目录\n序言……iv")]}
    with pytest.raises(ValueError, match="visible entries empty"):
        apply_navigation(
            doc,
            task(doc, [1]),
            {
                "entries": [],
                "page_coverage": [{"page": 1, "status": "empty", "reason": "空页"}],
            },
            {},
        )


@pytest.mark.parametrize("manual", [False, True])
def test_completed_cross_page_title_retires_only_a_machine_suffix_alias(manual):
    from tests.unit.processing.test_navigation_spans import cross_page_document, split_proposal

    doc = cross_page_document()
    old = validate_proposal(
        doc,
        [
            {
                "source_page": 2,
                "source_anchor": "工作的决定……12",
                "title": "工作的决定",
                "target_label": "12",
            }
        ],
    )[0]
    decision = {"source_sha256": old["source_sha256"], "target": None, "final": True}
    doc["navigation"] = {
        "source_entries": [old],
        "decisions": {old["id"]: decision} if manual else {},
    }
    before = deepcopy(doc["navigation"]["decisions"])
    apply_navigation(doc, task(doc, [1, 2]), {"entries": [split_proposal()]}, {})
    nav = doc["navigation"]
    assert nav["decisions"] == before
    if manual:
        assert len(nav["source_entries"]) == 2
        assert nav.get("source_entry_aliases", {}) == {}
    else:
        assert len(nav["source_entries"]) == 1
        assert nav["source_entry_aliases"][old["id"]] == nav["source_entries"][0]["id"]
        assert nav["source_entry_history"][0]["entry"] == old
