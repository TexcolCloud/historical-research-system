import pytest
from test_card_finalization import draft

from hrs_platform.domain.card_rules import ResearchPlan
from hrs_platform.domain.card_rules import apply_card_repair
from hrs_platform.services.cards.reading import neighbor_context
from hrs_platform.domain.card_rules import quote_pages
from hrs_platform.domain.card_rules import validate_plan
from hrs_platform.domain.generation_contracts import CardRepair


def test_card_patch_preserves_untouched_items_and_rejects_dangling_evidence():
    original = draft()
    before = original.model_dump(mode="json")
    changed = original.items[0].model_copy(update={"attribution": "译者更正"})
    patch = CardRepair(items=[changed], remove_item_ids=[], metadata=None)
    repaired = apply_card_repair(original, patch)
    assert original.model_dump(mode="json") == before
    assert repaired.items[0].attribution == "译者更正"
    assert repaired.items[1:] == original.items[1:]
    assert repaired.model_dump(exclude={"items"}) == original.model_dump(exclude={"items"})
    with pytest.raises(ValueError, match="conflicting"):
        apply_card_repair(original, CardRepair(items=[changed, changed], remove_item_ids=[], metadata=None))
    with pytest.raises(ValueError, match="conflicting"):
        apply_card_repair(original, CardRepair(items=[], remove_item_ids=["unknown"], metadata=None))
    # A correction cannot leave metadata pointing at deleted evidence.
    original.formation_date.basis = [original.items[0].item_id]
    with pytest.raises(ValueError, match="evidence_refs"):
        apply_card_repair(original, CardRepair(items=[], remove_item_ids=[original.items[0].item_id], metadata=None))


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
