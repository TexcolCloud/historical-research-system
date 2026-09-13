"""Real HTTP/engine checks for the synthetic, publicly adopted protocol input."""

import argparse
import hashlib
import json
import subprocess
import sys

from engineering_support import MODULE, Engineering, load

from document_retrieval.client import AgentTools
from document_retrieval.engine import OpenSearch
from document_retrieval.records import new_id


def text(result):
    return "\n".join(unit.get("text", "") for unit in result.get("units", []))


def glyphs(run):
    positives = {}
    for name, query, mode in (
        ("single_cjk", "甲", "exact_quote"),
        ("unicode_code_point", "甲𠀀乙", "exact_quote"),
        ("english_substring", "ricalEvi", "exact_quote"),
        ("space_punctuation", "punctuation! whitespace preserved.", "exact_quote"),
        ("compatible_case", "HISTORICALEVIDENCE ABCDEF", "compatible_text"),
        ("compatible_traditional", "大后方", "compatible_text"),
        ("cross_soft_windows", run.parts["long"]["text"], "exact_quote"),
    ):
        result = run.search(query, mode=mode)
        assert result["items"], (name, result)
        reading = run.read(result["items"][0])
        assert reading["units"] and not reading.get("read_cursor"), (name, reading)
        for unit in reading["units"]:
            assert (
                hashlib.sha256(unit["text"].encode("utf-8")).hexdigest()
                == unit["source_anchor"]["text_sha256"]
            )
            assert unit["source_anchor"]["end"] - unit["source_anchor"]["start"] == len(
                unit["text"]
            )
        if name == "cross_soft_windows":
            assert run.parts["long"]["text"] in text(reading)
        if name == "compatible_traditional":
            assert "大後方" in text(reading) and "compatible" in " ".join(
                result["items"][0]["match_reasons"]
            )
        positives[name] = {"search": result, "read": reading}
    negatives = {}
    for query in (
        "HARDLEFTHARDRIGHT",
        "ARTLEFTARTRIGHT",
        "SEMLEFTSEMRIGHT",
        "VERSION_ONE_ONLYVERSION_TWO_ONLY",
        "台湾",
    ):
        result = run.search(
            query, mode="compatible_text" if query == "台湾" else "exact_quote", scope=run.scope
        )
        assert not result["items"] and result["outcome"] == "no_results", result
        negatives[query] = result
    rejected = run.client.http.post(
        "searches",
        json={
            "query": "长" * 8193,
            "mode": "exact_quote",
            "scope": run.main,
        },
        headers={"Idempotency-Key": new_id("oversize")},
    )
    assert rejected.status_code == 422
    manifest = load(run.settings.state_root / "normalization/mapping-manifest.json")
    assert manifest["include_tofu_risk_dictionaries"] is False
    assert manifest["unreviewed_mapping_policy"] == "preserve_original"
    return run.finish(
        "01-glyphs",
        {
            "positive": positives,
            "negative": negatives,
            "oversized_query": rejected.json(),
            "normalizer": manifest,
        },
    )


def organization(run):
    body = run.search("BODY_NOTE_TARGET")
    forward = run.read(body["items"][0], layer="related")
    assert "NOTE_ONE" in text(forward)
    note = run.search("NOTE_ONE")
    reverse = run.read(
        note["items"][0], layer="related", budget={**run.budget, "max_response_tokens": 16000}
    )
    assert "BODY_NOTE_TARGET" in text(reverse)
    ambiguous = run.search("AMBIGUOUS_NOTE_TARGET")
    unrelated = run.read(ambiguous["items"][0], layer="related")
    assert not unrelated["units"] and any(
        i["code"] == "no_explicit_related_evidence" for i in unrelated["issues"]
    )
    versions = run.search("COMMON_VERSION_QUERY", scope=run.scope)
    assert {i["citation"]["snapshot_id"] for i in versions["items"]} == {
        s["snapshot_id"] for s in run.scope["sources"]
    }
    assert len(versions["items"]) == 2
    metadata = {}
    for year, assertion, expected in ((1937, "adopted", 1), (1939, "adopted", 0), (1939, "all", 1)):
        result = run.search(
            "COMMON_VERSION_QUERY",
            scope=run.scope,
            filters=[
                {
                    "field_ref": "edition.publication_year",
                    "operator": "equals",
                    "value": year,
                    "assertion_scope": assertion,
                }
            ],
            include_undetermined=True,
        )
        assert len(result["items"]) == expected and len(result["undetermined_items"]) == 1, result
        if assertion == "all":
            assert any(m["conflict"] for m in result["items"][0]["metadata_evidence"]["matches"])
        metadata[f"{year}-{assertion}"] = result
    unknown = run.search("UNKNOWN_STRUCTURE_TARGET")
    unknown_read = run.read(unknown["items"][0])
    assert "UNKNOWN_STRUCTURE_TARGET" in text(unknown_read)
    assert all("cell" not in u and "row" not in u for u in unknown_read["units"])
    return run.finish(
        "02-organization",
        {
            "body": body,
            "note_forward": forward,
            "note_reverse": reverse,
            "ambiguous": unrelated,
            "versions": versions,
            "bibliography": metadata,
            "unknown": unknown_read,
            "real_cross_page_long_location_evidence": "Reported separately in fixed real-source quality tasks.",
        },
    )


