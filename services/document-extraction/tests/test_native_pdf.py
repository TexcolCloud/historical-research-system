"""Synthetic PDFs test routing and evidence, not unseen-book OCR accuracy."""
import json
from contextlib import closing
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from document_extraction.artifacts import clean_page_numbers, write_outputs
from document_extraction.native_pdf import LEGACY_POLICY, POLICY, inspect_pdf, native_eligible
from document_extraction.provenance import text_hash
from document_extraction.semantic_completion import complete_document
from document_extraction.utils import sha256
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

TEXT = ("In 1938 the first district received 120 tons of grain.",
        "The second district received 80 tons in the following year.")


def make_pdf(path, contents=None):
    writer = PdfWriter()
    streams = contents or [f"BT /F1 12 Tf 50 700 Td ({TEXT[0]}) Tj 0 -20 Td ({TEXT[1]}) Tj ET"]
    for content in streams:
        page = writer.add_blank_page(width=600, height=800)
        font = DictionaryObject({NameObject('/Type'): NameObject('/Font'),
                                 NameObject('/Subtype'): NameObject('/Type1'),
                                 NameObject('/BaseFont'): NameObject('/Helvetica')})
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): font})})
        stream = DecodedStreamObject()
        stream.set_data(content.encode('ascii'))
        page[NameObject('/Contents')] = writer._add_object(stream)
    writer.write(path)
    return path


def native_page(tmp_path):
    import pypdfium2 as pdfium
    source = make_pdf(tmp_path / 'source.pdf')
    evidence = inspect_pdf(source, enabled=True)[1]
    assert evidence['route'] == 'native', evidence
    image = tmp_path / 'page.png'
    with pdfium.PdfDocument(source) as pdf, closing(pdf[0]) as page, closing(page.render(scale=1)) as bitmap:
        bitmap.to_pil().save(image)
    evidence['exported_text_sha256'] = text_hash(evidence['text'])
    return source, {'page': 1, 'image_path': image, 'text': evidence['text'], 'ocr': {},
                    'extraction_method': 'native_pdf', 'native_evidence': evidence,
                    'conversion_evidence': {'source_sha256': sha256(source), 'image_sha256': sha256(image)}}


def test_native_acceptance_has_no_model_receipt_and_keeps_source_map(tmp_path):
    source, page = native_page(tmp_path)
    reviewer = Mock(side_effect=AssertionError('native page must not invoke a model'))
    result = complete_document([clean_page_numbers(page)], SimpleNamespace(review_mode='full'), tmp_path, reviewer=reviewer)
    assert result['all_pages_accepted'] and not result['all_pages_verified']
    assert result['model_calls'] == 0 and not result['machine_review']
    assert result['pages'][0]['review_route'] == 'native-pass'
    assert result['pages'][0]['receipts'] == []
    manifest = write_outputs(source, tmp_path, result, ['paddleocr-vl'], {})
    assert manifest['pending_human_review'] == 0
    assert manifest['pages'][0]['acceptance_basis'] == 'native-pass'
    assert manifest['pages'][0]['extraction_method'] == 'native_pdf'
    mapping = json.loads((tmp_path / 'source-map.json').read_text('utf-8'))
    assert mapping['spans'][0]['page'] == 1
    reviewer.assert_not_called()
    from document_extraction.content_readiness import verify_quote
    assert verify_quote(tmp_path, TEXT[0])['status'] == 'evidence-linked'


@pytest.mark.parametrize('edit', ['text', 'word-boundary', 'source', 'image', 'geometry', 'policy'])
def test_native_acceptance_rejects_changed_evidence(tmp_path, edit):
    _, page = native_page(tmp_path)
    if edit == 'text':
        page['text'] = page['text'].replace('120', '900')
    elif edit == 'word-boundary':
        page['text'] = page['text'].replace('the first', 'thefirst')
        page['native_evidence']['exported_text_sha256'] = text_hash(page['text'])
    elif edit == 'source':
        page['conversion_evidence']['source_sha256'] = 'other'
    elif edit == 'image':
        page['image_path'].write_bytes(b'changed original')
    elif edit == 'geometry':
        page['native_evidence']['lines'][1]['bbox'] = [10, 10, 100, 100]
    else:
        page['native_evidence']['policy'] = 'unknown'
    assert not native_eligible(page)


