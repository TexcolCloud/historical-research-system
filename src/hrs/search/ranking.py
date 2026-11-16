"""Rank fusion and conservative query boosts, independent of storage and models."""

import re
from hashlib import sha256


def multipart_queries(query):
    """Split only an explicit two-part evidence request; never rewrite its facts."""
    match = re.fullmatch(
        r"(?:请)?分别(?:指出|找出|提供|说明)(.+?)，以及(.+?)[。？?]?", query.strip()
    )
    if not match or any(c in query for c in '“”"「」'):
        return []
    parts = [part.strip("。？? ") for part in match.groups()]
    if any(len(part) < 6 or "以及" in part for part in parts):
        return []
    return parts


def lexical_query(query, scope, alias_query=None, *, raw=False):
    phrases = [
        query,
        *re.findall(r'[“"「](.+?)[”"」]', query),
        *re.findall(r"(?<!\d)\d{4}(?!\d)", query),
    ]
    text_field, alias_field = (
        ("raw_text_search", "raw_text_aliases") if raw else ("text_search", "text_aliases")
    )
    alternatives = [{"multi_match": {"query": query, "fields": ["title_search^2", text_field]}}]
    if alias_query is not None:
        alternatives.append(
            {
                "multi_match": {
                    "query": alias_query,
                    "fields": ["title_aliases^0.35", alias_field + "^0.2"],
                }
            }
        )
    return {
        "bool": {
            "must": [{"bool": {"should": alternatives, "minimum_should_match": 1}}],
            "should": [
                {"match_phrase": {field: {"query": phrase, "boost": boost}}}
                for phrase in dict.fromkeys(phrases)
                for field, boost in [("title_search", 3), (text_field, 2)]
            ],
            "filter": scope,
        }
    }


def group_exact_results(hits):
    """Group exact source text, retaining all locations and the best single score."""
    groups = {}
    for hit in hits:
        key = hit["text"]
        if key not in groups:
            groups[key] = {
                **hit,
                "source_group_id": sha256(key.encode()).hexdigest(),
                "occurrences": [],
            }
        group = groups[key]
        group["score"] = max(group["score"], hit["score"])
        occurrences = hit.get("occurrences") or [
            {k: v for k, v in hit.items() if k != "occurrences"}
        ]
        for occurrence in occurrences:

            def location(row):
                return tuple(
                    row.get(k) for k in ("book_id", "run_id", "chapter_id", "start", "end")
                )

            previous = next(
                (o for o in group["occurrences"] if location(o) == location(occurrence)), None
            )
            if previous is None:
                group["occurrences"].append(occurrence)
            elif occurrence.get("score", 0) > previous.get("score", 0):
                previous.update(occurrence)
    return [
        {**group, "occurrences": group["occurrences"] if len(group["occurrences"]) > 1 else []}
        for group in groups.values()
    ]


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
    reserved = {row["_source"]["id"] for lane in rankings for row in lane[:lane_quota]}
    chosen = [h for h in ranked if h["id"] in reserved][:budget]
    chosen_ids = {h["id"] for h in chosen}
    chosen.extend(h for h in ranked if h["id"] not in chosen_ids)
    selected = {h["id"] for h in chosen[:budget]}
    return [h for h in ranked if h["id"] in selected]


def diverse_order(hits, relevance_window=None):
    """Optional chapter round-robin for explicitly comparative questions."""
    ranked = sorted(hits, key=lambda h: h["score"], reverse=True)
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
