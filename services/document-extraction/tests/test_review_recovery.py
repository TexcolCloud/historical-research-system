import copy

from document_extraction.semantic_completion import apply_changes, complete_document
from test_single_ocr_completion import verdict


def patch(before, after):
    return dict(before=before, after=after, source_reading=after, location='正文',
                kind='claim', explanation='原图中的数量不同')


def run(tmp_path, text, replies, **page_fields):
    tmp_path.mkdir(parents=True, exist_ok=True)
    image = tmp_path / 'page.png'
    image.write_bytes(b'original-fixture')
    calls = []

    def reviewer(packet, images, settings):
        calls.append(packet)
        return copy.deepcopy(replies[min(len(calls)-1, len(replies)-1)])

    result = complete_document([dict(page=1, text=text, image_path=image, **page_fields)],
                               None, tmp_path, reviewer=reviewer)
    return result['pages'][0], calls


def test_typography_match_keeps_actual_source_offsets():
    text = '甲——乙，人数 １２ 人。'
    result, edits, rejected = apply_changes(text, [patch('甲--乙，人数12人。', '甲——乙，人数13人。')])
    assert not rejected
    assert result == '甲——乙，人数13人。'
    assert edits[0]['before'] == text
    assert edits[0]['end_before'] == len(text)


def test_fact_mismatch_and_ambiguous_quotes_stay_rejected():
    for text, before in [('甲部12人。', '甲部13人。'), ('甲12人。甲12人。', '甲12人。')]:
        result, edits, rejected = apply_changes(text, [patch(before, '甲14人。')])
        assert result == text and not edits and rejected


def test_bad_sibling_patch_does_not_prevent_clean_recheck(tmp_path):
    page, calls = run(tmp_path, '甲一人。乙三人。', [verdict(changes=[
        patch('甲一人', '甲二人'), patch('不存在的原文', '乙四人')]), verdict()])
    assert page['verified'] and page['text'] == '甲二人。乙三人。'
    assert calls[-1]['phase'] == 'verify-correction'


def test_unlocated_patch_gets_one_source_bound_repair_request(tmp_path):
    page, calls = run(tmp_path, '甲一人。', [verdict(changes=[patch('甲三人', '甲二人')]),
                       verdict(changes=[patch('甲一人', '甲二人')]), verdict()])
    assert page['verified'] and page['text'] == '甲二人。'
    assert calls[1].get('repair_feedback')


def test_incomplete_review_retries_without_discarding_receipts(tmp_path):
    page, calls = run(tmp_path, '甲一人。', [{'review_state':'unavailable', 'reason':'timeout'}, verdict()])
    assert page['verified'] and len(page['receipts']) == len(calls) == 2


def test_second_check_can_finish_one_more_correction(tmp_path):
    page, calls = run(tmp_path, '甲一人。乙三人。', [verdict(changes=[patch('甲一人', '甲二人')]),
                 verdict(changes=[patch('乙三人', '乙四人')]), verdict()])
    assert page['verified'] and page['text'] == '甲二人。乙四人。'
    rebuilt = '甲一人。乙三人。'
    for edit in reversed(page['changes']):
        a,b = edit['start_before'], edit['end_before']
        assert rebuilt[a:b] == edit['before']
        rebuilt = rebuilt[:a] + edit['after'] + rebuilt[b:]
    assert rebuilt == page['text'] and len(calls) == 3


def test_contradictory_no_error_claim_requires_fresh_verdict(tmp_path):
    concern = {'kind':'claim', 'excerpt':'甲一人。', 'explanation':'原图与底稿内容完全一致，无误。'}
    page, calls = run(tmp_path, '甲一人。', [verdict(concerns=[concern]), verdict()])
    assert page['verified'] and len(calls) == 2
    page, calls = run(tmp_path/'still-pending', '甲一人。', [verdict(concerns=[concern])])
    assert not page['verified'] and len(calls) <= 3


def test_repeated_failure_is_bounded_and_retains_original(tmp_path):
    page, calls = run(tmp_path, '甲一人。', [{'review_state':'unavailable','reason':'timeout'}])
    assert not page['verified'] and page['text'] == '甲一人。' and len(calls) <= 3


def test_multiline_table_cells_keep_numbers_and_exclude_external_folio():
    from document_extraction.visual_source import table_number_conflict
    draft = '<table><tr><td>1939年1月10日</td><td>约5个师</td></tr></table>'
    reading = {'tables':['| 期间 | 说明 |\n|---|---|\n|1939年1月10日|情况\n约5个师 |\n124'], 'numeric_lines':[], 'unclear':[]}
    assert table_number_conflict(draft, reading) == ''


def test_targeted_review_never_rechecks_accepted_neighbor(tmp_path):
    from types import SimpleNamespace
    image = tmp_path / 'p.png'
    image.write_bytes(b'original')
    pages = [dict(page=1, text='甲一人。', image_path=image),
             dict(page=2, text='乙段无误。', image_path=image, review_route='rule-pass', verified=True,
                  changes=[], concerns=[], receipts=[])]
    calls=[]
    def reviewer(packet, *_):
        calls.append(packet)
        return verdict(changes=[patch('甲一人','甲二人')]) if len(calls)==1 else verdict()
    result=complete_document(pages, SimpleNamespace(review_mode='full'),tmp_path,reviewer=reviewer,target_pages={1})
    assert all(c['target']['page']==1 for c in calls)
    assert result['pages'][1]['receipts']==[] and result['pages'][1]['verified']
