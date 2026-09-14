"""Conservative typography matching for review locations, never fuzzy content repair."""

import re
import unicodedata
from difflib import SequenceMatcher


def normalized(value):
    result, offsets = [], []
    for index, char in enumerate(value):
        for item in unicodedata.normalize("NFKC", char):
            if item.isspace():
                continue
            item = "-" if item in "—–―－-" else item
            if item == "-" and result and result[-1] == "-":
                offsets[-1] = (offsets[-1][0], index + 1)
                continue
            result.append(item)
            offsets.append((index, index + 1))
    return "".join(result), offsets


def locate(text, excerpt):
    if not excerpt:
        return None
    if text.count(excerpt) == 1:
        start = text.index(excerpt)
        return start, start + len(excerpt)
    value, offsets = normalized(text)
    needle, _ = normalized(excerpt)
    if not needle or value.count(needle) != 1:
        return None
    start = value.index(needle)
    return offsets[start][0], offsets[start + len(needle) - 1][1]


def split_change(change):
    """Separate actual inline edits from unchanged context; keep paragraph restructuring atomic."""
    before, after = change['before'], change['after']
    base = change.get('start_before', 0)
    edits = [(a, b, c, d) for tag, a, b, c, d in
             SequenceMatcher(None, before, after, autojunk=False).get_opcodes() if tag != 'equal']
    if any(char in before[a:b] or char in after[c:d]
           for a, b, c, d in edits for char in '\r\n'):
        # Proposals have no offsets; callers may only supply the located start.
        # Derive both bounds on every path, including unsplit structural edits.
        return [{**change, 'start_before': base, 'end_before': base + len(before)}]
    return [{**change, 'before':before[a:b], 'after':after[c:d],
             'start_before':base+a, 'end_before':base+b} for a, b, c, d in edits]


def concern_ranges(text, concern):
    """Use exact proposal diffs when available; a context quote alone is not an edit range."""
    proposal = concern.get('proposed_change')
    if proposal and proposal.get('before') and text.count(proposal['before']) == 1:
        base = text.index(proposal['before'])
        edits = split_change({**proposal, 'start_before':base})
        if edits:
            return [(max(0, c['start_before']-1), min(len(text), c['end_before']+1))
                    if c['start_before'] == c['end_before'] else (c['start_before'], c['end_before'])
                    for c in edits]
    scope = locate(text, concern.get('excerpt', ''))
    return [scope] if scope else None


def figure_page(text):
    """Route sparse image-bearing pages to carrier review, not map-label transcription."""
    images = re.findall(r"!\[[^\]]*\]\([^\n]+\)", text)
    rest = re.sub(r"!\[[^\]]*\]\([^\n]+\)", "", text)
    prose = any(
        len(block.strip()) > 120
        or (
            re.search(r"[。！？]", block) and not re.match(r"\s*(?:图|圖|Figure)\s*\d", block, re.I)
        )
        for block in re.split(r"\n\s*\n", rest)
    )
    return bool(images) and len(rest.strip()) < 600 and "<table" not in rest.lower() and not prose
