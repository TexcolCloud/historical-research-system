"""Source facts govern every retrieval view; synthetic examples certify no source."""

from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest

from hrs.search.chunks import expand_hits, indexable_chunks, retrieval_chunks
from hrs.search.directory_scope import DirectoryScope
from hrs.search.inputs import bounded_chunks
from hrs.search.projection import (
    lexical_text,
    prepare_projection,
    project_hit,
    slice_segments,
    source_excerpt,
    source_excerpts,
    source_ranges,
    visible_segments,
)
from hrs.search.ranking import group_exact_results, lexical_query
from hrs.search.search import Search


def chapter(text, facts=()):
    return {
        **{key: str(uuid4()) for key in ("id", "book_id", "run_id")},
        "title": "史料",
        "text": text,
        "parts": [{"span_id": "p", "start": 11, "text": text, "source": {"pages": [9]}}],
        "source_structure": {"blocks": list(facts)},
    }


def fact(text, exact, role, layer="source", status="ready", **extra):
    start = text.index(exact)
    return {
        "start": start,
        "end": start + len(exact),
        "role": role,
        "layer": layer,
        "status": status,
        **extra,
    }


def hit(source, start=0, end=None, context=()):
    return {
        **source_excerpt(source, start, len(source["text"]) if end is None else end),
        "context": list(context),
        "score": 1,
        "book_title": "原书",
        "section_path": ["史料"],
    }


def test_source_excerpt_reuse_keeps_fresh_containers_and_snapshot_identity(monkeypatch):
    from hrs.search import projection

    source = chapter("原始证据。")
    source["navigation"] = {
        "entries": [
            {
                "id": "first",
                "level": 1,
                "path": ["原始章"],
                "target": {"physical_page": 9, "start": 0},
            }
        ],
        "page_labels": [{"physical_page": 9, "printed_label": "一"}],
    }
    original, calls = projection.source_excerpt, []
    monkeypatch.setattr(
        projection, "source_excerpt", lambda *args: calls.append(args[1:]) or original(*args)
    )
    excerpt = source_excerpts()
    expected = original(source, 0, 2)
    first = excerpt(source, 0, 2)
    first["sources"][0]["start"] = -1
    first["printed_pages"][0]["label"] = "changed"
    first["toc_path"].append("changed")
    first["pages"].append(99)
    first["role"] = "caller context"
    assert excerpt(source, 0, 2) == expected and len(calls) == 1
    assert excerpt(source, 0, 2)["sources"][0]["source_record"] is source["parts"][0]["source"]
    other = deepcopy(source)
    other["title"] = "另一个来源快照"
    assert excerpt(other, 0, 2)["title"] == other["title"]
    assert len(calls) == 2
    with pytest.raises(ValueError, match="Invalid immutable chapter range"):
        excerpt(source, -1, 2)
    with pytest.raises(TypeError):
        excerpt(source, 0.0, 2.0)
    assert excerpt(source, False, 2)["start"] is False
    excerpt(source, 1, 2)
    assert excerpt(source, True, 2)["start"] is True
    with pytest.raises(TypeError):
        excerpt(source, 1.0, 2.0)
    assert excerpt(source, 0, 2) == expected
    source["navigation"]["page_labels"][0]["printed_label"] = "修订"
    assert source_excerpts()(source, 0, 2)["printed_pages"][0]["label"] == "修订"


@pytest.mark.parametrize("bound", ["entries", "range", "total"])
def test_source_excerpt_reuse_bounds_only_control_reuse(monkeypatch, bound):
    from hrs.search import projection

    source = chapter("史" * 131074)
    original, calls = projection.source_excerpt, []
    monkeypatch.setattr(
        projection, "source_excerpt", lambda *args: calls.append(args[1:]) or original(*args)
    )
    excerpt = source_excerpts()
    if bound == "entries":
        stored = [(i, i + 1) for i in range(512)]
        uncached = (512, 513)
    elif bound == "range":
        stored, uncached = [(0, 1)], (1, 16386)
    else:
        stored = [(i * 16384, (i + 1) * 16384) for i in range(8)]
        uncached = (131072, 131073)
    for start, end in stored:
        assert excerpt(source, start, end) == original(source, start, end)
    assert excerpt(source, *stored[0]) == original(source, *stored[0])
    assert len(calls) == len(stored)
    for _ in range(2):
        assert excerpt(source, *uncached) == original(source, *uncached)
    assert len(calls) == len(stored) + 2


def test_deferred_context_still_merges_a_later_overlap_beyond_the_group_limit():
    source = chapter("甲乙丙丁戊己庚。\n\n独立段落。")
    other = source["text"].index("独立")
    rows = [hit(source, 0, 4), hit(source, other), hit(source, 2, 6)]
    for row, score in zip(rows, (3, 2, 1), strict=True):
        row["score"] = score
    result = expand_hits(rows, lambda _: source, limit=1)
    assert len(result) == 1 and (result[0]["start"], result[0]["end"]) == (0, 6)
    assert result[0]["text"] == "甲乙丙丁戊己"


