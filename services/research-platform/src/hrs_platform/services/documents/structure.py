"""Source-bound structure projections. Canonical chapter text is never rewritten."""

import re
from collections import defaultdict
from hashlib import sha256
from html.parser import HTMLParser

from hrs_platform.services.documents.footnotes import resolve_footnotes
from hrs_platform.services.retrieval.chunks import TableRows, blocks

POLICY = 'source-bound-reading-structure-v2-local-rowspans'


class Cells(HTMLParser):
    """Compare row/column ownership, not serializer whitespace or HTML attributes."""

    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.rows, self.cell, self.depth = [], None, 0
        self.valid = True
        self.feed(text)
        self.valid &= self.cell is None and self.depth == 0

    def handle_starttag(self, tag, attrs):
        if tag == 'table':
            self.depth += 1
            self.valid &= self.depth == 1
        if tag == 'caption':
            self.valid = False  # Keep captioned/nested tables in their original layout.
        if tag == 'tr':
            self.rows.append([])
        if tag in {'td', 'th'}:
            attrs = dict(attrs)
            self.cell = [attrs.get('rowspan', '1'), attrs.get('colspan', '1'), '']
        if tag in {'img', 'sup'}:
            self.valid = False

    def handle_data(self, data):
        if self.cell is not None:
            self.cell[2] += data
        elif data.strip():
            self.valid = False

    def handle_endtag(self, tag):
        if tag == 'table':
            self.depth -= 1
        if tag in {'td', 'th'} and self.cell is not None:
            if not self.rows:
                self.valid = False
            else:
                self.rows[-1].append((*self.cell[:2], re.sub(r'\s+', '', self.cell[2])))
            self.cell = None


def table_width(rows):
    """Reject ragged/overlapping cells and rowspans crossing a source page boundary."""
    occupied, width = {}, None
    for index, row in enumerate(rows):
        column = 0
        for rowspan, colspan, _ in row:
            if not rowspan.isdigit() or not colspan.isdigit():
                return None
            height, span = int(rowspan), int(colspan)
            if height < 1 or span < 1 or span > 1000 or index + height > len(rows):
                return None
            while occupied.get(column, 0) > index:
                column += 1
            if any(occupied.get(c, 0) > index for c in range(column, column + span)):
                return None
            for c in range(column, column + span):
                occupied[c] = index + height
            column += span
        active = {c for c, end in occupied.items() if end > index}
        width = width if width is not None else len(active)
        if active != set(range(width)):
            return None
    return width


def merge_table_parts(texts, candidate):
    """A supplied continuation candidate must preserve every cell and column owner."""
    parsed = [Cells(text) for text in texts]
    wanted = Cells(candidate)
    if not parsed or not all(p.valid and p.rows and all(p.rows) for p in [*parsed, wanted]):
        return None
    first_rows = TableRows(texts[0]).rows
    if not first_rows:
        return None
    header_count = 0
    for row in first_rows:
        if not row[3]:
            break
        header_count += 1
    header_count = header_count or 1
    header = parsed[0].rows[:header_count]
    widths = [table_width(p.rows) for p in [*parsed, wanted]]
    if not widths[0] or any(width != widths[0] for width in widths):
        return None
    expected, additions = list(parsed[0].rows), []
    for raw, p in zip(texts[1:], parsed[1:], strict=True):
        rows = TableRows(raw).rows
        skip = header_count if p.rows[:header_count] == header else 0
        if len(rows) != len(p.rows) or len(rows) <= skip:
            return None
        if any(i + int(cell[0]) > skip for i, row in enumerate(p.rows[:skip]) for cell in row):
            return None
        expected.extend(p.rows[skip:])
        additions.extend(raw[a:b] for a, b, _, _ in rows[skip:])
    if expected != wanted.rows:
        return None
    end = first_rows[-1][1]
    return texts[0][:end] + ''.join(additions) + texts[0][end:]


def page_ranges(parts):
    offset, result = 0, []
    for part in parts:
        result.append((offset, offset + len(part['text']), part['source']['pages']))
        offset += len(part['text'])
    return result


def join_paragraphs(members):
    value = members[0]['text'].rstrip()
    for member in members[1:]:
        tail = member['text'].strip()
        space = ' ' if value and tail and value[-1].isascii() and value[-1].isalnum() and tail[0].isascii() and tail[0].isalnum() else ''
        value += space + tail
    return value


