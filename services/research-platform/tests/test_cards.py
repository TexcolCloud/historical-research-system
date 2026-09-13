import pytest

from hrs_platform.cards import ResearchPlan, neighbor_context, quote_pages, validate_plan


def test_agent_scope_does_not_hide_the_previous_chapter_or_book_opening():
    units = [{"unit_id": str(i)} for i in range(8)]
    assert neighbor_context(units, units[4:6]) == [units[0], units[3], units[6]]


def test_visual_evidence_uses_the_quote_range_not_every_page_in_its_source_chunk():
    unit = {
        "text": "第一段。第二段。第三段。",
        "sources": [
            {"unit_start": 0, "unit_end": 4, "pages": [3]},
            {"unit_start": 4, "unit_end": 8, "pages": [4]},
            {"unit_start": 8, "unit_end": 12, "pages": [5]},
        ],
    }
    assert quote_pages(unit, {"quote": "第二段。", "occurrence": 0}) == [4]
    assert quote_pages(unit, {"quote": "段。第二", "occurrence": 0}) == [3, 4]
    with pytest.raises(ValueError):
        quote_pages(unit, {"quote": "不存在", "occurrence": 0})


def test_dynamic_assignments_require_complete_nonduplicated_chapter_coverage():
    plan = ResearchPlan(
        rationale="合并相关章节",
        assignments=[{"name": "阅读", "objective": "全篇", "chapter_ids": ["a", "b"]}],
    )
    validate_plan(plan, ["a", "b"])
    with pytest.raises(ValueError):
        validate_plan(plan, ["a", "b", "c"])