def table_and_groups(run):
    tail = run.search("ROW_TAIL_ZEBRA")
    assert len(tail["items"]) == 1 and tail["items"][0]["kind"] == "table"
    read = run.read(
        tail["items"][0],
        budget={**run.budget, "max_excerpt_tokens": 4096, "max_response_tokens": 16000},
    )
    assert (
        "项目 | 值 | 单位" in text(read)
        and "ROW_000" in text(read)
        and "ROW_TAIL_ZEBRA" in text(read)
    )
    hybrid = run.search("ROW_TAIL_ZEBRA 540 records", mode="hybrid")
    assert hybrid["stages"]["vector"]["executed"] and hybrid["stages"]["rerank"]["executed"]
    assert len([i for i in hybrid["items"] if i["kind"] == "table"]) == 1
    store = run.store()
    generation = run.client.get("management/status")["active_generation"]
    builds = [
        store.get("build", store.snapshot_binding(generation, s["snapshot_id"])["build_id"])
        for s in run.scope["sources"]
    ]
    assert len(builds) == 2
    projections = [p for b in builds for p in b["binding"]["text_projections"]]
    engine = OpenSearch(run.settings)
    observed = []
    original = engine.request

    def request(method, path, **kwargs):
        result = original(method, path, **kwargs)
        observed.append(
            {"method": method, "path": path, "body": kwargs.get("json"), "response": result.json()}
        )
        return result

    engine.request = request
    try:
        base = engine.candidates(
            generation,
            projections,
            "lexical",
            query="COMMON_VERSION_QUERY",
            normalized="common_version_query",
            limit=3,
        )
        grouped = engine.candidates(
            generation,
            [*projections, *[f"nonmatching-protocol-{i}" for i in range(32000)]],
            "lexical",
            query="COMMON_VERSION_QUERY",
            normalized="common_version_query",
            limit=3,
        )
        assert [(i["id"], i["channel_score"]) for i in base["items"]] == [
            (i["id"], i["channel_score"]) for i in grouped["items"]
        ]
        assert len(observed) == 2 and observed[1]["body"]["size"] == 3
        assert len(observed[1]["body"]["query"]["bool"]["filter"][0]["bool"]["should"]) == 2
        windows = []
        for build in builds:
            records = load(run.settings.state_root / build["binding"]["records_ref"]["path"])
            table_windows = [r for r in records if r["record_type"] == "table_window"]
            if table_windows:
                assert any("ROW_TAIL_ZEBRA" in r["display"] for r in table_windows)
                windows.extend(table_windows)
        assert len(windows) > 1
    finally:
        engine.close()
    return run.finish(
        "03-table-and-grouping",
        {
            "literal_tail": tail,
            "whole_table": read,
            "hybrid_tail": hybrid,
            "table_windows": windows,
            "engine_requests": observed,
            "grouping_scope": "Two real projections plus 32000 nonmatching selector IDs; global query/ranking proof only, not capacity or full-range top-K.",
        },
    )


