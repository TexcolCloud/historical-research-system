"""Reversible layout exclusions in original Unicode coordinates, never OCR rewrites."""
import re
from hashlib import sha256

RULE_VERSION = "page-layout-v2"


def page_layout_exclusions(value, start, layout):
    """Call with a complete physical page, never with a search/chunk excerpt."""
    found = []
    hints = layout.get("block_text_hints", [])
    base = layout.get("start", start)
    protected = [(m.start(), m.end()) for m in re.finditer(
        r"(?ms)^\s*```.*?^\s*```[^\n]*|^\s*~~~.*?^\s*~~~[^\n]*|<table\b.*?</table>|^\[\^[^\]]+\]:[^\n]*(?:\n(?:[ \t]+[^\n]*|\s*$))*", value)]
    for hint in hints:
        if type(hint.get("start")) is not int or type(hint.get("end")) is not int:
            continue
        a, b = base + hint['start'] - start, base + hint['end'] - start
        label = hint.get('label') or ''
        if any(kind in label for kind in ('table', 'note', 'list', 'title', 'formula')):
            protected.append((a, b))
    lines = [m for m in re.finditer(r'[^\r\n]+', value) if m.group().strip()]
    if len(lines) > 1:
        for line in (lines[0], lines[-1]):
            # ponytail: edge-line rule selected by the user; keep uncertain nonnumeric furniture.
            if not re.fullmatch(r'[0-9０-９]{1,5}', line.group()):
                continue
            a, b = line.span()
            if any(left < b and right > a for left, right in protected):
                continue
            if not any(left < b and right > a for left, right, *_ in found):
                found.append((a, b, 'page_number', 'isolated-physical-page-edge'))
    return [dict(start=start+a, end=start+b, role=role, text=value[a:b],
                 text_sha256=sha256(value[a:b].encode()).hexdigest(), rule=RULE_VERSION, basis=basis)
            for a, b, role, basis in sorted(found)]
