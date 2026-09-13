from types import SimpleNamespace

from document_extraction.semantic_completion import complete_document
from document_extraction.artifacts import write_outputs
from test_review_routing import clean_page


def test_conversion_archives_without_claiming_review_or_calling_model(tmp_path):
    image = tmp_path / 'original.png'
    image.write_bytes(b'engineering fixture')
    page = clean_page(image, 1)
    page['ocr_evidence']['confidence'] = None
    def forbidden(*args):
        raise AssertionError('Conversion must not call a reviewer')
    result = complete_document([page], SimpleNamespace(review_mode='conversion_only'), tmp_path, reviewer=forbidden)
    assert result['model_calls'] == 0
    assert not result['all_pages_verified']
    assert result['pages'][0]['review_route'] == 'deferred-article'
    manifest = write_outputs(image, tmp_path, result, ['paddleocr-vl'], {})
    assert manifest['archive_status'] == 'ready'
    assert manifest['review_status'] == 'not-reviewed'
    assert manifest['document_status'] != 'release-accepted'
    assert manifest['content_readiness_summary']['usable-with-warning'] == 1
    assert manifest['pending_human_review'] == 0


def test_block_hints_only_claim_offsets_for_unique_retained_text():
    from document_extraction.artifacts import block_text_hints
    page={'text':'正文①\n①实质注释\n重复\n重复','ocr_evidence':{'blocks':[
        {'label':'text','text':'正文①','bbox':[1,2,3,4],'order':0},
        {'label':'footnote','text':'①实质注释','bbox':[1,5,3,6],'order':1},
        {'label':'text','text':'重复','bbox':[1,7,3,8],'order':2}]}}
    hints=block_text_hints(page)
    assert hints[1]['start']==4
    assert hints[2]['start'] is None
    assert all(h['image_localization']=='page' for h in hints)
