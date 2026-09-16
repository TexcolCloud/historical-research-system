from hrs_platform.services.structure_views import describe_structure
from hrs_platform.services.structure_views import reading_view


def source(pages):
    text, parts = '', []
    for number, value in enumerate(pages, 1):
        value += '\n\n'
        parts.append({'span_id': str(number), 'start': len(text), 'end': len(text) + len(value),
                      'text': value, 'source': {'pages': [number]}})
        text += value
    return {'text': text, 'parts': parts}


def table(value):
    return '<table><tr><th>地区</th><th>数量（吨）</th></tr><tr><td>' + value + '</td><td>120</td></tr></table>'


def test_headerless_continuation_with_closed_local_rowspans_keeps_column_ownership():
    from hrs_platform.services.structure_views import merge_table_parts

    first = '<table><tr><th>地区</th><th>数量</th></tr><tr><td rowspan="2">甲县</td><td>120</td></tr><tr><td>130</td></tr></table>'
    second = '<table><tr><td>乙县</td><td>140</td></tr></table>'
    merged = first.replace('</table>', second.removeprefix('<table>'))
    assert merge_table_parts([first, second], merged) == merged
    assert merge_table_parts([first.replace('rowspan="2"', 'rowspan="3"'), second], merged) is None
    assert merge_table_parts([first, second.replace('<td>140</td>', '')], merged) is None
    assert merge_table_parts([first, second], merged.replace('140', '14')) is None
    doc = source([first, second])
    metadata = {'tables': [{'pages': [1, 2], 'merged_html': merged,
        'members': [{'page': 1, 'original_html': first}, {'page': 2, 'original_html': second}]}]}
    assert describe_structure(doc, metadata, [], {1, 2})['items'][0]['status'] == 'ready'
    assert describe_structure(doc, metadata, [], {1})['items'][0]['status'] == 'retained'


def test_a_retrieved_table_note_also_carries_its_source_checked_header():
    from uuid import uuid4
    from hrs_platform.services.retrieval_chunks import retrieval_chunks

    first = table('甲县')
    second = '<table><tr><td>乙县①</td><td>140</td></tr></table>'
    merged = first.replace('</table>', second.removeprefix('<table>'))
    chapter = {**source([first, second + '\n\n① 仅计本月。']),
        **{k: str(uuid4()) for k in ('id', 'book_id', 'run_id')}, 'title': '统计'}
    metadata = {'tables': [{'pages': [1, 2], 'merged_html': merged,
        'members': [{'page': 1, 'original_html': first}, {'page': 2, 'original_html': second}]}]}
    chapter['structure'] = describe_structure(chapter, metadata, [], {1, 2})['items']
    hit = next(h for h in retrieval_chunks(chapter, '书') if h['kind'] == 'note')
    assert any(c['role'] == 'note_owner' and '乙县' in c['text'] for c in hit['context'])
    assert any(c['role'] == 'table_header' and c['pages'] == [1] for c in hit['context'])


def test_source_checked_cross_page_table_is_one_reading_table_without_changing_evidence():
    first, second = table('甲县'), table('乙县')
    merged = first.replace('</table>', '<tr><td>乙县</td><td>120</td></tr></table>')
    chapter = source([first, second])
    metadata = {'tables': [{'group_id': 'table-1', 'pages': [1, 2], 'merged_html': merged,
                            'members': [{'page': 1, 'original_html': first}, {'page': 2, 'original_html': second}]}]}
    report = describe_structure(chapter, metadata, [], {1, 2})
    assert report['items'][0]['status'] == 'ready'
    chapter['structure'] = report['items']
    view = reading_view(chapter)
    assert view['text'].count('<table>') == 1
    assert '甲县' in view['text'] and '乙县' in view['text']
    assert view['text'].count('数量（吨）') == 1
    assert chapter['text'] == first + '\n\n' + second + '\n\n'
    assert report['items'][0]['pages'] == [1, 2]


def test_table_candidates_cannot_drop_numbers_or_bypass_content_review():
    from hrs_platform.services.structure_views import merge_table_parts

    first, second = table('甲县'), table('乙县')
    merged = first.replace('</table>', '<tr><td>乙县</td><td>120</td></tr></table>')
    assert merge_table_parts([first, second], merged.replace('120', '12')) is None
    chapter = source([first, second])
    metadata = {'tables': [{'group_id': 'table-1', 'pages': [1, 2], 'merged_html': merged,
                            'members': [{'page': 1, 'original_html': first}, {'page': 2, 'original_html': second}]}]}
    report = describe_structure(chapter, metadata, [], {1})
    assert report['items'][0]['status'] == 'retained'
    assert reading_view({**chapter, 'structure': report['items']})['text'] == chapter['text']
    # A correction outside the table keeps the table relation reusable.
    chapter = source(['无关正文已校正。\n\n' + first, second])
    assert describe_structure(chapter, metadata, [], {1, 2})['items'][0]['status'] == 'ready'
    chapter = source([first, second.replace('120', '121')])
    assert describe_structure(chapter, metadata, [], {1, 2})['items'][0]['status'] == 'retained'


