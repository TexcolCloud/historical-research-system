"""Rank fusion and conservative query boosts, independent of storage and models."""

import re


def lexical_query(query, scope):
    phrases = [query, *re.findall(r'[“"「](.+?)[”"」]', query), *re.findall(r"\b\d{4}\b", query)]
    return {
        "bool": {
            "must": [{"multi_match": {"query": query, "fields": ["title_search^2", "text_search"]}}],
            "should": [
                {"match_phrase": {field: {"query": phrase, "boost": boost}}}
                for phrase in dict.fromkeys(phrases)
                for field, boost in [("title_search", 3), ("text_search", 2)]
            ],
            "filter": scope,
        }
    }


def fuse(*rankings, constant=60):
    found = {}
    for ranking in rankings:
        seen = set()
        for rank, row in enumerate(ranking, 1):
            identity = row["_id"]
            if identity in seen:
                continue
            seen.add(identity)
            hit = found.setdefault(identity, {**row["_source"], "score": 0.0})
            hit["score"] += 1 / (constant + rank)
    return sorted(found.values(), key=lambda h: (-h["score"], h["id"]))


def diverse_order(hits):
    """Optional chapter round-robin for explicitly comparative questions."""
    buckets = {}
    for hit in sorted(hits, key=lambda h: h["score"], reverse=True):
        buckets.setdefault(hit["chapter_id"], []).append(hit)
    return [
        bucket[i]
        for i in range(max(map(len, buckets.values()), default=0))
        for bucket in buckets.values()
        if i < len(bucket)
    ]
