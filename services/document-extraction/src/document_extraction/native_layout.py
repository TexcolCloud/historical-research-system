"""Native layout reconstruction with pdfplumber; no OCR or language models.

PDFium independently checks visible Unicode content. Here pdfplumber owns line
and cell extraction; the adapter preserves paragraphs, columns and table spans.
"""
import html
import math
import re
from collections import Counter
from html.parser import HTMLParser
from statistics import median

POLICY = 'native-pdf-layout-v2'


def _box(obj):
    return [float(obj[k]) for k in ('x0', 'top', 'x1', 'bottom')]


def _inside(box, container, tolerance=0):
    return (container[0] - tolerance <= box[0] <= box[2] <= container[2] + tolerance
            and container[1] - tolerance <= box[1] <= box[3] <= container[3] + tolerance)


def _overlap(a, b):
    return min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])


def _content(text):
    return Counter(c for c in text if not c.isspace())


def _escape(text):
    # Printed Markdown syntax is content, not instructions to the serializer.
    return re.sub(r'([\\`*_{}\[\]<>#!|+\-])', r'\\\1', text)


def _table(page, table):
    """Use detected cell rectangles to keep merged-cell ownership explicit."""
    from pdfplumber.utils import extract_text
    xs = sorted({x for c in table.cells for x in (c[0], c[2])})
    ys = sorted({y for c in table.cells for y in (c[1], c[3])})
    cells, covered = [], set()
    for box in table.cells:
        chars = [c for c in page.chars if _inside(_box(c), box, 1)]
        for c in chars:
            identity = id(c)
            if identity in covered:
                raise ValueError('ambiguous-table-cell-ownership')
            covered.add(identity)
        cells.append({'bbox': list(box), 'text': extract_text(chars) or '',
                      'row': ys.index(box[1]), 'col': xs.index(box[0]),
                      'rowspan': ys.index(box[3]) - ys.index(box[1]),
                      'colspan': xs.index(box[2]) - xs.index(box[0])})
    in_table = [c for c in page.chars if _inside(_box(c), table.bbox, 1)]
    if {id(c) for c in in_table} != covered:
        raise ValueError('unassigned-table-characters')
    rows = []
    for row in range(len(ys) - 1):
        values = sorted((c for c in cells if c['row'] == row), key=lambda c: c['col'])
        rows.append('<tr>' + ''.join(
            f'<td rowspan="{c["rowspan"]}" colspan="{c["colspan"]}">{html.escape(c["text"]).replace(chr(10), "<br>")}</td>'
            for c in values) + '</tr>')
    return {'kind': 'table', 'bbox': list(table.bbox), 'cells': cells,
            'text': '\n'.join(c['text'] for c in cells), 'markdown': '<table>' + ''.join(rows) + '</table>'}


def _order(blocks, uncertain):
    """Whitespace cuts separate full-width bands and then complete columns."""
    if len(blocks) < 2:
        return blocks
    for axis, minimum in ((0, 12), (1, 2)):
        ordered = sorted(blocks, key=lambda b: b['bbox'][axis])
        edge = ordered[0]['bbox'][axis + 2]
        for index, block in enumerate(ordered[1:], 1):
            if block['bbox'][axis] - edge >= minimum:
                return _order(ordered[:index], uncertain) + _order(ordered[index:], uncertain)
            edge = max(edge, block['bbox'][axis + 2])
    ordered = sorted(blocks, key=lambda b: (round(b['bbox'][1], 1), b['bbox'][0]))
    if any(_overlap(a['bbox'], b['bbox']) for i, a in enumerate(ordered) for b in ordered[i + 1:]):
        uncertain.append('overlapping-layout-regions')
    return ordered


