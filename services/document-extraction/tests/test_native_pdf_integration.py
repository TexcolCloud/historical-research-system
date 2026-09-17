"""Actual PDFium/Docling serialization with fake recognition and review models."""
import json
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

pytest.importorskip('docling')

from document_extraction import docling_conversion, ocr, pipeline
from document_extraction.models import OcrResult
from test_native_pdf import TEXT, make_pdf
from test_single_ocr_completion import verdict


def settings():
    return SimpleNamespace(render_dpi=72, native_pdf=True,
        ocr_backends=[{'name': 'paddleocr-vl', 'kind': 'paddleocr-vl'}],
        acceleration=SimpleNamespace(cpu_fallback=True),
        vision_review=SimpleNamespace(review_mode='full'), risk=SimpleNamespace(enabled=True, auto_accept=True))


def profile():
    return SimpleNamespace(torch_device='cpu', cpu_threads=1, report=lambda: {'device': 'cpu'})


def test_all_native_book_never_enters_gpu_or_loads_ocr(tmp_path, monkeypatch):
    source = make_pdf(tmp_path / 'source.pdf')
    forbidden = Mock(side_effect=AssertionError('native PDF must stay on CPU'))
    monkeypatch.setattr(pipeline, 'gpu_lease', forbidden)
    monkeypatch.setattr(pipeline, 'prepare_ocr', forbidden)
    monkeypatch.setattr(pipeline, 'detect', forbidden)
    monkeypatch.setattr(ocr, 'create', forbidden)
    cfg = settings()
    backend = pipeline.ScheduledConverter(cfg, profile())
    result = pipeline.run_document(source, tmp_path / 'out', cfg, backend, profile(), reviewer=forbidden)
    assert result['pending_human_review'] == 0
    assert result['runtime']['timings']['ocr_calls'] == 0
    assert result['runtime']['timings']['native_pages'] == 1
    text = (tmp_path / 'out/document.candidate.md').read_text('utf-8')
    assert all(line in text for line in TEXT)
    forbidden.assert_not_called()


def test_readiness_check_still_initializes_the_selected_recognizer(tmp_path, monkeypatch):
    from document_extraction import cli
    cfg = settings()
    cfg.env_file, cfg.model_root = tmp_path / '.env', tmp_path
    cfg.vision_review = SimpleNamespace(api_key='local', api_mode='chat_completions',
        endpoint='http://fixture.invalid', model='fixture', enabled=True)
    create = Mock(return_value=SimpleNamespace(name='paddleocr-vl'))
    monkeypatch.setattr(ocr, 'create', create)
    monkeypatch.setattr(cli, 'detect', lambda _: profile())
    assert cli.check(cfg) == 0
    create.assert_called_once()


def test_mixed_book_only_ocr_and_reviews_uncertain_pages_with_contiguous_runs(tmp_path, monkeypatch):
    native = f'BT /F1 12 Tf 50 700 Td ({TEXT[0]}) Tj 0 -20 Td ({TEXT[1]}) Tj ET'
    source = make_pdf(tmp_path / 'source.pdf', [native, '0 0 100 100 re f', native, '0 0 80 80 re f', '0 0 90 90 re f'])
    calls, runs, leases, reviews = [], [], [], []
    def extract(image, *_):
        number = int(image.stem.split('-')[1])
        calls.append(number)
        return OcrResult(f'OCR fixture page {number}. This text is supplied by a test double.', [])
    def restructure(pages, output):
        runs.append([p['page'] for p in pages])
        (output / 'paddle-restructure.json').write_text(json.dumps({'tables': [], 'titles': []}))
        return {'page_count': len(pages)}
    backend = SimpleNamespace(name='paddleocr-vl', extract=extract, restructure_document=restructure)
    @contextmanager
    def lease():
        leases.append('enter')
        try:
            yield
        finally:
            leases.append('exit')
    monkeypatch.setattr(pipeline, 'gpu_lease', lease)
    monkeypatch.setattr(pipeline, 'prepare_ocr', lambda: None)
    monkeypatch.setattr(ocr, '_release_cuda', lambda: None)
    monkeypatch.setattr(ocr, 'create', lambda *_: backend)
    def review(packet, *_):
        reviews.append(packet['target']['page'])
        return verdict()
    cfg = settings()
    # detect takes the acceleration settings.
    monkeypatch.setattr(pipeline, 'detect', lambda _: profile())
    result = pipeline.run_document(source, tmp_path / 'out', cfg, pipeline.ScheduledConverter(cfg, profile()), profile(), reviewer=review)
    assert calls == [2, 4, 5]
    assert runs == [[2], [4, 5]]
    assert set(reviews) == {2, 4, 5}
    assert leases == ['enter', 'exit']
    assert result['pending_human_review'] == 0
    assert [p['page'] for p in result['pages']] == [1, 2, 3, 4, 5]
    assert result['paddle_restructure'] == 'paddle-restructure.json'


@pytest.mark.parametrize('page_count', [1, 3])
def test_native_export_mismatch_retries_through_ocr_under_lease(tmp_path, monkeypatch, page_count):
    native = f'BT /F1 12 Tf 50 700 Td ({TEXT[0]}) Tj 0 -20 Td ({TEXT[1]}) Tj ET'
    source = make_pdf(tmp_path / 'source.pdf', [native] * page_count)
    export = docling_conversion.export_markdown
    damaged = []
    def changed(document, page_no=None, **kwargs):
        text = export(document, page_no, **kwargs)
        if page_no and page_no not in damaged:
            damaged.append(page_no)
            return text.replace('120', '999')
        return text
    monkeypatch.setattr(docling_conversion, 'export_markdown', changed)
    lease_calls = []
    @contextmanager
    def lease():
        lease_calls.append(True)
        yield
    backend = SimpleNamespace(name='paddleocr-vl', extract=Mock(return_value=OcrResult('OCR fallback original text.', [])))
    monkeypatch.setattr(pipeline, 'gpu_lease', lease)
    monkeypatch.setattr(pipeline, 'prepare_ocr', lambda: None)
    monkeypatch.setattr(pipeline, 'detect', lambda _: profile())
    monkeypatch.setattr(ocr, '_release_cuda', lambda: None)
    monkeypatch.setattr(ocr, 'create', lambda *_: backend)
    pages, report = pipeline.ScheduledConverter(settings(), profile()).convert_document(source, tmp_path / 'out')
    assert lease_calls == [True]
    assert backend.extract.call_count == page_count
    assert pages[0]['extraction_method'] == 'ocr'
    assert pages[0]['native_probe']['reasons'] == ['native-export-difference']
    assert report['native_pages'] == 0