def test_deferred_context_budget_rejection_leaves_room_for_a_later_fallback():
    source = chapter("甲" * 20 + "\n\n短证据。")
    first, later = hit(source, 0, 20), hit(source, 22)
    first["score"], later["score"] = 2, 1
    metrics = {}
    result = expand_hits(
        [first, later], lambda _: source, limit=1, context_chars=10, total_chars=10, metrics=metrics
    )
    assert len(result) == 1 and result[0]["text"] == "短证据。"
    assert metrics["budget_rejected"] == 1


def test_deferred_context_keeps_later_identical_text_at_each_source_position():
    source = chapter("证据。\n\n另段。\n\n证据。")
    second, last = source["text"].index("另段"), source["text"].rindex("证据")
    rows = [hit(source, 0, 3), hit(source, second, second + 3), hit(source, last, last + 3)]
    for row, score in zip(rows, (3, 2, 1), strict=True):
        row["score"] = score
    result = expand_hits(rows, lambda _: source, limit=1)
    assert len(result) == 1
    assert [(r["start"], r["end"]) for r in result[0]["occurrences"]] == [(0, 3), (last, last + 3)]


@pytest.mark.parametrize("foreign", [False, True])
def test_deferred_context_preserves_out_of_limit_malformed_range_errors_and_foreign_ignores(
    foreign,
):
    source = chapter("首证。\n\n后证。")
    first, later = hit(source, 0, 3), hit(source, 5)
    first["score"], later["score"] = 2, 1
    later["context"] = [
        {
            "chapter_id": str(uuid4()) if foreign else source["id"],
            "start": len(source["text"]) + 1,
            "end": len(source["text"]) + 2,
            "role": "footnote",
        }
    ]
    if foreign:
        result = expand_hits([first, later], lambda _: source, limit=1)
        control = expand_hits([first, {**later, "context": []}], lambda _: source, limit=1)
        assert len(result) == 1 and result == control
    else:
        with pytest.raises(ValueError, match="Invalid immutable chapter range"):
            expand_hits([first, later], lambda _: source, limit=1)


@pytest.mark.parametrize("foreign", [False, True])
def test_deferred_empty_primary_still_resolves_malformed_external_context(foreign):
    text = "确认页眉"
    source = chapter(text, [fact(text, text, "header")])
    row = hit(
        source,
        context=[
            {
                "chapter_id": str(uuid4()) if foreign else source["id"],
                "start": len(text) + 1,
                "end": len(text) + 2,
                "role": "footnote",
            }
        ],
    )
    if foreign:
        assert expand_hits([row], lambda _: source, source_scope="content") == []
    else:
        with pytest.raises(ValueError, match="Invalid immutable chapter range"):
            expand_hits([row], lambda _: source, source_scope="content")


@pytest.mark.parametrize("noncontiguous", [False, True])
@pytest.mark.parametrize("short", [False, True])
def test_mandatory_metadata_holes_are_reserved_against_actual_primary_ranges(noncontiguous, short):
    text = "ANBCM" if noncontiguous else "ABNM"
    holes = [(1, 2), (4, 5)] if noncontiguous else [(2, 4)]
    bodies = [(0, 1), (2, 4)] if noncontiguous else [(0, 2)]
    source = chapter(
        text,
        [
            {"start": a, "end": b, "role": role, "layer": "source", "status": "ready"}
            for role, ranges in [("body", bodies), ("bibliography", holes)]
            for a, b in ranges
        ],
    )
    source["source_structure"]["relations"] = [
        {
            "kind": "note_of",
            "status": "ready",
            "members": [
                {"start": a, "end": b, "endpoint": "from"},
                {"start": bodies[i][0], "end": bodies[i][1], "endpoint": "to"},
            ],
        }
        for i, (a, b) in enumerate(holes)
    ]
    budget = sum(b - a for a, b in bodies) if short else len(text)
    result = expand_hits(
        [hit(source)],
        lambda _: source,
        source_scope="content",
        context_chars=budget,
        total_chars=budget,
    )
    if short:
        assert result == []
    else:
        assert len(result) == 1
        assert [(c["start"], c["end"]) for c in result[0]["context"]] == holes
        assert sum(r["end"] - r["start"] for r in result[0]["ranges"]) == budget