def describe_structure(document, metadata, completion, approved_pages):
    text, parts = document['text'], document['parts']
    ranges = page_ranges(parts)
    units = blocks(text)

    def pages_for(start, end):
        return sorted({p for a, b, pages in ranges if a < end and b > start for p in pages})

    by_page = defaultdict(list)
    for block in units:
        a, b = block['start'], block['end']
        pages = pages_for(a, b)
        if len(pages) == 1:
            by_page[pages[0]].append((block['kind'], {'start': a, 'end': b, 'text': text[a:b], 'pages': pages}))

    def find(page, value, *, kind=None, signature=False):
        matches = []
        wanted = Cells(value).rows if signature else None
        for unit_kind, located in by_page[page]:
            if kind and unit_kind != kind:
                continue
            raw = located['text']
            if (Cells(raw).rows == wanted if signature else value and raw.count(value) == 1):
                matches.append(located)
        return matches[0] if len(matches) == 1 else None

    items = []

    def add(kind, title, pages, members, display=None, reason='', **extra):
        located = members and all(members)
        ready = bool(located and set(pages) <= approved_pages and (kind == 'heading' or display))
        if not located:
            reason = '来源片段已变化或不能唯一定位，保留原结构。'
        elif not set(pages) <= approved_pages:
            reason = '相关页面尚未完成内容核对，暂不采用结构候选。'
        elif not ready:
            reason = reason or '候选结构无法通过来源一致性检查，保留原结构。'
        members = [m for m in members if m]
        identity = sha256(repr((POLICY, kind, title, pages, [(m['text'], m['pages']) for m in members])).encode()).hexdigest()
        item = {'id': identity, 'kind': kind, 'title': title, 'pages': pages,
                'status': 'ready' if ready else 'retained', 'reason': reason,
                'members': members, 'display_text': display if ready else None, **extra}
        items.append(item)
        return item

    for group in metadata.get('tables', []):
        members = [find(m['page'], m['original_html'], kind='html_table', signature=True)
                   for m in group['members']]
        display = merge_table_parts([m['text'] for m in members], group['merged_html']) if all(members) else None
        add('table', '跨页表格', group['pages'], members, display,
            reason='表头、行列归属及数值通过来源一致性检查，原件逐页保留。' if display else '')
    for heading in metadata.get('titles', []):
        target = find(heading['page'], heading.get('after') or heading['title'], kind='heading')
        add('heading', heading['title'], [heading['page']], [target],
            reason='沿用来源标题层级；下级标题仍属于所在章节。', level=heading['level'],
            before=heading.get('before', ''), after=heading.get('after', ''))
    for previous, page in zip(completion, completion[1:]):
        evidence = page.get('structure', {})
        if not evidence.get('continues_previous') or page['page'] != previous['page'] + 1:
            continue
        left = find(previous['page'], evidence.get('continuation_before', ''), kind='paragraph')
        right = find(page['page'], evidence.get('continuation_after', ''), kind='paragraph')
        display = None
        if left and right and left['end'] <= right['start'] and not text[left['end']:right['start']].strip():
            if not re.search(r'\[\^|[①-⑳]|<sup|!\[', left['text'] + right['text']):
                display = join_paragraphs([left, right])
        current = add('continuation', '跨页续段', [previous['page'], page['page']], [left, right], display,
                      reason='依据逐页核验的原文锚点连接，保留各页出处。' if display else '')
        if current['status'] == 'ready' and len(items) > 1:
            preceding = items[-2]
            if preceding['kind'] == 'continuation' and preceding['status'] == 'ready' and preceding['members'][-1] == left:
                members = [*preceding['members'], right]
                items[-2:] = []
                add('continuation', '跨页续段', sorted(set(preceding['pages'] + current['pages'])),
                    members, join_paragraphs(members), reason=current['reason'])
    return {'policy': POLICY, 'available': bool(metadata or completion), 'items': items}


def project_structure(report, parts):
    """Project document coordinates into one chapter; never join across chapters."""
    offsets, offset = [], 0
    for part in parts:
        offsets.append((part['start'], part['end'], offset))
        offset += len(part['text'])
    result = []
    for item in report.get('items', []):
        members = []
        for m in item['members']:
            found = [(max(a, m['start']), min(b, m['end']), c - a) for a, b, c in offsets
                     if a < m['end'] and b > m['start']]
            if found and sum(b-a for a, b, _ in found) == m['end'] - m['start']:
                members.append({**m, 'start': found[0][0] + found[0][2], 'end': found[-1][1] + found[-1][2]})
        if not members:
            continue
        complete = len(members) == len(item['members'])
        result.append({**item, 'members': members,
                       **({} if complete else {'status': 'retained', 'display_text': None,
                                              'reason': '结构跨越章节边界，在原件中对照。'})})
    return result


def reading_view(chapter):
    text, replacements = chapter['text'], []
    for item in chapter.get('structure', []):
        members = item['members']
        if item['status'] != 'ready' or not item.get('display_text') or not members:
            continue
        if any(text[a['end']:b['start']].strip() for a, b in zip(members, members[1:])):
            continue  # An intervening caption/note remains in reading order.
        start, end = members[0]['start'], members[-1]['end']
        if any(start < b and end > a for a, b, _, _ in replacements):
            continue
        replacements.append((start, end, item['display_text'], item['pages']))
    ranges, parts, cursor = page_ranges(chapter['parts']), [], 0

    def preserve(start, end):
        for a, b, pages in ranges:
            if a < end and b > start:
                parts.append({'text': text[max(start, a):min(end, b)], 'source': {'pages': pages}})

    for start, end, value, pages in sorted(replacements):
        preserve(cursor, start)
        parts.append({'text': value + '\n\n', 'source': {'pages': pages}})
        cursor = end
    preserve(cursor, len(text))
    reading = ''.join(p['text'] for p in parts)
    return {'text': reading, 'footnotes': resolve_footnotes(reading, parts)}
