import copy
import json
from hashlib import sha256

from document_extraction.artifacts import write_outputs


def test_pipeline_cleans_folios_before_visual_review_but_keeps_table_values(tmp_path):
    from types import SimpleNamespace
    from document_extraction.pipeline import run_document
    from test_review_scope_repairs import verdict

    source = tmp_path / 'source.pdf'
    source.write_bytes(b'synthetic-pdf')
    (tmp_path / 'output').mkdir()
    image = tmp_path / 'output/page.png'
    image.write_bytes(b'synthetic-image')
    original = '12\n\n<table><tr><td>数量</td><td>12</td></tr></table>\n\n正文1939年。\n\n13'
    backend = SimpleNamespace(name='fixture', convert_document=lambda *_: (
        [dict(page=1, image_path=image, text=original)], {}))
    seen = []

    def reviewer(packet, *_):
        seen.append(packet['target']['text'])
        assert not packet['target']['text'].startswith('12\n')
        assert not packet['target']['text'].endswith('\n13')
        assert '<td>12</td>' in packet['target']['text']
        assert '1939年' in packet['target']['text']
        return verdict()

    run_document(source, tmp_path / 'output', SimpleNamespace(
        vision_review=SimpleNamespace(review_mode='full'),
        risk=SimpleNamespace(enabled=True, auto_accept=True)), backend,
        SimpleNamespace(report=lambda: {}), reviewer=reviewer)
    assert len(seen) == 1
    result = json.loads((tmp_path / 'output/semantic-acceptance.json').read_text('utf-8'))
    page = result['pages'][0]
    assert page['text'] == seen[0] and page['verified']
    assert page['layout_cleanup']['original_text'] == original
    assert page['receipts'][0]['target_sha256'] == sha256(page['text'].encode()).hexdigest()


def test_conversion_removes_page_numbers_before_blocks_and_retains_original(tmp_path):
    source = tmp_path / 'source.pdf'
    source.write_bytes(b'engineering-fixture-not-a-real-pdf')
    output = tmp_path / 'output'
    output.mkdir()
    image = output / 'page.png'
    image.write_bytes(b'engineering-fixture-not-an-image')
    original = '12\n\n# 第一章\n\n正文有1939年。\n\n13'
    page = dict(page=1, image_path=str(image), text=original, verified=True,
        concerns=[], changes=[], receipts=[{'verdict':{'review_state':'completed'}}])
    completion = {'pages':[page]}
    before = copy.deepcopy(completion)
    write_outputs(source, output, completion, ['fixture'], {})
    markdown = (output/'document.candidate.md').read_text(encoding='utf-8')
    assert '\n12\n' not in markdown and '\n13\n' not in markdown
    assert '正文有1939年。' in markdown
    assert completion == before
    review = json.loads((output/'reviews/page-001.json').read_bytes())
    trace = review['processing']['completion']['layout_cleanup']
    assert review['processing']['completion']['final_text_sha256'] == sha256(review['candidate_text'].encode()).hexdigest()
    assert trace['original_text'] == original
    assert [x['text'] for x in trace['removed']] == ['12','13']
    mapping = json.loads((output/'source-map.json').read_bytes())
    for span in mapping['spans']:
        selected = markdown[span['start']:span['end']]
        assert selected == review['candidate_text'][span['source_start']:span['source_end']]
        assert sha256(selected.encode()).hexdigest() == span['text_sha256']
    # Re-exporting a completed conversion does not remove newly exposed numeric content.
    cleaned = json.loads((output/'semantic-acceptance.json').read_bytes())
    write_outputs(source, output, cleaned, ['fixture'], {})
    assert (output/'document.candidate.md').read_text(encoding='utf-8') == markdown
