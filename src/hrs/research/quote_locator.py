"""Locate literal quotes in a frozen source without interpreting or normalizing it."""


def locate_quotes(source: dict, quotes: list[str], offset=0, limit=20):
    """Return codepoint offsets and complete ambiguity counts with bounded result memory."""
    if (
        not isinstance(quotes, list)
        or not 1 <= len(quotes) <= 20
        or any(not isinstance(quote, str) or not 1 <= len(quote) <= 4000 for quote in quotes)
        or sum(map(len, quotes)) > 16000
    ):
        raise ValueError(
            "Quote lookup requires 1–20 literal strings, each 1–4000 characters, at most 16000 total"
        )
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 20:
        raise ValueError(
            "Quote lookup requires a nonnegative integer offset and an integer limit of 1–20"
        )
    text, counts, matches, total = source["text"], [], [], 0
    for index, quote in enumerate(quotes):
        cursor, count = 0, 0
        while (start := text.find(quote, cursor)) >= 0:
            if offset <= total < offset + limit:
                matches.append({"quote_index": index, "start": start, "end": start + len(quote)})
            total += 1
            count += 1
            cursor = start + 1
        counts.append({"quote_index": index, "total_matches": count})
    return {
        "article_id": source["id"],
        "text_sha256": source["text_sha256"],
        "quote_counts": counts,
        "matches": matches,
        "total": total,
        "offset": offset,
        "limit": limit,
        "truncated": offset + len(matches) < total,
    }