def test_continuation_joins_only_located_body_and_preserves_footnote_links():
    from hrs_platform.services.structure_views import project_structure

    first, second = '本月运入粮食共计', '一百二十吨，不含军运。'
    chapter = source([first, second + '\n\n另见注释[^a]。\n\n[^a]: 按当月统计。'])
    completion = [{'page': 1}, {'page': 2, 'structure': {'continues_previous': True,
                  'continuation_before': first, 'continuation_after': second}}]
    report = describe_structure(chapter, {}, completion, {1, 2})
    assert report['items'][0]['status'] == 'ready'
    chapter['structure'] = project_structure(report, chapter['parts'])
    view = reading_view(chapter)
    assert first + second in view['text']
    assert len(view['footnotes']) == 1
    assert view['text'][view['footnotes'][0]['note']['start']:view['footnotes'][0]['note']['end']].startswith('[^a]')
    split = project_structure(report, chapter['parts'][:1])
    assert split[0]['status'] == 'retained' and split[0]['display_text'] is None


def test_cross_page_context_reaches_retrieval_with_exact_source_ranges():
    from uuid import uuid4

    from hrs_platform.services.retrieval_chunks import retrieval_chunks

    first, second = table('甲县'), table('乙县')
    chapter = {**source(['单位：吨\n\n' + first, second]),
               **{k: str(uuid4()) for k in ('id', 'book_id', 'run_id')}, 'title': '运输统计'}
    metadata = {'tables': [{'pages': [1, 2], 'merged_html': first.replace('</table>', '<tr><td>乙县</td><td>120</td></tr></table>'),
                            'members': [{'page': 1, 'original_html': first}, {'page': 2, 'original_html': second}]}]}
    chapter['structure'] = describe_structure(chapter, metadata, [], {1, 2})['items']
    hits = list(retrieval_chunks(chapter, '合成书'))
    hit = next(h for h in hits if '乙县' in h['text'])
    assert any(c['role'] == 'table_header' and c['pages'] == [1] for c in hit['context'])
    assert any(c['text'].strip() == '单位：吨' for c in hit['context'])
    for item in [hit, *hit['context']]:
        assert item['text'] == chapter['text'][item['start']:item['end']]


def test_three_page_paragraph_is_joined_as_one_chain():
    chapter = source(['本月记录载明', '甲县运入粮食', '一百二十吨。'])
    completion = [{'page': 1}, {'page': 2, 'structure': {'continues_previous': True,
        'continuation_before': '本月记录载明', 'continuation_after': '甲县运入粮食'}},
        {'page': 3, 'structure': {'continues_previous': True,
        'continuation_before': '甲县运入粮食', 'continuation_after': '一百二十吨。'}}]
    report = describe_structure(chapter, {}, completion, {1, 2, 3})
    assert reading_view({**chapter, 'structure': report['items']})['text'].strip() == '本月记录载明甲县运入粮食一百二十吨。'


def test_intervening_notes_and_ambiguous_or_complex_tables_keep_original_layout():
    from hrs_platform.services.structure_views import merge_table_parts

    first, second = table('甲县'), table('乙县')
    merged = first.replace('</table>', '<tr><td>乙县</td><td>120</td></tr></table>')
    metadata = {'tables': [{'pages': [1, 2], 'merged_html': merged,
        'members': [{'page': 1, 'original_html': first}, {'page': 2, 'original_html': second}]}]}
    chapter = source([first + '\n\n注：本页另有统计口径。', second])
    report = describe_structure(chapter, metadata, [], {1, 2})
    assert reading_view({**chapter, 'structure': report['items']})['text'] == chapter['text']
    ambiguous = source([first + '\n\n' + first, second])
    assert describe_structure(ambiguous, metadata, [], {1, 2})['items'][0]['status'] == 'retained'
    assert merge_table_parts([first.replace('<td>', '<td rowspan="2">', 1), second], merged) is None
    assert merge_table_parts([first.replace('120', '120<sup>①</sup>'), second], merged) is None
