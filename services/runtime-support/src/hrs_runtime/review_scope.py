"""Conservative typography matching for review locations, never fuzzy content repair."""

import re
import unicodedata


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
