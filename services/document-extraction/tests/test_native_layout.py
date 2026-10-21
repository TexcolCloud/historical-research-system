"""Broader native acceptance keeps paragraph, column and cell ownership."""
from unittest.mock import Mock

import pytest
from document_extraction.native_layout import structure
from document_extraction.native_pdf import inspect_pdf
from test_native_pdf import TEXT, make_pdf


def text_at(text, x, y, size=12):
    return f' BT /F1 {size} Tf {x} {y} Td ({text}) Tj ET '


def grid(merged=False):
    edges = ' '.join(f'50 {y} m 350 {y} l S' for y in (400, 430, 460, 490))
    edges += ' ' + ' '.join(f'{x} 400 m {x} 490 l S' for x in (50, 350))
    edges += f' 200 400 m 200 {460 if merged else 490} l S '
    rows = [('District totals' if merged else 'District', 60, 475)]
    if not merged:
        rows.append(('Grain', 210, 475))
    rows.extend([('First', 60, 445), ('120', 210, 445), ('Second', 60, 415), ('80', 210, 415)])
    return edges + ' '.join(text_at(*row) for row in rows)


CASES = {
    'indent': text_at(TEXT[0], 50, 700) + text_at(TEXT[1], 74, 680),
    'paragraph_gap': text_at(TEXT[0], 50, 700) + text_at(TEXT[1], 50, 580),
    'two_columns': ''.join(text_at(text, x, y) for text, x, y in [
        ('Left column first line.', 50, 700), ('Right column first line.', 330, 700),
        ('Left column second line.', 50, 680), ('Right column second line.', 330, 680)]),
    'three_columns': ''.join(text_at(f'Column {i} line {j}.', x, 700 - 20 * j) for j in range(2) for i, x in enumerate((30, 220, 410))),
    'heading_columns': text_at('Chapter heading across the two columns of the whole page.', 50, 750, 18)
        + ''.join(text_at(f'{side} column line {j}.', x, 700 - 20 * j) for j in range(2) for side, x in [('Left', 50), ('Right', 330)]),
    'close_heading': text_at('Chapter heading across the two columns of the whole page.', 50, 720)
        + ''.join(text_at(f'{side} column line {j}.', x, 700 - 20 * j) for j in range(2) for side, x in [('Left', 50), ('Right', 330)]),
    'column_notes': ''.join(text_at(f'{side} body line {j}.', x, 700 - 20 * j)
        for j in range(2) for side, x in [('Left', 50), ('Right', 330)])
        + text_at('[1] Short note.', 50, 100, 8),
    'long_column_notes': ''.join(text_at(f'{side} body line {j}.', x, 700 - 20 * j)
        for j in range(2) for side, x in [('Left', 50), ('Right', 330)])
        + ''.join(text_at('[1] First note line.' if j == 0 else f'Note continuation line {j}.', 50, 150 - 12 * j, 8) for j in range(5)),
    'footnote': text_at('Native body cites note [1].', 50, 700) + text_at('[1] The source records 120 tons.', 50, 100, 8),
    'colored_text': '0 0 0.8 rg ' + text_at(TEXT[0], 50, 700) + text_at(TEXT[1], 50, 680),
    'table': grid(),
    'merged_table': grid(True),
}


@pytest.mark.parametrize('name', CASES)
def test_verified_complex_native_layout_can_skip_vision(tmp_path, name):
    source = make_pdf(tmp_path / f'{name}.pdf', [CASES[name]])
    evidence = inspect_pdf(source, enabled=True)[1]
    assert evidence['route'] == 'native', evidence
    assert not evidence['visual_reasons'], evidence
    rendered = structure(evidence['text'])
    if name in {'indent', 'paragraph_gap', 'footnote'}:
        assert len(rendered) == 2
    elif name == 'two_columns':
        assert len(rendered) == 2
        assert 'Left column second line' in rendered[0][1]
        assert 'Right column first line' in rendered[1][1]
    elif name == 'three_columns':
        assert len(rendered) == 3
        assert all(f'Column {i}' in b[1] for i, b in enumerate(rendered))
    elif name in {'heading_columns', 'close_heading'}:
        assert len(rendered) == 3
        assert rendered[0][1].startswith('Chapter heading')
        assert 'Left column line 1' in rendered[1][1]
        assert 'Right column line 0' in rendered[2][1]
    elif name in {'column_notes', 'long_column_notes'}:
        assert len(rendered) == 3
        assert rendered[0][1].startswith('Left body')
        assert rendered[1][1].startswith('Right body')
        assert rendered[2][1].startswith('[1]')
        if name == 'long_column_notes':
            assert 'Note continuation line 4.' in rendered[2][1]
    elif 'table' in name:
        assert rendered[0][0] == 'table'
        assert rendered[0][1][1][1][2] == '120'
        assert rendered[0][1][2][1][2] == '80'
        if name == 'merged_table':
            assert rendered[0][1][0][0][:2] == (1, 2)


@pytest.mark.parametrize('name', CASES)
def test_actual_docling_preserves_complex_native_layout(tmp_path, monkeypatch, name):
    pytest.importorskip('docling')
    from document_extraction import ocr, pipeline
    from test_native_pdf_integration import profile, settings
    source = make_pdf(tmp_path / f'{name}.pdf', [CASES[name]])
    forbidden = Mock(side_effect=AssertionError('verified layout must not invoke models'))
    monkeypatch.setattr(ocr, 'create', forbidden)
    monkeypatch.setattr(pipeline, 'prepare_ocr', forbidden)
    cfg = settings()
    result = pipeline.run_document(source, tmp_path / 'out', cfg, pipeline.ScheduledConverter(cfg, profile()), profile(), reviewer=forbidden)
    assert result['pending_human_review'] == 0
    assert result['runtime']['timings']['ocr_calls'] == 0
    assert result['pages'][0]['acceptance_basis'] == 'native-pass'
    candidate = (tmp_path / 'out/document.candidate.md').read_text('utf-8')
    expected = inspect_pdf(source, enabled=True)[1]['text']
    assert structure(candidate) == structure(expected)
    forbidden.assert_not_called()


