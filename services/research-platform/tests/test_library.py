from uuid import uuid4

import pytest

from hrs_platform.domain.book_structure import validate_outline_boundaries
from hrs_platform.library import reviewed_spans


def test_corrected_span_keeps_both_original_pages_and_continuation_whitespace():
    source = "第一章\n原文甲\n\n原文乙\n第二章"
    pages = [{"page": 1, "start": 0, "end": 8}, {"page": 2, "start": 9, "end": len(source)}]
    spans = reviewed_spans(source, pages, [], str(uuid4()))
    assert "".join(row["selected_text"] for row in spans) == source
    corrected = reviewed_spans(
        source,
        pages,
        [{"start": 6, "end": 12, "text": "校正后的文字", "decision_id": "technical-test"}],
        str(uuid4()),
    )
    assert "".join(row["selected_text"] for row in corrected) == source[:6] + "校正后的文字" + source[12:]
    edited = next(row for row in corrected if row["source_record"]["human_decision_id"])
    assert edited["source_record"]["pages"] == [1, 2]


def test_existing_chapter_rule_rejects_a_subsection_as_new_logical_page():
    outline = {
        "mode": "chaptered",
        "items": [{"title": "第一章", "role": "chapter"}, {"title": "第二章", "role": "chapter"}],
    }
    previous = [{"kind": "article", "title": "第一章"}]
    with pytest.raises(ValueError):
        validate_outline_boundaries(outline, previous, [{"kind": "article", "title": "一、行政组织"}])
    validate_outline_boundaries(outline, previous, [])
