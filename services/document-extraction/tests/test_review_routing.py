from pathlib import Path
from types import SimpleNamespace

from document_extraction.semantic_completion import complete_document


def clean_page(image, number):
    text = "这是一段用于接口测试的完整正文，来源范围明确，保持原有文字。"
    return {"page":number, "image_path":image, "text":text,
            "ocr_evidence":{"text":text, "confidence":0.995,
                "metadata":{"confidence_kind":"recognition", "confidence_scope":"page"},
                "blocks":[{"label":"text", "text":text, "bbox":[10, 10, 500, 300]}]}}


def test_high_confidence_plain_interior_page_skips_model_without_faking_review(tmp_path):
    image = tmp_path/'original.png'
    image.write_bytes(b'engineering-original')
    calls = []
    def reviewer(packet, images, settings):
        calls.append(packet['target']['page'])
        from test_single_ocr_completion import verdict
        return verdict()
    pages = [clean_page(image, n) for n in (1, 2, 3)]
    result = complete_document(pages, SimpleNamespace(review_mode='risk_based', confidence_threshold=0.98), tmp_path, reviewer=reviewer)
    assert set(calls) == {1, 3}  # First/last pages retain boundary checking.
    middle = result['pages'][1]
    assert middle['review_route'] == 'rule-pass'
    assert middle['receipts'] == []
    assert middle['verified'] is False
    assert result['routing_summary']['rule_passed_pages'] == 1
    from document_extraction.artifacts import write_outputs
    manifest = write_outputs(image, tmp_path, result, ['engineering-fixture'], {})
    assert manifest['pending_human_review'] == 0
    assert manifest['review_routing']['rule_passed_pages'] == 1


def test_explicit_revision_only_reviews_selected_page_and_preserves_other_receipts(tmp_path):
    from test_single_ocr_completion import verdict
    image = tmp_path/'original.png'
    image.write_bytes(b'engineering-original')
    pages = [clean_page(image, n) for n in (1,2,3)]
    pages[0].update(receipts=[{'verdict':verdict(), 'original_receipt':True}], verified=True,
                    concerns=[], changes=[], structure=verdict()['structure'])
    pages[2].update(receipts=[{'verdict':verdict(), 'original_receipt':True}], verified=True,
                    concerns=[], changes=[], structure=verdict()['structure'])
    calls = []
    def reviewer(packet, images, settings):
        calls.append(packet['target']['page'])
        return verdict()
    result = complete_document(pages, None, tmp_path, reviewer=reviewer, target_pages={2})
    assert calls == [2]
    assert result['pages'][0]['receipts'] == pages[0]['receipts']
    assert result['pages'][2]['receipts'] == pages[2]['receipts']


def test_unknown_low_and_structurally_risky_pages_never_pass_by_score(tmp_path):
    import copy
    from document_extraction.review_routing import route_page, rule_passed
    image = tmp_path/'original.png'
    image.write_bytes(b'engineering-original')
    base = clean_page(image, 2)
    for score in (None, True, float('nan'), 0.4):
        page = copy.deepcopy(base)
        page['ocr_evidence']['confidence'] = score
        assert route_page(page, 1, 3)['route'] == 'deepseek'
    for text in ('# 标题\n'+base['text'], '<table><tr><td>数字</td></tr></table>', '乱码�'+base['text']):
        page = copy.deepcopy(base)
        page['text'] = page['ocr_evidence']['text'] = text
        assert route_page(page, 1, 3)['route'] == 'deepseek'
    page = copy.deepcopy(base)
    page['ocr_evidence']['blocks'] = []
    assert route_page(page, 1, 3)['route'] == 'deepseek'
    page = copy.deepcopy(base)
    page['ocr_evidence']['metadata']['confidence_kind'] = 'layout'
    assert route_page(page, 1, 3)['route'] == 'deepseek'
    decision = route_page(base, 1, 3)
    base.update(review_route='rule-pass', routing_decision=decision, receipts=[], concerns=[])
    assert rule_passed(base)
    base['text'] += '改文'
    assert not rule_passed(base)
    assert route_page(clean_page(image, 2), 1, 3, mode='full')['route'] == 'deepseek'


def test_changed_neighbor_revokes_rule_pass_and_failed_recheck_stays_held(tmp_path):
    from test_single_ocr_completion import verdict
    from document_extraction.review_routing import rule_passed
    image = tmp_path/'original.png'
    image.write_bytes(b'engineering-original')
    pages = [clean_page(image, n) for n in (1,2,3)]
    def reviewer(packet, images, settings):
        n = packet['target']['page']
        if n == 1 and packet['phase'] == 'initial':
            # A substantive repair; normalization proposals are deliberately not applied.
            return verdict(changes=[{'before':'保持原有文字', 'after':'不要保持原有文字',
                'source_reading':'不要保持原有文字', 'location':'正文', 'kind':'claim', 'explanation':'工程夹具：否定词遗漏'}])
        if n == 2:
            return {'review_state':'unavailable', 'reason':'engineering-unavailable'}
        return verdict()
    result = complete_document(pages, None, tmp_path, reviewer=reviewer)
    middle = result['pages'][1]
    assert middle['review_route'] == 'deepseek'
    assert not rule_passed(middle)
    assert not middle['verified']
    assert middle['concerns'][0]['kind'] == 'unreviewed'
    assert result['routing_summary']['rule_passed_pages'] == 0