def test_merge_retains_raw_qualifications_inside_a_metadata_hole():
    source = chapter(
        "ABXc",
        [
            {"start": a, "end": b, "role": role, "layer": "source", "status": "ready"}
            for a, b, role in [(0, 2, "body"), (2, 3, "bibliography"), (3, 4, "body")]
        ],
    )
    first = hit(source, 0, 3, context=[{**source_excerpt(source, 2, 3), "role": "footnote"}])
    first["score"] = 2
    result = expand_hits([first, hit(source, 1, 4)], lambda _: source, source_scope="content")
    assert len(result) == 1 and (result[0]["start"], result[0]["end"]) == (0, 4)
    assert "X" in result[0]["snippet"]
    assert [(c["start"], c["end"]) for c in result[0]["context"]] == [(2, 3)]


def test_final_render_keeps_a_budget_removed_unknown_note_out_of_the_result():
    source = chapter("|地点|数量|\n|---|---|\n|甲|120|\n\n注：附近范围未知。")
    row = next(c for c in retrieval_chunks(source, "原书") if c["kind"] == "table")
    row["score"] = 1
    budget = row["end"] - row["start"]
    tight = expand_hits(
        [row], lambda _: source, source_scope="content", context_chars=budget, total_chars=budget
    )
    assert tight and "附近范围未知" not in tight[0]["snippet"] and tight[0]["context_truncated"]
    full = expand_hits([row], lambda _: source, source_scope="content")
    assert full and "附近范围未知" in full[0]["snippet"] and "归属未定" in full[0]["snippet"]


def test_multiple_overlap_merge_does_not_reserve_context_already_inside_merged_primary():
    source = chapter("nABCOO")
    source["source_structure"]["relations"] = [
        {
            "kind": "note_of",
            "status": "ready",
            "members": [
                {"start": 0, "end": 1, "endpoint": "from"},
                {"start": 4, "end": 6, "endpoint": "to"},
            ],
        }
    ]
    rows = [hit(source, a, b) for a, b in [(0, 2), (4, 6), (1, 5)]]
    for row, score in zip(rows, (3, 2, 1), strict=True):
        row["score"] = score
    result = expand_hits(
        rows, lambda _: source, source_scope="content", context_chars=6, total_chars=6
    )
    assert len(result) == 1 and result[0]["text"] == source["text"]
    assert (
        "".join(s["text"] for s in result[0]["snippet_segments"] if not s["synthetic"])
        == source["text"]
    )
    assert result[0]["context"] == []
    assert sum(r["end"] - r["start"] for r in result[0]["ranges"]) == 6


def test_ready_furniture_is_model_excluded_but_raw_lexical_keeps_the_original():
    text = "# 真标题\n\n实质正文。\n\n# 采集站点\n\n后续正文120。\n\n7"
    source = chapter(text, [fact(text, "# 采集站点", "header"), fact(text, "7", "page_number")])
    chunks = list(indexable_chunks(source, "原书"))
    body = next(c for c in chunks if "后续正文" in c["text"])
    assert body["section_path"] == ["史料", "真标题"]
    assert all("采集站点" not in c["retrieval_text"] for c in chunks)
    furniture = next(c for c in chunks if "采集站点" in c["raw_text"])
    assert furniture["model_eligible"] is False and furniture["source_scope"] == "furniture"
    raw = project_hit(source, furniture, purpose="lexical", source_scope="all")
    assert "采集站点" in raw["snippet"] and raw["text"] == furniture["text"]


def test_partial_heading_furniture_does_not_reappear_through_the_section_title():
    text = "# 页眉 实际标题\n\n正文内容。"
    source = chapter(text, [fact(text, "页眉", "header")])
    chunks = list(retrieval_chunks(source, "原书"))
    assert any("实际标题" in c["retrieval_text"] for c in chunks)
    assert all("页眉" not in c["retrieval_text"] for c in chunks)


def test_prepared_value_caches_do_not_mutate_chapters_or_reuse_changed_facts():
    source = chapter("页眉。正文。")
    plan = prepare_projection(source)
    first = project_hit(source, hit(source), plan=plan)
    first["ranges"][0]["role"] = "changed-by-caller"
    first["snippet_segments"][0]["text"] = "changed-by-caller"
    assert project_hit(source, hit(source))["snippet"] == source["text"]
    assert source["source_structure"] == {"blocks": []}
    source["source_structure"]["blocks"].append(fact(source["text"], "页眉。", "header"))
    assert prepare_projection(source) is not plan
    assert "页眉" not in project_hit(source, hit(source))["snippet"]
    assert "页眉" in project_hit(source, hit(source), plan=plan)["snippet"]
    with pytest.raises(TypeError):
        plan["ranges"][0]["role"] = "changed-by-caller"


def test_rerank_rebuilds_stale_index_paths_from_current_source_facts():
    source = chapter("# 页眉\n\n正文内容。")
    old = next(retrieval_chunks(source, "原书"))
    assert "页眉" in old["section_path"]
    source["source_structure"]["blocks"] = [fact(source["text"], "# 页眉\n\n", "header")]
    refreshed = project_hit(source, old, purpose="rerank", source_scope="content")
    assert "页眉" not in refreshed["retrieval_text"]
    assert refreshed["section_path"] == [source["title"]]