def extract_layout(page, pdfium):
    from pdfplumber.utils import extract_text
    reasons = []
    if any(getattr(a.get('data', {}).get('Subtype'), 'name', None) != 'Link' for a in page.annots):
        raise ValueError('visible-annotation-needs-ocr-review')
    raw = pdfium['text']
    if not page.chars or _content(raw) != _content(''.join(c['text'] for c in page.chars)):
        raise ValueError('native-extractors-disagree')
    if any(not c.get('upright') for c in page.chars):
        raise ValueError('vertical-or-rotated-native-text')
    tables = page.find_tables()
    tables = [t for t in tables if len(t.rows) >= 2 and len(t.columns) >= 2]
    # A single numeric column is enough to make a borderless table ambiguous.
    lines = page.objects.get('textlinehorizontal', [])
    numeric_columns = sum(bool(re.fullmatch(r'[\d,.%+−-]+', line['text'].strip())) for line in lines) >= 2
    spaced_columns = sum(bool(re.search(r'\S\s{2,}\S', line['text']))
                         and bool(re.search(r'\d', line['text'])) for line in lines) >= 2
    if not tables and (numeric_columns or spaced_columns):
        candidates = page.find_tables({'vertical_strategy': 'text', 'horizontal_strategy': 'text',
                                      'min_words_vertical': 3, 'min_words_horizontal': 2})
        for table in candidates:
            rows = [r for r in table.extract() if any(r)]
            if len(rows) >= 3 and len(table.columns) >= 2 and sum(
                sum(bool(re.fullmatch(r'[\d,.%+−-]+', (c or '').strip())) for c in row) >= 1 for row in rows
            ) >= 2:
                tables.append(table)
                reasons.append('borderless-table-structure')
        if not tables:
            reasons.append('unresolved-numeric-column-structure')
    blocks = [_table(page, table) for table in tables]
    remaining = [c for c in page.chars if not any(_inside(_box(c), t.bbox, 1) for t in tables)]
    accounted = set()
    for line in page.objects.get('textlinehorizontal', []):
        chars = [c for c in remaining if _inside(_box(c), _box(line), 0.1)]
        if not chars:
            continue
        if any(id(c) in accounted for c in chars):
            raise ValueError('duplicate-line-ownership')
        accounted.update(id(c) for c in chars)
        sizes = [c['size'] for c in chars if c['text'].strip()]
        if sizes and max(sizes) > min(sizes) * 1.15:
            reasons.append('inline-font-or-note-ownership')
        value = extract_text(chars, x_tolerance=2, y_tolerance=3) or ''
        blocks.append({'kind': 'text', 'bbox': _box(line), 'text': value,
                       'font_size': median(c['size'] for c in chars), 'markdown': _escape(value)})
    if accounted != {id(c) for c in remaining}:
        raise ValueError('unassigned-native-characters')
    if _content(raw) != _content(''.join(b['text'] for b in blocks)):
        raise ValueError('layout-content-coverage')

    # Keep illustrations losslessly. Their contents require Qwen review, not OCR
    # of the already proven surrounding text. Full-page scans still use OCR.
    figures = [_box(obj) for obj in page.images]
    drawings = [obj for obj in [*page.lines, *page.rects, *page.curves]
                if not any(_inside(_box(obj), t.bbox, 2) for t in tables)]
    # A lone horizontal separator is retained in the original, not body content.
    if len(drawings) == 1 and drawings[0]['height'] <= 1:
        drawings = []
    if drawings:
        figures.append([min(o['x0'] for o in drawings), min(o['top'] for o in drawings),
                        max(o['x1'] for o in drawings), max(o['bottom'] for o in drawings)])
    # Include thin strokes and clip to the original page before raster cropping.
    figures = [[max(0, b[0] - 1), max(0, b[1] - 1), min(page.width, b[2] + 1), min(page.height, b[3] + 1)] for b in figures]
    for box in figures:
        if box[2] <= box[0] or box[3] <= box[1]:
            raise ValueError('invalid-figure-bounds')
        if (box[2] - box[0]) * (box[3] - box[1]) > page.width * page.height * 0.65:
            raise ValueError('image-dominant-page')
        reasons.append('retained-figure-needs-visual-check')
    for i, box in enumerate(figures):
        blocks.append({'kind': 'figure', 'bbox': box, 'text': '', 'figure_index': i,
                       'markdown': f'![](native-figure-{i + 1}.png)'})
    # Detached smaller-print notes follow the entire body, not just its left column.
    upper_sizes = [b['font_size'] for b in blocks if b['kind'] == 'text' and b['bbox'][1] < page.height / 2]
    body_size = median(upper_sizes or [b['font_size'] for b in blocks if b['kind'] == 'text']) if remaining else 0
    notes = [b for b in blocks if b['kind'] == 'text' and
             (b['font_size'] < body_size * 0.9 or re.match(r'^\s*(?:\[\d+\]|[①-⑳])', b['text']))]
    body = [b for b in blocks if b not in notes]
    detached = notes and body and min(b['bbox'][1] for b in notes) >= max(b['bbox'][3] for b in body) + 2
    if notes and body and not detached:
        # A marked note owns its continuation lines even at the body font size.
        boundary = min(b['bbox'][1] for b in notes)
        before = [b for b in blocks if b['bbox'][3] <= boundary - 2]
        after = [b for b in blocks if b['bbox'][1] >= boundary]
        if before and len(before) + len(after) == len(blocks):
            body, notes, detached = before, after, True
        else:
            reasons.append('uncertain-note-region')
    ordered = _order(body, reasons) + _order(notes, reasons) if detached else _order(blocks, reasons)
    paragraphs = []
    for block in ordered:
        previous = paragraphs[-1] if paragraphs else None
        if (previous and block['kind'] == previous['kind'] == 'text'
                and -1 <= block['bbox'][1] - previous['bbox'][3] <= block['font_size'] * 0.9
                and abs(block['font_size'] - previous['font_size']) <= 1
                and block['bbox'][0] - previous['bbox'][0] <= block['font_size'] * 0.5
                and abs(block['bbox'][0] - previous['bbox'][0]) < block['font_size'] * 3
                and not any(other['bbox'][0] >= block['bbox'][2] and other['bbox'][0] < previous['bbox'][2]
                            and abs(other['bbox'][1] - block['bbox'][1]) <= 2 for other in ordered if other is not block)
                and not re.match(r'^\s*(?:\[\d+\]|[①-⑳]|\d+[.)])', block['text'])):
            previous['markdown'] += '\n' + block['markdown']
            previous['text'] += '\n' + block['text']
            previous['bbox'] = [min(previous['bbox'][0], block['bbox'][0]), previous['bbox'][1],
                                max(previous['bbox'][2], block['bbox'][2]), block['bbox'][3]]
        else:
            paragraphs.append(dict(block))
    markdown = '\n\n'.join(b['markdown'] for b in paragraphs)
    return {'raw_text': raw, 'text': markdown, 'layout_blocks': paragraphs,
            'figures': figures, 'visual_reasons': sorted(set(reasons)),
            'native_char_count': sum(_content(raw).values())}


