"""Rank fusion and conservative query boosts, independent of storage and models."""

import re


def lexical_query(query, scope):
    phrases = [query, *re.findall(r'[“"「](.+?)[”"」]', query), *re.findall(r"(?<!\d)\d{4}(?!\d)", query)]
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


def candidates_for_rerank(rankings, budget, lane_quota=0):
    """Experimental lane reservations retain their fused order and total budget."""
    ranked = fuse(*rankings)
    reserved = {row['_source']['id'] for lane in rankings for row in lane[:lane_quota]}
    chosen = [h for h in ranked if h['id'] in reserved][:budget]
    chosen_ids = {h['id'] for h in chosen}
    chosen.extend(h for h in ranked if h['id'] not in chosen_ids)
    selected = {h['id'] for h in chosen[:budget]}
    return [h for h in ranked if h['id'] in selected]


def diverse_order(hits, relevance_window=None):
    """Optional chapter round-robin for explicitly comparative questions."""
    ranked = sorted(hits, key=lambda h: h['score'], reverse=True)
    relevance_window = relevance_window if relevance_window is not None else len(ranked)
    buckets = {}
    for hit in ranked[:relevance_window]:
        buckets.setdefault(hit["chapter_id"], []).append(hit)
    return [
        bucket[i]
        for i in range(max(map(len, buckets.values()), default=0))
        for bucket in buckets.values()
        if i < len(bucket)
    ] + ranked[relevance_window:]
