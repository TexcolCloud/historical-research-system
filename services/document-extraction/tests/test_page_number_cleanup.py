import copy
import json
from hashlib import sha256

from document_extraction.artifacts import write_outputs


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