@pytest.mark.parametrize('content,reason', [
    ('BT /F1 12 Tf 50 700 Td 3 Tr (Invisible text must never replace a scanned page.) Tj 0 -20 Td (Second line of hidden text.) Tj ET', 'hidden-transformed-or-clipped-text'),
    ('0 0 100 100 re f', 'non-text-page-object'),
    ('BT /F1 12 Tf 50 700 Td (A left column contains a long text.) Tj 300 0 Td (A right column is separate.) Tj ET', 'ambiguous-inline-order-or-columns'),
    ('BT /F1 12 Tf 50 700 Td (A body paragraph contains enough native text.) Tj /F1 8 Tf 0 -400 Td (A footnote belongs to that paragraph.) Tj ET', 'mixed-type-or-footnotes'),
    ('BT /F1 12 Tf 50 700 Td (One short line) Tj ET', 'sparse-text'),
    ('BT /F1 12 Tf 50 700 Td (District     Grain     Year) Tj 0 -20 Td (First        120       1938) Tj 0 -20 Td (Second       80        1939) Tj ET', 'table-or-list-like-text'),
    (f'BT /F1 12 Tf 50 700 Td ({TEXT[0]}) Tj 0 -100 Td ({TEXT[1]}) Tj ET', 'paragraph-boundary-or-detached-note'),
    (f'BT /F1 12 Tf 50 700 Td ({TEXT[0]}) Tj 24 -20 Td ({TEXT[1]}) Tj ET', 'multiple-columns-or-indentation'),
])
def test_frozen_v1_pages_keep_the_original_policy(tmp_path, content, reason):
    source = make_pdf(tmp_path / 'source.pdf', [content])
    evidence = inspect_pdf(source, enabled=LEGACY_POLICY)[1]
    assert evidence['route'] == 'ocr'
    assert reason in evidence['reasons']


def test_native_probe_disabled_for_legacy_config(tmp_path):
    assert inspect_pdf(make_pdf(tmp_path / 'source.pdf'), enabled=False) == {}


def test_mixed_review_keeps_whole_book_held_and_native_page_readable(tmp_path):
    source, native = native_page(tmp_path)
    scanned = {'page': 2, 'image_path': native['image_path'], 'text': 'uncertain OCR text'}
    calls = []
    def unavailable(packet, *_):
        calls.append(packet['target']['page'])
        return {'review_state': 'unavailable', 'reason': 'fixture offline'}
    result = complete_document([native, scanned], SimpleNamespace(review_mode='full'), tmp_path, reviewer=unavailable)
    manifest = write_outputs(source, tmp_path, result, ['paddleocr-vl'], {})
    assert calls and set(calls) == {2}
    assert manifest['pending_human_review'] == 1
    assert manifest['content_status'] != 'release-accepted'
    assert manifest['pages'][0]['status'] == 'release-accepted'


def test_review_policy_change_reuses_extraction_but_new_extraction_config_does_not(tmp_path):
    from document_extraction.stages import load_checkpoint, save_checkpoint
    source, page = native_page(tmp_path)
    config = tmp_path / 'config.json'
    values = {'native_pdf': {'policy': POLICY}, 'render_dpi': 150, 'vision_review': {'review_mode': 'full'}}
    config.write_text(json.dumps(values))
    # config is an input, not an output artifact.
    out = tmp_path / 'out'
    out.mkdir()
    moved = out / 'page.png'
    moved.write_bytes(page['image_path'].read_bytes())
    page['image_path'] = moved
    save_checkpoint(source, out, [page], {}, 'paddleocr-vl', {}, config)
    values['vision_review']['timeout_seconds'] = 300
    config.write_text(json.dumps(values))
    assert load_checkpoint(source, out, config)['pages'][0]['native_evidence'] == page['native_evidence']
    values['render_dpi'] = 300
    config.write_text(json.dumps(values))
    with pytest.raises(ValueError, match='different source or configuration'):
        load_checkpoint(source, out, config)


def test_explicit_revision_overrides_native_approval(tmp_path):
    _, page = native_page(tmp_path)
    reviewer = Mock(return_value={'review_state': 'unavailable', 'reason': 'offline'})
    result = complete_document([page], None, tmp_path, reviewer=reviewer, target_pages={1})
    assert not result['all_pages_accepted']
    assert reviewer.call_count >= 1


def test_v1_checkpoint_can_only_restore_under_legacy_full_review(tmp_path):
    from document_extraction.stages import load_checkpoint, save_checkpoint
    source, native = native_page(tmp_path)
    config = tmp_path / 'input.json'
    config.write_text('{}')
    out = tmp_path / 'legacy'
    out.mkdir()
    image = out / 'page.png'
    image.write_bytes(native['image_path'].read_bytes())
    page = {'page': 1, 'image_path': image, 'text': native['text']}
    checkpoint = save_checkpoint(source, out, [page], {}, 'paddleocr-vl', {}, config)
    checkpoint['schema_version'] = 1
    for key in ('config', 'extraction_sha256'):
        checkpoint.pop(key)
    (out / 'ocr-checkpoint.json').write_text(json.dumps(checkpoint), encoding='utf-8')
    config.write_text(json.dumps({'vision_review': {'review_mode': 'full'}}))
    loaded = load_checkpoint(source, out, config, reuse_legacy=True)
    assert not native_eligible(loaded['pages'][0])
    config.write_text(json.dumps({'native_pdf': {'policy': POLICY}, 'vision_review': {'review_mode': 'full'}}))
    with pytest.raises(ValueError):
        load_checkpoint(source, out, config, reuse_legacy=True)
