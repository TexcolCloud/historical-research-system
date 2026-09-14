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


def test_consecutive_titles_travel_with_first_body_without_merging_full_sections():
    source = chapter("# 村长任务\n\n## 训练意义\n\n说明训练目的。\n\n## 任职条件\n\n说明任职条件。")
    hits = list(retrieval_chunks(source, "合成书"))
    assert len(hits) == 2
    assert hits[0]['text'].startswith('# 村长任务') and '说明训练目的' in hits[0]['text']
    assert '任职条件' not in hits[0]['text']
    assert hits[0]['section_path'] == ['运输记录', '村长任务', '训练意义']


def test_heading_chain_expands_into_its_list_but_not_the_next_section():
    source = chapter('# 总题\n\n## 条件\n\n须具备：\n\n1. 责任感。\n2. 知识。\n\n## 其他\n\n无关正文。')
    hit = next(retrieval_chunks(source, '书'))
    expanded = expand_hits([{**hit, 'score': 1}], lambda _: source)[0]
    assert any('责任感' in c['text'] for c in expanded['context'])
    assert all('无关正文' not in c['text'] for c in expanded['context'])


def test_same_level_heading_chain_keeps_following_paragraphs_in_embedding():
    source = chapter('#### 总题\n\n#### 条件\n\n须具备：\n\n责任感和知识。\n\n#### 其他\n\n无关正文。')
    hit = next(retrieval_chunks(source, '书'))
    assert '责任感和知识' in hit['retrieval_text']
    assert '无关正文' not in hit['retrieval_text']


def test_linked_note_and_separate_signature_are_one_source_chunk():
    text = "正文①。\n\n① 注释说明。\n\n——译者\n\n后续正文。"
    source = chapter(text)
    source['parts'] = [{'span_id': 'p1', 'start': 0, 'text': text, 'source': {'pages': [1]}}]
    hits = list(retrieval_chunks(source, "合成书"))
    notes = [h for h in hits if h['kind'] == 'note']
    assert len(notes) == 1 and '——译者' in notes[0]['text']
    assert '后续正文' not in notes[0]['text']
    assert any(c['role'] == 'note_owner' and '正文①' in c['text'] for c in notes[0]['context'])


def test_image_destinations_are_not_embedding_text_but_source_stays_intact():
    text = '图示如下。\n\n![部署图](<C:/cache/a folder/sha123.png>)\n\n![Image](pages/noise.png)\n\n后续文字。'
    source = chapter(text)
    hits = list(retrieval_chunks(source, '合成书'))
    projection = '\n'.join(h['retrieval_text'] for h in hits)
    assert '部署图' in projection and '后续文字' in projection
    assert 'sha123' not in projection and 'noise.png' not in projection
    assert ''.join(h['text'] for h in hits) == text


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


def test_temporal_introduction_survives_numbered_siblings_but_not_next_section():
    source = chapter('进入1942年，部队调整部署。\n\n其情况如下。\n\n## 一、甲地\n1月进驻。\n\n## 二、乙地\n2月撤出。\n\n## 其他事项\n另议。')
    hits = list(retrieval_chunks(source, '样本'))
    for title in ['一、甲地', '二、乙地', '其他事项']:
        hit = next(h for h in hits if h['text'].startswith('## '+title))
        result = expand_hits([{**hit,'score':1}], lambda _:source)[0]
        assert any('进入1942年' in c['text'] for c in result['context']) == (title != '其他事项')
        assert all(c['text']==source['text'][c['start']:c['end']] for c in result['context'])


def test_continued_table_keeps_event_intro_and_shared_cell_scope():
    intro = '将各部队强行改编如下，从而改变组织。\n\n'
    first = '<table><tr><td>队伍</td><td>数量</td></tr><tr><td>甲</td><td>1</td></tr></table>\n\n'
    second = '<table><tr><td>乙</td><td rowspan="2">共45</td></tr><tr><td>丙</td></tr></table>'
    source = chapter(intro+first+second)
    source['structure']=[{'kind':'table','status':'ready','members':[
        {'start':len(intro),'end':len(intro+first)}, {'start':len(intro+first),'end':len(source['text'])}]}]
    hit = next(h for h in retrieval_chunks(source,'样本') if 'rowspan' in h['text'])
    result = expand_hits([{**hit,'score':1}],lambda _:source)[0]
    assert any(c['role']=='table_intro' and '强行改编' in c['text'] for c in result['context'])
    scope = next(c for c in result['table_scopes'] if c['value']=='共45')
    assert scope['row_numbers']==[1,2] and scope['column_numbers']==[2]
    assert scope['row_labels']==[['乙'],['丙']]


def test_complete_paragraph_does_not_pull_optional_background():
    source = chapter('# 第一节\n\n直接答案。\n\n无关背景。')
    start=source['text'].index('直接答案')
    from hrs_platform.retrieval_chunks import source_excerpt
    hit={**source_excerpt(source,start,start+len('直接答案。\n\n')),'score':1,'context':[]}
    result=expand_hits([hit],lambda _:source)
    assert all('无关背景' not in c['text'] for c in result[0]['context'])


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