def test_unknown_numbers_repeated_text_and_source_annotations_are_never_guessed_away():
    text = "7\n\n相同文字。\n\n相同文字。\n\n不包括乙地。\n\n藏书水印。\n"
    source = chapter(
        text,
        [
            fact(text, "7", "page_number", status="unknown"),
            fact(text, "不包括乙地。", "note", "annotation"),
            fact(text, "藏书水印。", "watermark", "source"),
        ],
    )
    item = project_hit(source, hit(source), source_scope="content")
    assert item["snippet"].count("相同文字。") == 2
    assert all(word in item["snippet"] for word in ["7", "不包括乙地", "藏书水印"])
    assert any(r["layer"] == "annotation" for r in item["ranges"])


def test_source_scope_separates_catalogue_and_imprint_from_substantive_prefaces_and_notes():
    text = "目录条目。版权出版。序言实质。附录证据。译者不赞成。作者补注。图注120吨。"
    source = chapter(
        text,
        [
            fact(text, "目录条目。", "toc"),
            fact(text, "版权出版。", "frontmatter", subkind="copyright"),
            fact(text, "序言实质。", "frontmatter", subkind="preface"),
            fact(text, "附录证据。", "frontmatter", subkind="appendix"),
            fact(text, "译者不赞成。", "note", "translator_note"),
            fact(text, "作者补注。", "note", "author_note"),
            fact(text, "图注120吨。", "caption"),
        ],
    )
    content = project_hit(source, hit(source), source_scope="content")
    metadata = project_hit(source, hit(source), source_scope="metadata")
    assert all(
        word in content["snippet"]
        for word in ["序言实质", "附录证据", "译者不赞成", "作者补注", "图注120吨"]
    )
    assert "版权出版" not in content["snippet"] and "目录条目" not in content["snippet"]
    assert "目录条目" in metadata["snippet"] and "版权出版" in metadata["snippet"]
    assert "译者不赞成" not in metadata["snippet"]


def test_scope_filtered_primary_does_not_seed_relations_owned_by_excluded_body():
    text = "版权元数据。正文120吨。附注：不包括乙地。"
    body, note = text.index("正文"), text.index("附注")
    source = chapter(
        text,
        [
            fact(text, text[:body], "frontmatter", subkind="copyright"),
            fact(text, text[body:note], "body"),
            fact(text, text[note:], "note"),
        ],
    )
    source["source_structure"]["relations"] = [
        {
            "kind": "note_of",
            "status": "ready",
            "members": [
                {"start": note, "end": len(text), "endpoint": "from"},
                {"start": body, "end": note, "endpoint": "to"},
            ],
        }
    ]
    old_context = [dict(source_excerpt(source, note, len(text)), role="footnote")]
    metadata = project_hit(source, hit(source, context=old_context), source_scope="metadata")
    assert "版权元数据" in metadata["snippet"]
    assert "正文120吨" not in metadata["snippet"] and "不包括乙地" not in metadata["snippet"]


def test_snippet_maps_real_unicode_ranges_and_marks_layout_and_joiners_as_synthetic():
    text = "# 标题\n\n<table><tr><td>𠮷地&amp;甲</td><td>120吨</td></tr></table>\n![部署图](C:/private/secret.png)"
    source = chapter(text)
    item = project_hit(source, hit(source))
    assert "secret.png" not in item["snippet"] and "部署图" in item["snippet"]
    assert "𠮷地&甲" in item["snippet"] and "<td>" not in item["snippet"]
    for segment in item["snippet_segments"]:
        assert item["snippet"][segment["snippet_start"] : segment["snippet_end"]] == segment["text"]
        if segment["synthetic"]:
            assert segment["start"] is None and segment["end"] is None
        elif not segment.get("transformation"):
            assert text[segment["start"] : segment["end"]] == segment["text"]
        else:
            assert text[segment["start"] : segment["end"]] == "&amp;"
    assert any(s["synthetic"] for s in item["snippet_segments"])
    assert item["text"] == text and item["sources"][0]["start"] == 11


def test_context_is_deduplicated_by_canonical_range_including_overlapping_roles():
    source = chapter("甲乙丙丁戊己庚辛壬癸")
    extras = [
        dict(source_excerpt(source, a, b), role=role)
        for a, b, role in [(1, 6, "footnote"), (4, 8, "table_note"), (6, 10, "continuation")]
    ]
    item = project_hit(source, hit(source, 0, 3, extras))
    covered = [i for r in item["ranges"] for i in range(r["start"], r["end"])]
    assert sorted(covered) == list(range(10))
    assert (
        "".join(s["text"] for s in item["snippet_segments"] if not s["synthetic"]) == source["text"]
    )
    deferred = project_hit(source, hit(source, 0, 3, extras), include_view=False)
    completed = project_hit(source, deferred)
    assert completed["snippet"] == item["snippet"]
    assert completed["context"] == item["context"]