def three_entries(run):
    selected = run.client.post(
        "sources/resolve",
        {
            "selection": {
                "kind": "target",
                "target_type": "document",
                "target_id": run.fixture["allocated_ids"]["document"],
            },
            "purpose": "card_input",
        },
        new_id("ambiguous"),
    )
    selected = run.client.completed(selected, run.budget)
    assert selected["outcome"] == "source_selection_required" and len(selected["items"]) == 2
    body = {
        "query": "甲𠀀乙",
        "mode": "exact_quote",
        "scope": run.main,
        "purpose": "card_input",
        "budget": run.budget,
    }
    key = run.run_id + ":three-entries"
    http = run.client.completed(run.client.post("searches", body, key), run.budget)
    raw = AgentTools(run.client).invoke(
        "search_evidence", {"kind": "search", **body}, operation_key=key
    )
    assert raw == run.client.last_raw
    python = json.loads(raw)
    path = run.root / "中文请求"
    path.mkdir(exist_ok=True)
    from document_retrieval.records import atomic_json

    atomic_json(path / "查找.json", body)
    output = path / "结果.json"
    command = [
        sys.executable,
        "-X",
        "utf8",
        "-m",
        "document_retrieval.cli",
        "--url",
        run.url,
        "--key",
        key,
        "--wait",
        "--output",
        str(output),
        "search",
        "--request",
        str(path / "查找.json"),
    ]
    process = subprocess.run(
        command, cwd=MODULE, capture_output=True, text=True, encoding="utf-8", check=False
    )
    assert process.returncode == 0, process.stderr
    cli = load(output)
    assert http["list_id"] == python["list_id"] == cli["list_id"]
    assert http["items"] == python["items"] == cli["items"]
    conflict = run.expect_problem(
        lambda: run.client.post("searches", {**body, "query": "different"}, key),
        {"idempotency_conflict"},
    )
    return run.finish(
        "04-three-entries",
        {
            "source_selection": selected,
            "http": http,
            "python": python,
            "cli": cli,
            "cli_command": command,
            "changed_parameters": conflict,
            "queue_timeout_and_lost_reply": "Covered by the actual interruption scenarios.",
        },
    )


def budgets(run):
    table = run.search("ROW_TAIL_ZEBRA")
    small = run.read(
        table["items"][0],
        budget={"max_items": 1, "max_excerpt_tokens": 50, "max_response_tokens": 1200},
    )
    assert small["read_cursor"] and small["units"][0]["omitted"]
    wrong_list = run.expect_problem(
        lambda: run.client.post(
            f"searches/{table['list_id']}/results",
            {"cursor": small["read_cursor"], "budget": run.budget},
        ),
        {"reference_not_found", "cursor_mismatch"},
    )
    results = run.search(
        "ROW", budget={"max_items": 1, "max_excerpt_tokens": 12, "max_response_tokens": 2200}
    )
    if results["next_cursor"]:
        wrong_read = run.expect_problem(
            lambda: run.client.post(
                "reads", {"selection": {"kind": "continue", "read_cursor": results["next_cursor"]}}
            ),
            {"reference_not_found", "cursor_mismatch"},
        )
    else:
        other = run.search(
            "COMMON_VERSION_QUERY",
            scope=run.scope,
            budget={"max_items": 1, "max_response_tokens": 3000},
        )
        assert other["next_cursor"]
        wrong_read = run.expect_problem(
            lambda: run.client.post(
                "reads", {"selection": {"kind": "continue", "read_cursor": other["next_cursor"]}}
            ),
            {"reference_not_found", "cursor_mismatch"},
        )
    cursor, reads, seen = small["read_cursor"], [small], set()
    while cursor:
        assert cursor not in seen and len(reads) < 30
        seen.add(cursor)
        part = run.client.post(
            "reads",
            {
                "selection": {"kind": "continue", "read_cursor": cursor},
                "budget": {"max_items": 6, "max_excerpt_tokens": 4096, "max_response_tokens": 6000},
            },
        )
        assert part["units"] or part["outcome"] == "read_complete"
        reads.append(part)
        cursor = part["read_cursor"]
    assert "ROW_TAIL_ZEBRA" in "".join(text(r) for r in reads)
    small_error = run.expect_problem(
        lambda: run.client.post(
            f"searches/{table['list_id']}/results", {"budget": {"max_response_tokens": 128}}
        ),
        {"budget_too_small"},
    )
    unknown = run.expect_problem(
        lambda: run.client.post(
            f"searches/{table['list_id']}/results", {"budget": {"counter_ref": "unknown-tokenizer"}}
        ),
        {"counter_unsupported"},
    )
    return run.finish(
        "05-budgets",
        {
            "truncated_table": small,
            "continued": reads,
            "wrong_list_cursor": wrong_list,
            "wrong_read_cursor": wrong_read,
            "insufficient_citation": small_error,
            "unknown_counter": unknown,
            "all_HTTP_full_bytes_and_proxy_counts": str(run.trace_folder),
            "real_streaming_media": "Recorded separately against actual PDF/page-image assets in Linux and held-out media tasks.",
        },
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--fixture", default="state/engineering-protocol-20260908-v2/fixture.json")
    parser.add_argument(
        "--groups", default="glyphs,organization,table_and_groups,three_entries,budgets"
    )
    args = parser.parse_args()
    run = Engineering(args.run_id, args.fixture)
    run.start()
    if not (run.root / "initial-index.json").exists():
        run.save("initial-index", run.index("initial-index"))
    for group in args.groups.split(","):
        globals()[group](run)
    run.client.close()


if __name__ == "__main__":
    main()
