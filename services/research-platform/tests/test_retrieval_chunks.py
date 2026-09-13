"""Synthetic structural regressions; these are not historical-source accuracy scores."""

from uuid import uuid4

import pytest

from hrs_platform.retrieval_chunks import blocks, expand_hits, retrieval_chunks


def chapter(text):
    split = len(text) // 2
    return {
        "id": str(uuid4()),
        "book_id": str(uuid4()),
        "run_id": str(uuid4()),
        "title": "运输记录",
        "text": text,
        "parts": [
            {"span_id": "page1", "start": 13, "text": text[:split], "source": {"pages": [8]}},
            {"span_id": "page2", "start": 20, "text": text[split:], "source": {"pages": [9]}},
        ],
    }


@pytest.mark.parametrize(
    "text",
    [
        "# 一章\n\n这是第一段。\n\n这是第二段，不包括其他月份。\n",
        "## 同名\r\n第一行。\r\n\r\n## 同名\r\n第二行。\r\n",
        "首节\n====\n\n跨页的完整内容。\n",
        "# 运输\n\n" + "甲地向乙地运送粮食。" * 200,
        "| 年份 | 数量 |\n| --- | --- |\n| 1940 | 120吨 |\n",
        "<table><tr><th>年份</th><th>数量</th></tr><tr><td>1940</td><td>120吨</td></tr></table>\n",
        "正文[^a]。\n\n[^a]: 此数只计本月。\n\n后续段落。",
        "- 条件一。\n- 条件二，不包含其他地区。\n",
        "```text\n# 这不是章节标题\n120\n```\n\n正文。",
        "甲\u200b乙。𠮷地登记120吨，不是130吨。\n",
        "\n\n   \n",
        "正文参见[来源]。\n\n[来源]: https://example.invalid/\n",
    ],
)
def test_structure_preserves_all_original_characters_and_source_offsets(text):
    source = chapter(text)
    chunks = list(retrieval_chunks(source, "合成样本", size=120, overlap=16))
    covered = set()
    for hit in chunks:
        assert hit["text"] == text[hit["start"] : hit["end"]]
        assert hit["retrieval_text"].startswith("合成样本\n")
        covered.update(range(hit["start"], hit["end"]))
        for item in [hit, *hit["context"]]:
            assert item["text"] == text[item["start"] : item["end"]]
            assert sum(s["unit_end"] - s["unit_start"] for s in item["sources"]) == len(item["text"])
    assert covered == set(range(len(text)))


def test_sections_do_not_merge_and_titles_are_in_retrieval_projection_only():
    source = chapter("# 第一节\n\n该部队于次年撤离。\n\n# 第二节\n\n驻留部队继续行动。")
    hits = list(retrieval_chunks(source, "合成史料"))
    assert len(hits) == 2
    assert hits[0]["section_path"] == ["运输记录", "第一节"]
    assert "合成史料" not in hits[0]["text"]
    assert "第二节" not in hits[0]["retrieval_text"]


def test_long_markdown_table_rows_keep_header_units_and_note_as_separate_evidence():
    header = "| 年份 | 数量（吨） |\n| --- | --- |\n"
    rows = "".join(f"| {1940 + i} | {120 + i} |\n" for i in range(30))
    source = chapter("表1 运输量\n\n" + header + rows + "\n注：仅计甲地。\n")
    tables = [h for h in retrieval_chunks(source, "样本", size=130, overlap=16) if h["kind"] == "table"]
    assert len(tables) > 1
    for hit in tables[1:]:
        assert header in hit["retrieval_text"]
        assert "注：仅计甲地" in hit["retrieval_text"]
        assert any(c["role"] == "table_header" for c in hit["context"])
        assert all(line.count("|") == 3 for line in hit["text"].splitlines() if line.strip())


def test_html_rowspan_group_is_not_split_and_header_is_repeated_separately():
    source = chapter(
        "<table><tr><th>地区</th><th>数量</th></tr>"
        '<tr><td rowspan="2">甲地</td><td>120吨</td></tr>'
        "<tr><td>130吨</td></tr>"
        "<tr><td>乙地</td><td>140吨</td></tr></table>"
    )
    hits = list(retrieval_chunks(source, "样本", size=70, overlap=10))
    owned = [h for h in hits if 'rowspan="2"' in h["text"]]
    assert len(owned) == 1 and "130吨" in owned[0]["text"]
    assert len(hits) > 1 and "<th>地区</th>" in hits[-1]["retrieval_text"]
    assert owned[0]["oversized"]


def test_linked_footnote_keeps_negation_and_its_own_original_pages():
    source = chapter("# 第一节\n登记120吨[^a]。\n\n# 注释\n[^a]: 不包括乙地，不是全省合计。\n")
    hit = next(h for h in retrieval_chunks(source, "样本") if "登记120吨" in h["text"])
    note = next(c for c in hit["context"] if c["role"] == "footnote")
    assert "不包括乙地" in note["text"]
    assert note["pages"] == [9]


def test_overlap_merges_evidence_and_context_stays_within_budget_and_section():
    source = chapter("# 第一节\n\n" + "完整运输过程，不包括乙地。" * 50 + "\n\n# 第二节\n无关内容。")
    hits = [dict(h, score=1.0) for h in retrieval_chunks(source, "样本", size=120, overlap=20)]
    output = expand_hits(hits[:3], lambda _: source, context_chars=500, total_chars=700)
    assert len(output) < 3
    assert sum(len(h["text"]) + sum(len(c["text"]) for c in h["context"]) for h in output) <= 700
    assert all("无关内容" not in c["text"] for h in output for c in h["context"])
    assert all(h["text"] == source["text"][h["start"] : h["end"]] for h in output)


def test_markdown_parser_does_not_treat_fenced_headings_as_sections():
    assert not any("伪标题" in " ".join(b["path"]) for b in blocks("```\n# 伪标题\n```\n\n正文"))


def test_identically_named_sections_stay_separate_even_during_context_expansion():
    source = chapter("# 同名\n\n甲地运输。\n\n# 同名\n\n乙地驻留。")
    hits = list(retrieval_chunks(source, "样本"))
    assert len(hits) == 2
    result = expand_hits([{**hits[1], "score": 1}], lambda _: source)
    assert "甲地" not in result[0]["text"]
    assert all("甲地" not in c["text"] for c in result[0]["context"])


def test_long_list_splits_between_complete_items_and_note_hit_retains_owner():
    source = chapter("".join(f"- {i}号运输队不包括乙地。\n" for i in range(40)))
    hits = list(retrieval_chunks(source, "样本", size=120, overlap=16))
    assert len(hits) > 1
    assert all(h["text"].startswith("- ") and h["text"].endswith("。\n") for h in hits)
    source = chapter("运入120吨[^a]。\n\n[^a]: 本月甲地合计。")
    note = next(h for h in retrieval_chunks(source, "样本") if h["kind"] == "note")
    assert any(c["role"] == "note_owner" and "120吨" in c["text"] for c in note["context"])


def test_supplement_budget_cannot_displace_other_primary_hits():
    first = chapter("# 甲\n" + "甲地运输。" * 35)
    second = chapter("# 乙\n" + "乙地运输。" * 35)
    hits = [dict(next(retrieval_chunks(c, "样本", size=50, overlap=0)), score=1) for c in (first, second)]
    chapters = {c["id"]: c for c in (first, second)}
    budget = sum(len(h["text"]) for h in hits)
    result = expand_hits(hits, chapters.__getitem__, context_chars=6000, total_chars=budget)
    assert len(result) == 2
    assert all(not h["context"] and h["context_truncated"] for h in result)