def test_bounded_directory_expansion_and_rerank_views_do_not_reintroduce_noise():
    text = "甲地" * 80 + "采集水印" + "乙地" * 80
    source = chapter(text, [fact(text, "采集水印", "watermark", "capture")])
    original = hit(source)
    original.update(kind="paragraph", retrieval_text=text)
    tokenizer = SimpleNamespace(encode=lambda value: SimpleNamespace(ids=list(value)))
    bounded = list(bounded_chunks(source, [original], tokenizer, limit=80))
    assert len(bounded) > 1 and all(len(c["retrieval_text"]) <= 80 for c in bounded)
    assert all("采集水印" not in c["retrieval_text"] for c in bounded)
    scope = DirectoryScope({source["id"]: (1, len(text) - 1)})
    clipped = scope.clip_candidates([original], lambda _: source)
    expanded = expand_hits(clipped, lambda _: source, source_scope="content")
    assert expanded and "采集水印" not in expanded[0]["snippet"]
    rerank = project_hit(source, expanded[0], purpose="rerank", source_scope="content")
    assert "采集水印" not in rerank["retrieval_text"]


def test_visible_windows_never_split_hidden_image_destinations_into_model_text():
    text = "甲地" * 30 + "![部署图](C:/private/" + "secret" * 100 + ".png)" + "乙地" * 40
    source = chapter(text)
    original = hit(source)
    original.update(kind="paragraph", retrieval_text=text)
    tokenizer = SimpleNamespace(encode=lambda value: SimpleNamespace(ids=list(value)))
    bounded = list(bounded_chunks(source, [original], tokenizer, limit=60))
    assert len(bounded) > 1
    assert all(
        "secret" not in c["retrieval_text"] and "private" not in c["retrieval_text"]
        for c in bounded
    )
    assert set().union(*(set(range(c["start"], c["end"])) for c in bounded)) == set(
        range(len(text))
    )


@pytest.mark.parametrize("status,linked", [("ready", True), ("unknown", False)])
def test_only_reviewed_reading_order_can_join_columns_and_cross_page_fragments(status, linked):
    text = "# 第二栏\n\n后半句。\n\n# 第一栏\n\n先半句。"
    source = chapter(text)
    first, second = text.index("先半句"), text.index("后半句")
    source["source_structure"]["relations"] = [
        {
            "kind": "continues",
            "status": status,
            "members": [{"start": first, "end": len(text)}, {"start": second, "end": second + 4}],
        }
    ]
    chunks = list(retrieval_chunks(source, "原书"))
    item = next(c for c in chunks if "后半句" in c["text"])
    assert ("先半句" in item["snippet"]) is linked
    if linked:
        assert item["snippet"].index("先半句") < item["snippet"].index("后半句")


def test_exact_source_groups_keep_versions_without_adding_duplicate_scores():
    a, b, near = chapter("王某率100人。"), chapter("王某率100人。"), chapter("王某率101人。")
    rows = [hit(a), dict(hit(b), score=3), dict(hit(near), score=2)]
    grouped = group_exact_results(rows)
    assert len(grouped) == 2 and grouped[0]["score"] == 3
    assert {o["book_id"] for o in grouped[0]["occurrences"]} == {a["book_id"], b["book_id"]}
    assert grouped[1]["text"] == near["text"]
    query = lexical_query("臺灣王某", [], "台湾王某")
    fields = query["bool"]["must"][0]["bool"]["should"]
    assert fields[0]["multi_match"]["query"] == "臺灣王某"
    assert fields[1]["multi_match"]["fields"] == ["title_aliases^0.35", "text_aliases^0.2"]


def test_chained_reviewed_column_order_is_applied_inside_one_canonical_envelope():
    source = chapter("丙段。乙段。甲段。")
    source["source_structure"]["relations"] = [
        {
            "kind": "reading_next",
            "status": "ready",
            "members": [{"start": 6, "end": 9}, {"start": 3, "end": 6}],
        },
        {
            "kind": "reading_next",
            "status": "ready",
            "members": [{"start": 3, "end": 6}, {"start": 0, "end": 3}],
        },
    ]
    item = project_hit(source, hit(source))
    assert item["snippet"] == "甲段。\n乙段。\n丙段。"
    assert item["text"] == "丙段。乙段。甲段。"


