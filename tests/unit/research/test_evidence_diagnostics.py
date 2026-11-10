"""Evidence rejections locate exact frozen ranges without changing source gates."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from hrs.codex.research_tools import rejection_diagnostics
from hrs.processing.errors import EvidenceMismatch
from hrs.processing.research_workflow import ResearchWorkflow


def material():
    text = "甲页报告🙂[1]。\n[1]: 初次说明。\n乙页纪要[1]。\n[1]: 后次说明。\n"
    split = text.index("乙页")
    refs = [text.index("[1]"), text.index("[1]", split)]
    notes = [text.index("[1]:"), text.index("[1]:", split)]
    article = {
        "id": "original-article",
        "text": text,
        "footnotes": [
            {
                "id": f"page-{page}-note-1",
                "label": "1",
                "page": page,
                "note": {"start": note, "end": text.index("\n", note) + 1},
                "references": [{"start": ref, "end": ref + 3}],
            }
            for page, ref, note in zip((3, 9), refs, notes, strict=True)
        ],
    }

    def evidence(identity, page=0, note_id=None):
        start, end = (0, text.index("\n")) if page == 0 else (split, text.index("\n", split))
        return {
            "id": identity,
            "article_id": article["id"],
            "start": start,
            "end": end,
            "quote": text[start:end],
            "footnote_ids": [note_id or article["footnotes"][page]["id"]],
        }

    card = {
        "id": "source-card",
        "article_id": article["id"],
        "identity_links": [],
        "evidence": [
            evidence("valid-first"),
            evidence("wrong-page", note_id="page-9-note-1"),
            evidence("valid-second", 1),
        ],
    }
    return article, card, evidence


def validate(article, cards, version=6):
    # Isolate this source gate from the separately tested complete-card contract.
    workflow = SimpleNamespace(_required_sources=lambda *args: [article])
    ResearchWorkflow._validate_cards(
        workflow,
        cards,
        {"stage": "author", "article_id": article["id"]},
        {"policy": {"version": version, "card_only": version >= 6}},
    )


@pytest.mark.parametrize("version", [1, 2, 3, 4, 5, 6])
def test_same_label_on_another_page_reports_the_actual_owned_ranges_without_mutating(version):
    article, card, _ = material()
    retained = deepcopy((article, card))
    with pytest.raises(EvidenceMismatch, match="Footnote citation is not owned") as rejected:
        validate(article, [card], version)
    issue = rejected.value.validation_issues[0]
    assert issue["loc"] == ("cards", 0, "evidence", 1, "footnote_ids", 0)
    assert issue["type"] == "footnote_evidence_ownership"
    context = issue["ctx"]
    assert context["qualified_evidence_id"] == "source-card/wrong-page"
    assert context["article_id"] == article["id"]
    assert context["evidence_range"] == {"start": 0, "end": card["evidence"][1]["end"]}
    assert context["note_id"] == "page-9-note-1" and context["source_note_index"] == 1
    assert context["note_range"] == article["footnotes"][1]["note"]
    assert context["reference_ranges"] == [{"index": 0, **article["footnotes"][1]["references"][0]}]
    assert "cards[0].evidence[1].footnote_ids[0]" in issue["msg"]
    assert "source-card/wrong-page" in issue["msg"]
    assert "page-9-note-1" in issue["msg"]
    assert article["text"] not in issue["msg"]
    assert (article, card) == retained
    card["evidence"][1]["footnote_ids"] = ["page-3-note-1"]
    validate(article, [card], version)


def test_quote_and_missing_note_failures_are_located_together_through_native_reply():
    article, card, evidence = material()
    card["evidence"][1]["footnote_ids"] = ["page-3-note-1"]
    card["evidence"].extend(
        [
            {**evidence("wrong-quote"), "quote": "The model changed this text."},
            evidence("missing-note", 1, "unknown-note"),
        ]
    )
    with pytest.raises(EvidenceMismatch) as rejected:
        validate(article, [card])
    issues = rejected.value.validation_issues
    assert [item["loc"] for item in issues] == [
        ("cards", 0, "evidence", 3, "quote"),
        ("cards", 0, "evidence", 4, "footnote_ids", 0),
    ]
    assert issues[0]["msg"].startswith(
        "Evidence quote does not match the frozen original text range"
    )
    assert issues[1]["msg"].startswith("Footnote belongs to another article or does not exist")
    assert issues[0]["ctx"]["article_length"] == len(article["text"])
    assert issues[0]["ctx"]["quote_length"] == len(card["evidence"][3]["quote"])
    assert issues[1]["ctx"]["note_id"] == "unknown-note"
    audit, reply = rejection_diagnostics(rejected.value)
    assert audit["issue_count"] == reply["issue_count"] == 2
    assert reply["issues"][0]["path"] == ["cards", "*", "evidence", "*", "quote"]
    assert "cards[0].evidence[3].quote" in reply["issues"][0]["message"]
    assert "source-card/wrong-quote" in reply["issues"][0]["message"]
    assert all(article["text"] not in item["message"] for item in audit["issues"])


def test_a_valid_reference_or_note_body_overlap_is_accepted_but_boundary_touch_is_not():
    article, card, _ = material()
    note = article["footnotes"][1]
    evidence = card["evidence"][1]
    evidence.update(start=note["note"]["start"], end=note["note"]["end"])
    evidence["quote"] = article["text"][evidence["start"] : evidence["end"]]
    validate(article, [card])
    evidence.update(start=note["references"][0]["end"], end=note["note"]["start"])
    evidence["quote"] = article["text"][evidence["start"] : evidence["end"]]
    with pytest.raises(EvidenceMismatch, match="Footnote citation is not owned"):
        validate(article, [card])


def test_quote_difference_reports_original_unicode_offset_after_a_non_bmp_character():
    article, card, _ = material()
    card["evidence"][1]["footnote_ids"] = ["page-3-note-1"]
    evidence = card["evidence"][0]
    offset = article["text"].index("🙂") + 1
    evidence["quote"] = evidence["quote"][:offset] + "X" + evidence["quote"][offset + 1 :]
    with pytest.raises(EvidenceMismatch) as rejected:
        validate(article, [card])
    assert rejected.value.validation_issues[0]["ctx"]["first_difference_offset"] == offset


def test_out_of_bounds_evidence_retains_exact_path_and_source_length():
    article, card, _ = material()
    card["evidence"][1]["footnote_ids"] = ["page-3-note-1"]
    card["evidence"][2]["end"] = len(article["text"]) + 1
    with pytest.raises(EvidenceMismatch) as rejected:
        validate(article, [card])
    issue = rejected.value.validation_issues[0]
    assert issue["loc"] == ("cards", 0, "evidence", 2, "quote")
    assert issue["ctx"]["evidence_range"]["end"] == len(article["text"]) + 1
    assert issue["ctx"]["article_length"] == len(article["text"])


def test_many_bad_references_keep_bounded_diagnostics_with_explicit_omissions():
    article, card, _ = material()
    card["evidence"][1]["footnote_ids"] = ["page-9-note-1"] * 4096
    with pytest.raises(EvidenceMismatch) as rejected:
        validate(article, [card])
    issues = rejected.value.validation_issues
    assert len(issues) == 16
    assert issues[0]["ctx"]["error_count"] == 4096
    assert issues[0]["ctx"]["omitted_errors"] == 4080
    _, reply = rejection_diagnostics(rejected.value)
    assert "4096 invalid evidence references, 4080 omitted" in reply["issues"][0]["message"]
    assert len(str(issues)) < 32000


def test_bad_reference_after_a_large_valid_prefix_is_still_rejected_at_its_actual_index():
    article, card, _ = material()
    card["evidence"][1]["footnote_ids"] = ["page-3-note-1"] * 4096 + ["page-9-note-1"]
    with pytest.raises(EvidenceMismatch) as rejected:
        validate(article, [card])
    assert rejected.value.validation_issues[0]["loc"][-1] == 4096


def test_long_identifiers_do_not_hide_the_failed_note_position_in_native_reply():
    article, card, _ = material()
    card["id"] = "card-" + "c" * 1000
    card["evidence"][1]["id"] = "evidence-" + "e" * 1000
    with pytest.raises(EvidenceMismatch) as rejected:
        validate(article, [card])
    audit, reply = rejection_diagnostics(rejected.value)
    message = reply["issues"][0]["message"]
    assert len(message) <= 400
    assert "cards[0].evidence[1].footnote_ids[0]" in message
    assert "page-9-note-1" in message and "source note[1]" in message
    assert "note range=" in message and "reference ranges=" in message
    assert article["text"] not in str(audit)


def test_another_card_reports_its_actual_card_index():
    article, first, _ = material()
    first["evidence"][1]["footnote_ids"] = ["page-3-note-1"]
    second = deepcopy(first)
    second["id"] = "another-card"
    second["evidence"][2]["quote"] = "Changed."
    with pytest.raises(EvidenceMismatch) as rejected:
        validate(article, [first, second], 4)
    issue = rejected.value.validation_issues[0]
    assert issue["loc"] == ("cards", 1, "evidence", 2, "quote")
    assert issue["ctx"]["qualified_evidence_id"] == "another-card/valid-second"


def test_owned_reference_after_display_limit_is_still_checked_and_accepted():
    article, card, _ = material()
    note = article["footnotes"][1]
    note["references"] *= 12
    with pytest.raises(EvidenceMismatch) as rejected:
        validate(article, [card])
    context = rejected.value.validation_issues[0]["ctx"]
    assert len(context["reference_ranges"]) == 8 and context["omitted_reference_ranges"] == 4
    note["references"].append(article["footnotes"][0]["references"][0])
    validate(article, [card])