class _Structure(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks, self.cell, self.row, self.table, self.text = [], None, None, None, None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'table':
            self.table = []
        elif tag == 'tr':
            self.row = []
        elif tag in {'td', 'th'}:
            self.cell = [int(attrs.get('rowspan', 1)), int(attrs.get('colspan', 1)), '']
        elif tag == 'img':
            self.blocks.append(('image',))
        elif tag in {'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'pre'} and self.table is None:
            self.text = [tag, '']
        elif tag == 'br':
            self.handle_data(' ')

    def handle_data(self, data):
        if self.cell is not None:
            self.cell[2] += data
        elif self.text is not None:
            self.text[1] += data

    def handle_endtag(self, tag):
        if tag in {'td', 'th'} and self.cell is not None:
            self.row.append(tuple([*self.cell[:2], ' '.join(self.cell[2].split())]))
            self.cell = None
        elif tag == 'tr' and self.row is not None:
            self.table.append(self.row)
            self.row = None
        elif tag == 'table' and self.table is not None:
            self.blocks.append(('table', self.table))
            self.table = None
        elif self.text is not None and tag == self.text[0]:
            if self.text[1].strip():
                self.blocks.append((tag, ' '.join(self.text[1].split())))
            self.text = None


def structure(text):
    """Compare paragraphs and cell spans after Docling, not just character counts."""
    from markdown_it import MarkdownIt
    parser = _Structure()
    parser.feed(MarkdownIt('commonmark', {'html': True}).enable('table').render(text))
    return parser.blocks


def to_docling(evidence, image, dpi):
    """Map verified structure to public Docling types, avoiding Markdown reparsing."""
    from docling_core.types.doc import (
        BoundingBox,
        DocItemLabel,
        DoclingDocument,
        ImageRef,
        ProvenanceItem,
        TableCell,
        TableData,
    )
    document = DoclingDocument(name=f'native-page-{evidence["page"]}')
    width, height = evidence['page_size']
    for block in evidence['layout_blocks']:
        left, top, right, bottom = block['bbox']
        bbox = BoundingBox(l=left, t=top, r=right, b=bottom)
        prov = ProvenanceItem(page_no=evidence['page'], bbox=bbox, charspan=(0, len(block['text'])))
        if block['kind'] == 'text':
            document.add_text(label=DocItemLabel.TEXT, text=block['text'], prov=prov)
        elif block['kind'] == 'table':
            cells = [TableCell(text=c['text'], row_span=c['rowspan'], col_span=c['colspan'],
                              start_row_offset_idx=c['row'], end_row_offset_idx=c['row'] + c['rowspan'],
                              start_col_offset_idx=c['col'], end_col_offset_idx=c['col'] + c['colspan'])
                     for c in block['cells']]
            document.add_table(data=TableData(table_cells=cells,
                num_rows=max(c.end_row_offset_idx for c in cells),
                num_cols=max(c.end_col_offset_idx for c in cells)), prov=prov)
        elif block['kind'] == 'figure':
            crop = image.crop((left * image.width / width, top * image.height / height,
                               right * image.width / width, bottom * image.height / height))
            document.add_picture(image=ImageRef.from_pil(crop, dpi=dpi), prov=prov)
    return document


def validate_layout(evidence):
    errors = [reason for reason in evidence.get('inspection_errors', []) if reason in {
        'hidden-transformed-or-clipped-text', 'unreliable-unicode-mapping', 'rotation-or-annotations',
    } or reason.startswith('probe-failed:')]
    blocks = evidence.get('layout_blocks', [])
    if not blocks or _content(evidence.get('raw_text', '')) != _content(''.join(b['text'] for b in blocks)):
        errors.append('layout-content-coverage')
    width, height = evidence.get('page_size', [0, 0])
    if any(not all(math.isfinite(v) for v in b['bbox']) or not _inside(b['bbox'], [0, 0, width, height], 1) for b in blocks):
        errors.append('layout-outside-page')
    extent = [min(b['bbox'][0] for b in blocks), min(b['bbox'][1] for b in blocks),
              max(b['bbox'][2] for b in blocks), max(b['bbox'][3] for b in blocks)] if blocks else [0, 0, 0, 0]
    if any(not _inside(line['bbox'], extent, 1) for line in evidence.get('lines', [])):
        errors.append('line-layout-geometry-disagreement')
    return errors