def test_duplicate_sources_share_one_primary_budget_and_keep_a_distinct_result_slot():
    a, b, other = chapter("同一原文。"), chapter("同一原文。"), chapter("不同原文。")
    rows = [dict(hit(c), score=3 - i) for i, c in enumerate((a, b, other))]
    budget = len(a["text"]) + len(other["text"])
    result = expand_hits(
        rows,
        {c["id"]: c for c in (a, b, other)}.__getitem__,
        limit=2,
        total_chars=budget,
        source_scope="content",
    )
    assert len(result) == 2 and len(result[0]["occurrences"]) == 2
    assert result[0]["score"] == 3
    assert sum(len(c["text"]) for c in result) == budget


def test_ready_split_note_ownership_survives_directory_clipping_without_marker_heuristics():
    text = "正文登记120吨。\n\n旁节正文。\n\n译者附注：\n不包括乙地。"
    owner_end, note = text.index("\n\n"), text.index("译者")
    source = chapter(
        text,
        [
            fact(text, "正文登记120吨。", "body"),
            fact(text, "译者附注：\n不包括乙地。", "note", "translator_note"),
        ],
    )
    source["source_structure"]["relations"] = [
        {
            "kind": "note_of",
            "status": "ready",
            "members": [
                {"start": note, "end": note + 6, "endpoint": "from"},
                {"start": note + 6, "end": len(text), "endpoint": "from"},
                {"start": 0, "end": owner_end, "endpoint": "to"},
            ],
        }
    ]
    item = hit(source, 0, len(text))
    scope = DirectoryScope({source["id"]: (0, owner_end)})
    result = scope.clip_results([item], lambda _: source)[0]
    assert "不包括乙地" in result["snippet"]
    assert all(c["role"] == "footnote" and c["outside_scope"] for c in result["context"])
    assert all(c["layer"] == "translator_note" for c in result["context"])


@pytest.mark.parametrize("canonical_note", [False, True])
def test_table_caption_note_closure_is_bounded_and_keeps_the_note_qualification(canonical_note):
    from hrs.library.footnotes import resolve_footnotes

    text = "表题[^a]。\n\n<table><tr><td>120吨</td></tr></table>\n\n[^a]: 不包括乙地。\n"
    table_start, table_end = text.index("<table>"), text.index("</table>") + len("</table>")
    note_start, caption_end = text.index("[^a]:"), text.index("\n\n")
    source = chapter(
        text,
        [
            fact(text, text[:caption_end], "caption", id="caption"),
            fact(text, text[table_start:table_end], "table", id="table"),
            fact(text, text[note_start:], "note", "author_note", id="note"),
        ],
    )
    source["source_structure"]["relations"] = [
        {
            "kind": "caption_of",
            "status": "ready",
            "members": [
                {
                    "start": table_start,
                    "end": table_end,
                    "endpoint": "from",
                    "fragment_id": "table",
                },
                {"start": 0, "end": caption_end, "endpoint": "to", "fragment_id": "caption"},
            ],
        },
    ]
    if canonical_note:
        source["footnotes"] = resolve_footnotes(text, source["parts"])
    else:
        source["source_structure"]["relations"].append(
            {
                "kind": "note_of",
                "status": "ready",
                "members": [
                    {
                        "start": note_start,
                        "end": len(text),
                        "endpoint": "from",
                        "fragment_id": "note",
                    },
                    {"start": 0, "end": caption_end, "endpoint": "to", "fragment_id": "caption"},
                ],
            }
        )
    original = hit(source, table_start, table_end)
    original["kind"] = "html_table"
    item = project_hit(source, original, source_scope="content")
    assert "表题" in item["snippet"] and "不包括乙地" in item["snippet"]
    assert any(c["role"] == "figure_caption" for c in item["context"])
    assert any(c["role"] == "footnote" for c in item["context"])
    covered = [i for r in item["ranges"] for i in range(r["start"], r["end"])]
    assert len(covered) == len(set(covered))
    assert len(item["context"]) <= 3
    assert (
        expand_hits(
            [original],
            lambda _: source,
            source_scope="content",
            total_chars=table_end - table_start,
        )
        == []
    )


def test_reading_next_orders_evidence_without_expanding_unrelated_body_or_rejecting_budget():
    text = "表题。<table>120吨</table>注：仅甲地。" + "独立正文。" * 1600
    table_start, table_end = text.index("<table>"), text.index("</table>") + len("</table>")
    note_start, body_start = text.index("注："), text.index("独立正文")
    source = chapter(text)
    source["source_structure"]["relations"] = [
        {
            "kind": "caption_of",
            "status": "ready",
            "members": [
                {"start": 0, "end": table_start, "endpoint": "from"},
                {"start": table_start, "end": table_end, "endpoint": "to"},
            ],
        },
        {
            "kind": "note_of",
            "status": "ready",
            "members": [
                {"start": note_start, "end": body_start, "endpoint": "from"},
                {"start": table_start, "end": table_end, "endpoint": "to"},
            ],
        },
        {
            "kind": "reading_next",
            "status": "ready",
            "members": [
                {"start": note_start, "end": body_start, "endpoint": "from"},
                {"start": body_start, "end": len(text), "endpoint": "to"},
            ],
        },
    ]
    original = {**hit(source, table_start, table_end), "kind": "html_table"}
    projected = project_hit(source, original)
    assert "仅甲地" in projected["snippet"] and "表题" in projected["snippet"]
    assert "独立正文" not in projected["snippet"]
    result = expand_hits([original], lambda _: source, source_scope="content")
    assert len(result) == 1 and "独立正文" not in result[0]["snippet"]


