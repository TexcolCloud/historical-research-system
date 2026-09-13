"""Small, explicit source-range evaluations over published books."""

from math import ceil, inf, nextafter
from statistics import mean, median

from .outputs import fingerprint
from .retrieval_chunks import source_excerpt


def covered(start, end, ranges):
    cursor = start
    for a, b in sorted(ranges):
        if a > cursor:
            break
        cursor = max(cursor, b)
        if cursor >= end:
            return True
    return False


def evaluate(search, run_id, cases, *, semantic=False, limit=10):
    from .books import get_run

    run = get_run(search.engine, run_id)
    if run["kind"] != "book" or not (run["result"] or {}).get("published"):
        raise ValueError("Evaluation requires a published, reviewed book run.")
    return evaluate_cases(
        search, run_id, run["book_id"], cases, search.library.chapter, semantic=semantic, limit=limit
    )


def evaluate_cases(
    search, run_id, book_id, cases, load_chapter, *, semantic=False, limit=10, search_options=None
):
    if not isinstance(cases, list) or not cases or not 1 <= limit <= 50:
        raise ValueError("Provide nonempty evaluation cases and a result limit between 1 and 50.")
    chapters = {}

    def chapter(identity):
        if identity not in chapters:
            value = load_chapter(identity)
            if str(value["book_id"]) != str(book_id):
                raise ValueError("Evaluation evidence belongs to a different book.")
            chapters[identity] = value
        return chapters[identity]

    # Validate every expected source before spending any model calls.
    for case in cases:
        if (
            not isinstance(case.get("query"), str)
            or not 1 <= len(case["query"].strip()) <= 1000
            or not isinstance(case.get("expected"), list)
        ):
            raise ValueError("Every case needs a query and expected chapter character ranges.")
        for expected in case["expected"]:
            source_excerpt(chapter(expected["chapter_id"]), expected["start"], expected["end"])
    rows = []
    for case in cases:
        metrics = {}
        hits = search.search(
            case["query"], book_id, semantic=semantic, limit=limit, metrics=metrics, **(search_options or {})
        )
        evidence = [
            (str(hit["chapter_id"]), part, rank)
            for rank, hit in enumerate(hits, 1)
            for part in [hit, *hit.get("context", [])]
        ]
        valid = sum(
            part["text"] == chapter(identity)["text"][part["start"] : part["end"]]
            and part["sources"] == source_excerpt(chapter(identity), part["start"], part["end"])["sources"]
            for identity, part, _ in evidence
        )
        found = 0
        first_rank = None
        for expected in case["expected"]:
            matches = [
                (part["start"], part["end"], rank)
                for identity, part, rank in evidence
                if identity == expected["chapter_id"]
            ]
            if covered(expected["start"], expected["end"], [(a, b) for a, b, _ in matches]):
                found += 1
                rank = next(
                    r
                    for r in range(1, len(hits) + 1)
                    if covered(expected["start"], expected["end"], [(a, b) for a, b, n in matches if n <= r])
                )
                first_rank = min(first_rank or rank, rank)
        rows.append(
            {
                "query": case["query"],
                "category": case.get('category', 'unspecified'),
                "evidence_recall": found / len(case["expected"]) if case["expected"] else None,
                "unanswerable": not case["expected"],
                "unanswerable_returned_candidates": len(hits) if not case["expected"] else None,
                "reciprocal_rank": 1 / first_rank if first_rank else 0,
                "source_integrity": valid / len(evidence) if evidence else None,
                "returned": len(hits),
                "timing": metrics,
            }
        )
    durations = sorted(row["timing"]["total_ms"] for row in rows)
    return {
        "run_id": str(run_id),
        "semantic": semantic,
        "limit": limit,
        "cases_sha256": fingerprint(cases),
        "sources_sha256": {identity: fingerprint(value["text"]) for identity, value in chapters.items()},
        "evidence_recall": mean(row["evidence_recall"] for row in rows if row["evidence_recall"] is not None)
        if any(r["evidence_recall"] is not None for r in rows)
        else None,
        "mean_reciprocal_rank": mean(row["reciprocal_rank"] for row in rows),
        "complete_evidence_rate": mean(r['evidence_recall'] == 1 for r in rows if not r['unanswerable'])
        if any(not r['unanswerable'] for r in rows) else None,
        "p50_ms": median(durations),
        "p95_ms": durations[ceil(len(durations) * 0.95) - 1],
        "cases": rows,
        "categories": {
            category: {
                'cases': sum(r['category'] == category for r in rows),
                'evidence_recall': mean(values) if (values := [r['evidence_recall'] for r in rows
                     if r['category'] == category and r['evidence_recall'] is not None]) else None,
            } for category in sorted({r['category'] for r in rows})
        },
        "threshold_diagnostics": threshold_diagnostics(rows),
        "limitation": "Measures supplied source-range labels and provenance integrity, not historical truth or human approval.",
    }


def threshold_diagnostics(rows):
    """Development operating points, never an automatically adopted threshold."""
    scored = [r for r in rows if 'top_rerank_score' in r['timing']]
    if not scored or not any(r['unanswerable'] for r in scored) or all(r['unanswerable'] for r in scored):
        return []
    thresholds = sorted({r['timing']['top_rerank_score'] for r in scored})
    thresholds.append(nextafter(thresholds[-1], inf))
    return [{
        'threshold': threshold,
        'answerable_retention': mean(r['timing']['top_rerank_score'] >= threshold for r in scored if not r['unanswerable']),
        'unanswerable_rejection': mean(r['timing']['top_rerank_score'] < threshold for r in scored if r['unanswerable']),
    } for threshold in thresholds]