@pytest.mark.parametrize('drawing', ['100 300 200 100 re S', '100 300 m 100 400 l S'])
def test_native_graphic_keeps_original_and_uses_machine_review_only(tmp_path, monkeypatch, drawing):
    pytest.importorskip('docling')
    from document_extraction import ocr, pipeline
    from test_native_pdf_integration import profile, settings
    from test_single_ocr_completion import verdict
    source = make_pdf(tmp_path / 'figure.pdf', [text_at(TEXT[0], 50, 700) + drawing])
    evidence = inspect_pdf(source, enabled=True)[1]
    assert evidence['route'] == 'native'
    assert evidence['visual_reasons'] == ['retained-figure-needs-visual-check']
    forbidden = Mock(side_effect=AssertionError('native illustration should not load OCR'))
    monkeypatch.setattr(ocr, 'create', forbidden)
    monkeypatch.setattr(pipeline, 'prepare_ocr', forbidden)
    reviewer = Mock(return_value=verdict())
    cfg = settings()
    result = pipeline.run_document(source, tmp_path / 'out', cfg, pipeline.ScheduledConverter(cfg, profile()), profile(), reviewer=reviewer)
    assert result['pending_human_review'] == 0
    assert result['runtime']['timings']['ocr_calls'] == 0
    assert result['pages'][0]['acceptance_basis'] == 'deepseek'
    assert reviewer.call_count >= 1
    assert list((tmp_path / 'out/docling-assets').rglob('*.png'))
    assert 'docling-assets' in (tmp_path / 'out/document.candidate.md').read_text('utf-8')


def test_structure_comparison_catches_same_text_wrong_paragraph_or_cell():
    assert structure('First.\n\nSecond.') != structure('First. Second.')
    assert structure('<table><tr><td>First</td><td>120</td></tr></table>') != structure('<table><tr><td>120</td><td>First</td></tr></table>')
    assert structure('<table><tr><td colspan="2">120</td></tr></table>') != structure('<table><tr><td>120</td></tr></table>')


@pytest.mark.parametrize('content,reason', [
    ('BT /F1 12 Tf 50 700 Td (District     Grain     Year) Tj 0 -20 Td (First        120       1938) Tj 0 -20 Td (Second       80        1939) Tj ET', 'unresolved-numeric-column-structure'),
    (text_at('Body has a note', 50, 700) + text_at('1', 138, 705, 8) + text_at('after it.', 145, 700)
     + text_at('1 Note source.', 50, 100, 8), 'inline-font-or-note-ownership'),
])
def test_ambiguous_ownership_uses_machine_review_without_repeating_ocr(tmp_path, content, reason):
    evidence = inspect_pdf(make_pdf(tmp_path / 'ambiguous.pdf', [content]), enabled=True)[1]
    assert evidence['route'] == 'native', evidence
    assert reason in evidence['visual_reasons']


def test_borderless_table_with_one_numeric_column_is_not_auto_accepted(tmp_path):
    content = ''.join(text_at(t, x, y) for t, x, y in [
        ('District', 50, 700), ('Grain', 200, 700), ('First', 50, 680),
        ('120', 200, 680), ('Second', 50, 660), ('80', 200, 660)])
    evidence = inspect_pdf(make_pdf(tmp_path / 'table.pdf', [content]), enabled=True)[1]
    assert evidence['route'] == 'native'
    assert evidence['visual_reasons'] == ['borderless-table-structure']
    table = structure(evidence['text'])[0]
    assert table[0] == 'table'
    assert any([c[2] for c in row] == ['First', '120'] for row in table[1])


@pytest.mark.parametrize('stage', ['open', 'page', 'page-tree', 'page-count'])
def test_layout_parser_failure_falls_back_without_stopping_the_book(tmp_path, monkeypatch, stage):
    import pdfplumber
    from document_extraction import native_pdf
    from pdfplumber.utils.exceptions import PdfminerException
    content = text_at(TEXT[0], 50, 700)
    source = make_pdf(tmp_path / 'source.pdf', [content, content])
    if stage == 'open':
        monkeypatch.setattr(pdfplumber, 'open', Mock(side_effect=PdfminerException('unsupported layout')))
    elif stage in {'page-tree', 'page-count'}:
        from contextlib import nullcontext
        class BrokenPages:
            @property
            def pages(self):
                if stage == 'page-tree':
                    raise PdfminerException('incomplete page tree')
                return []
        monkeypatch.setattr(pdfplumber, 'open', lambda *args, **kwargs: nullcontext(BrokenPages()))
    else:
        extract = native_pdf.extract_layout
        def fail_first(page, evidence):
            if evidence['page'] == 1:
                raise PdfminerException('bad first page')
            return extract(page, evidence)
        monkeypatch.setattr(native_pdf, 'extract_layout', fail_first)
    plan = inspect_pdf(source, enabled=True)
    assert plan[1]['route'] == 'ocr'
    assert 'probe-failed:' in str(plan[1]['reasons'])
    assert plan[2]['route'] == ('native' if stage == 'page' else 'ocr')