@pytest.mark.parametrize("kind,role", [("note_of", "note"), ("caption_of", "caption")])
def test_auxiliary_shared_qualification_never_bridges_to_another_owner(kind, role):
    text = "甲地登记120吨。共享限定。" + "独立议题。" * 1600
    shared, other = text.index("共享限定"), text.index("独立议题")
    source = chapter(
        text,
        [
            fact(text, "共享限定。", role, id="shared"),
            fact(text, text[:shared], "table", id="first"),
            fact(text, text[other:], "figure", id="other"),
        ],
    )
    source["source_structure"]["relations"] = [
        {
            "kind": kind,
            "status": "ready",
            "members": [
                {"start": shared, "end": other, "endpoint": "from", "fragment_id": "shared"},
                {"start": a, "end": b, "endpoint": "to", "fragment_id": identity},
            ],
        }
        for a, b, identity in [(0, shared, "first"), (other, len(text), "other")]
    ]
    owner = project_hit(source, hit(source, 0, shared))
    assert "共享限定" in owner["snippet"] and "独立议题" not in owner["snippet"]
    assert len(expand_hits([hit(source, 0, shared)], lambda _: source, source_scope="content")) == 1
    primary_qualification = project_hit(source, hit(source, shared, other))
    assert (
        "甲地登记120吨" in primary_qualification["snippet"]
        and "独立议题" in primary_qualification["snippet"]
    )


@pytest.mark.parametrize("continuation", [None, "unknown", "ready"])
def test_later_table_inherits_known_note_only_through_reviewed_table_continuation(continuation):
    first = "| 地点 | 数量（吨） |\n| --- | --- |\n| 甲地 | 100 |\n\n"
    note = "注：不包括乙地。\n\n"
    second = "| 地点 | 数量（吨） |\n| --- | --- |\n| 丙地 | 120 |\n"
    text, note_start, second_start = first + note + second, len(first), len(first + note)
    source = chapter(text)
    source["source_structure"]["relations"] = [
        {
            "kind": "note_of",
            "status": "ready",
            "members": [
                {
                    "start": note_start,
                    "end": second_start,
                    "endpoint": "from",
                    "fragment_id": "note",
                },
                {"start": 0, "end": note_start, "endpoint": "to", "fragment_id": "first"},
            ],
        }
    ]
    if continuation:
        source["source_structure"]["relations"].append(
            {
                "kind": "table_continues",
                "status": continuation,
                "members": [
                    {"start": 0, "end": note_start, "endpoint": "from", "fragment_id": "first"},
                    {
                        "start": second_start,
                        "end": len(text),
                        "endpoint": "to",
                        "fragment_id": "second",
                    },
                ],
            }
        )
    later = next(c for c in retrieval_chunks(source, "原书") if "丙地" in c["text"])
    assert ("不包括乙地" in later["snippet"]) is (continuation == "ready")
    assert "甲地 | 100" not in later["snippet"]
    direct = project_hit(source, {**hit(source, second_start), "kind": "table"})
    assert ("不包括乙地" in direct["snippet"]) is (continuation == "ready")
    assert "甲地 | 100" not in direct["snippet"]


def test_unknown_nearby_table_note_remains_explicitly_unassigned_source():
    source = chapter("| 地点 | 数量 |\n| --- | --- |\n| 甲 | 120 |\n\n注：不包括乙地。")
    table = next(c for c in retrieval_chunks(source, "原书") if c["kind"] == "table")
    assert "不包括乙地" in table["snippet"] and "归属未定" in table["snippet"]
    assert any(
        c["role"] == "unassigned_context" and c["ownership_status"] == "unknown"
        for c in table["context"]
    )
    assert "归属未定" in table["retrieval_text"]
    assert "归属未定" not in lexical_text(table) and "附近原文" not in lexical_text(table)
    assert "不包括乙地" in lexical_text(table) and "原书" in lexical_text(table)
    expanded = expand_hits([{**table, "score": 1}], lambda _: source, source_scope="content")
    assert any(
        c["ownership_status"] == "unknown" and c["context_hint"] == "table_note"
        for c in expanded[0]["context"]
    )
    small = expand_hits(
        [{**table, "score": 1}],
        lambda _: source,
        source_scope="content",
        total_chars=len(table["text"]),
    )
    assert len(small) == 1 and not small[0]["context"] and small[0]["context_truncated"]


