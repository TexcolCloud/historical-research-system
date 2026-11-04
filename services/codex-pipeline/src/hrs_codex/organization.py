"""Source-complete article partitions shared by publication and the legacy reader."""

import re


def article_parts(pages, articles):
    groups = [[] for _ in articles]
    offset = 0
    for page in pages:
        number, text = page["page"], page["approved"]["markdown"] + "\n\n"
        owners = [i for i, article in enumerate(articles) if number in article["pages"]]
        if not owners:
            raise ValueError(f"Organization omitted physical page {number}")
        starts = []
        for position, owner in enumerate(owners):
            start = 0
            if position:
                boundary = next(
                    (b for b in articles[owner].get("boundaries", []) if b["page"] == number), {}
                )
                anchor = boundary.get("start_text", "")
                if not anchor or text.count(anchor) != 1:
                    raise ValueError(
                        f"Article boundary on physical page {number} needs a unique start_text"
                    )
                start = text.index(anchor)
                # A literal visible heading may omit Markdown's opening markers.
                # Keep those markers with their heading, preserving every codepoint.
                line_start = text.rfind("\n", 0, start) + 1
                if re.fullmatch(r" {0,3}#{1,6}[ \t]+| {0,3}(?:\*\*|__)", text[line_start:start]):
                    start = line_start
            starts.append((start, owner))
        if starts != sorted(starts) or len({s for s, _ in starts}) != len(starts):
            raise ValueError("Article starts are out of source order")
        for index, (start, owner) in enumerate(starts):
            end = starts[index + 1][0] if index + 1 < len(starts) else len(text)
            groups[owner].append(
                {
                    "page": number,
                    "start": offset + start,
                    "end": offset + end,
                    "original_start": start,
                    "original_end": end,
                    "text": text[start:end],
                }
            )
        offset += len(text)
    assert sum(len(p["text"]) for group in groups for p in group) == offset
    return groups