def test_all_original_search_is_lexical_and_maps_each_occurrence_to_its_own_source(monkeypatch):
    source = chapter(
        "# 采集站点",
        [{"start": 0, "end": 6, "role": "header", "layer": "capture", "status": "ready"}],
    )
    item = next(indexable_chunks(source, "原书"))
    search = object.__new__(Search)
    search.settings = SimpleNamespace(opensearch_index="synthetic")
    queries = []
    search.client = SimpleNamespace(
        indices=SimpleNamespace(exists=lambda **_: True),
        search=lambda **kw: (
            queries.append(kw["body"])
            or {"hits": {"hits": [{"_id": item["id"], "_score": 2, "_source": item}]}}
        ),
    )
    search.active_scope = lambda _: []
    search.library = SimpleNamespace(
        source_chapter=lambda _: source, toc_links=lambda chapter: chapter.get("toc_links", [])
    )
    result = search.search("采集站点", source_scope="all", semantic=True)
    assert result and "采集站点" in result[0]["snippet"]
    assert result[0]["reading_targets"] and result[0]["occurrences"] == []
    assert "raw_text_search" in str(queries[0]) and len(queries) == 1
    assert source_ranges(source, 0, 6, source_scope="content") == []


def test_repeated_reading_targets_remain_independent_result_values():
    source = chapter("正文证据。")
    search = object.__new__(Search)
    search.library = SimpleNamespace(
        source_chapter=lambda _: source, toc_links=lambda chapter: chapter.get("toc_links", [])
    )
    first, second = search.reading_results([hit(source), hit(source)])
    original = second["reading_target"]["anchor"]
    first["reading_target"]["anchor"] = "changed by caller"
    assert second["reading_target"]["anchor"] == original
    assert first["reading_targets"][0]["anchor"] == original


def test_search_reuses_source_envelopes_within_each_current_request(monkeypatch):
    from hrs.search import projection

    source = chapter("同章重叠的正文证据。")
    source["navigation"] = {"version": 1}
    source["parts"][0]["source"]["image_sha256"] = "a" * 64
    rows = [hit(source, 0, 5), hit(source, 3, len(source["text"]))]
    search = object.__new__(Search)
    search.settings = SimpleNamespace(opensearch_index="test")
    search.client = SimpleNamespace(
        indices=SimpleNamespace(exists=lambda **_: True),
        search=lambda **_: {
            "hits": {
                "hits": [{"_id": row["id"], "_score": 1, "_source": deepcopy(row)} for row in rows]
            }
        },
    )
    loads, calls = [], []
    search.library = SimpleNamespace(
        source_chapter=lambda identity: loads.append(identity) or deepcopy(source),
        toc_links=lambda _: [],
    )
    search.active_scope = lambda _: []
    original = projection.source_excerpt
    monkeypatch.setattr(
        projection, "source_excerpt", lambda *args: calls.append(args) or original(*args)
    )
    first = search.search("正文证据", semantic=False)[0]
    assert first["navigation_version"] == 1
    source["navigation"]["version"] = 2
    source["parts"][0]["source"]["image_sha256"] = "b" * 64
    second = search.search("正文证据", semantic=False)[0]
    assert second["navigation_version"] == 2
    assert second["reading_target"]["image_sha256"] == "b" * 64
    assert len(loads) == 2
    full = [call for call in calls if call[1:] == (0, len(source["text"]))]
    assert len(full) == 2 and full[0][0] is not full[1][0]


def test_visible_cleaner_preserves_source_angle_brackets_and_does_not_normalize_names():
    segments = visible_segments("*臺灣* <王某> &amp; 𠮷")
    assert "".join(s["text"] for s in segments) == "臺灣 <王某> & 𠮷"
    source = chapter('&lt;name&gt; <img src="private.png" alt="部署图">')
    item = project_hit(source, hit(source))
    assert "<name>" in lexical_text(item) and "部署图" in item["snippet"]
    assert "private.png" not in item["snippet"]
    sliced = slice_segments(item["snippet_segments"], 1, 8)
    assert sliced[0]["snippet_start"] == 0
    assert sliced[-1]["snippet_end"] == len("".join(s["text"] for s in sliced))
    literal = "120 * 5 * 4，不是2*3*4。"
    assert "".join(s["text"] for s in visible_segments(literal)) == literal


@pytest.mark.parametrize(
    "literal",
    [
        "Synthetic finding <source>",
        "数值 <120且120>100。",
        "原文<document>甲</document>。",
        "`<table>literal</table>`",
        "```html\n<table>literal</table>\n```",
    ],
)
def test_unknown_angle_tags_and_code_literals_remain_source_text(literal):
    visible = "".join(s["text"] for s in visible_segments(literal))
    expected = literal.replace("```html\n", "").replace("```", "").strip("`")
    assert expected in visible
