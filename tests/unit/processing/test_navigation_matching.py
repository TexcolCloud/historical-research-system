from copy import deepcopy

from hrs.processing.navigation import resolve_navigation, target_evidence, validate_proposal
from hrs.processing.navigation_matching import target_catalog
from hrs.processing.navigation_verification import verification_key
from tests.unit.processing.test_navigation import page


def document(rows, pages):
    source = "目录\n" + "\n".join(row["source_anchor"] for row in rows)
    doc = {"source": {"sha256": "a" * 64}, "pages": [page(1, source, "toc"), *pages]}
    doc["navigation"] = {
        "source_entries": validate_proposal(doc, [{"source_page": 1, **row} for row in rows])
    }
    return doc


def entry(title, label, context=""):
    return {"title": title, "target_label": label, "source_anchor": f"{title}{context} {label}"}


def test_layout_only_title_difference_uses_literal_target_evidence():
    doc = document(
        [entry("关于甲、乙工作的决定", "1")],
        [page(2, "# 关于甲乙工作的决定\n正文\n1", "text", "1", ["关于甲乙工作的决定"])],
    )
    result = resolve_navigation(doc)["entries"][0]
    assert result["status"] == "matched"
    assert result["target"]["anchor"] == "关于甲乙工作的决定"
    assert "标题仅标点或空白不同" in result["candidates"][0]["match_signals"]
    assert doc["pages"][1]["approved"]["markdown"].startswith("# 关于甲乙")


def test_observed_wrapped_title_binds_to_the_literal_multiline_source():
    title = "关于甲乙工作的决定"
    doc = document(
        [entry(title, "1")],
        [page(2, "# 关于甲乙\n工作的决定\n1", "text", "1", [title])],
    )
    result = resolve_navigation(doc)["entries"][0]
    assert result["status"] == "matched"
    assert result["target"]["anchor"] == "关于甲乙\n工作的决定"


def test_heading_variants_at_the_same_start_keep_their_opening_date():
    title = "关于甲乙工作的决定"
    doc = document(
        [entry(title, "1")],
        [page(2, "# 关于甲乙\n工作的决定\n（一九四五年九月十日）\n1", "text", "1", [title])],
    )
    assert all(item["dates"] == {(1945, 9, 10)} for item in target_catalog(doc))


def test_escaped_note_marker_does_not_make_one_heading_two_targets():
    title = "高岗关于统战工作致张德生、汪锋电"
    doc = document(
        [entry(title, "1")],
        [page(2, f"# {title}\\*\n\n正文\n\n1", "text", "1", [title])],
    )
    result = resolve_navigation(doc)["entries"][0]
    assert result["status"] == "matched"
    assert len(result["candidates"]) == 1
    assert result["target"]["anchor"] == title


def test_bookmark_short_title_alias_merges_only_at_the_same_article_start():
    title = "关于干部工作的通知"
    full = title + "（附工作安排）"
    text = f"# {full}\n\n正文\n\n# 关于干部工作的补充通知\n\n另一篇正文\n\n1"
    doc = document(
        [entry(title, "1")],
        [page(2, text, "text", "1", [title, full, "关于干部工作的补充通知"])],
    )
    source = doc["navigation"]["source_entries"][0]
    doc["navigation"]["supplemental_candidates"] = {
        source["id"]: [
            {
                **target_evidence(doc["pages"][1], anchor),
                "source_sha256": source["source_sha256"],
                "match_method": "bookmark",
                "match_score": 0.9,
            }
            for anchor in (full, "关于干部工作的补充通知")
        ]
    }
    result = resolve_navigation(doc)["entries"][0]
    assert result["status"] == "matched"
    assert len(result["candidates"]) == 2
    assert result["target"]["anchor"] == title
    assert result["candidates"][0]["ownership_start"] != result["candidates"][1]["ownership_start"]
    assert any("书签" in signal for signal in result["candidates"][0]["match_signals"])


def test_missing_footer_needs_an_independent_exact_date_to_auto_match():
    doc = document(
        [entry("关于教育工作的决定", "1", "（一九四五年九月十日）")],
        [
            page(
                2,
                "# 关于教育工作的决定\n（一九四五年九月十日）\n正文",
                "text",
                headings=["关于教育工作的决定"],
            )
        ],
    )
    result = resolve_navigation(doc)["entries"][0]
    assert result["status"] == "matched"
    assert result["target"]["physical_page"] == 2
    assert "日期一致" in result["candidates"][0]["match_signals"]
    no_date = document(
        [entry("关于教育工作的决定", "1")],
        [page(2, "# 关于教育工作的决定\n正文", "text", headings=["关于教育工作的决定"])],
    )
    unresolved = resolve_navigation(no_date)["entries"][0]
    assert unresolved["status"] == "conflict"
    assert unresolved["target"] is None
    assert unresolved["candidates"][0]["physical_page"] == 2


def test_same_title_and_repeated_page_number_are_disambiguated_by_date():
    title = "关于干部工作的通知"
    doc = document(
        [entry(title, "1", "（1945年9月10日）")],
        [
            page(2, f"# {title}\n（一九四五年九月九日）\n1", "text", "1", [title]),
            page(3, f"# {title}\n（一九四五年九月十日）\n1", "text", "1", [title]),
        ],
    )
    result = resolve_navigation(doc)["entries"][0]
    assert result["status"] == "matched"
    assert result["target"]["physical_page"] == 3
    assert result["candidates"][0]["physical_page"] == 3
    without_dates = deepcopy(doc)
    row = without_dates["navigation"]["source_entries"][0]
    row["source_anchor"] = f"{title} 1"
    assert resolve_navigation(without_dates)["entries"][0]["status"] == "conflict"


def test_page_map_stops_at_insertions_and_numbering_restarts():
    doc = document(
        [entry("甲", "1")],
        [
            page(2, "甲\n1", "text", "1", ["甲"]),
            page(3, "乙\n2", "text", "2", ["乙"]),
            page(4, "插图", "figure"),
            page(5, "丙\n3", "text", "3", ["丙"]),
            page(6, "丁\n4", "text", "4", ["丁"]),
            page(7, "附录甲\n1", "text", "1", ["附录甲"]),
            page(8, "附录乙\n2", "text", "2", ["附录乙"]),
        ],
    )
    mappings = resolve_navigation(doc)["page_mappings"]
    assert [(m["physical_start"], m["physical_end"], m["offset"]) for m in mappings] == [
        (2, 3, 1),
        (5, 6, 2),
        (7, 8, 6),
    ]
    assert all(not (m["physical_start"] <= 4 <= m["physical_end"]) for m in mappings)


def test_manual_anchor_reranks_later_entries_without_approving_them():
    doc = document(
        [entry("关于甲工作的通知", "1"), entry("关于乙工作的通知", "2")],
        [
            page(2, "关于甲工作的通知\n1", "text", "1", ["关于甲工作的通知"]),
            page(3, "关于乙工作的通知", "text", headings=["关于乙工作的通知"]),
            page(4, "其他正文", "text"),
            page(5, "关于甲工作的通知\n1", "text", "1", ["关于甲工作的通知"]),
            page(6, "关于乙工作的通知", "text", headings=["关于乙工作的通知"]),
        ],
    )
    before = resolve_navigation(doc)["entries"][1]
    assert before["candidates"][0]["physical_page"] == 3
    first = doc["navigation"]["source_entries"][0]
    doc["navigation"]["decisions"] = {
        first["id"]: {
            "source_sha256": first["source_sha256"],
            "target": target_evidence(doc["pages"][4], "关于甲工作的通知"),
            "final": True,
            "approval_type": "human_navigation",
        }
    }
    after = resolve_navigation(doc)
    assert after["entries"][0]["status"] == "matched_manual"
    second = after["entries"][1]
    assert second["candidates"][0]["physical_page"] == 6
    assert second["status"] == "conflict" and second["target"] is None
    assert "decision" not in second
    assert any("人工校准" in value for value in second["candidates"][0]["match_signals"])


def test_approximate_title_and_offset_never_grant_automatic_matching():
    doc = document(
        [entry("张某关于甲工作的意见", "2")],
        [
            page(2, "第一项\n1", "text", "1", ["第一项"]),
            page(3, "李某关于甲工作的意见\n正文", "text", headings=["李某关于甲工作的意见"]),
            page(4, "第三项\n3", "text", "3", ["第三项"]),
        ],
    )
    result = resolve_navigation(doc)["entries"][0]
    assert result["status"] == "conflict"
    assert result["target"] is None
    candidate = result["candidates"][0]
    assert candidate["physical_page"] == 3
    assert "标题近似，需核对" in candidate["match_signals"]
    assert any("页码映射" in signal for signal in candidate["match_signals"])


def test_same_page_titles_keep_distinct_character_anchors():
    text = "# 关于甲工作的通知\n正文一\n# 关于乙工作的通知\n正文二\n1"
    doc = document(
        [entry("关于甲工作的通知", "1"), entry("关于乙工作的通知", "1")],
        [page(2, text, "text", "1", ["关于甲工作的通知", "关于乙工作的通知"])],
    )
    result = resolve_navigation(doc)["entries"]
    assert all(row["status"] == "matched" for row in result)
    assert result[0]["target"]["start"] < result[1]["target"]["start"]


def test_supplemental_candidates_are_hash_bound_and_never_auto_approved():
    doc = document(
        [entry("目录中的简称", "1")],
        [page(2, "# 正文使用不同标题\n1", "text", "1", ["正文使用不同标题"])],
    )
    source = doc["navigation"]["source_entries"][0]
    target = target_evidence(doc["pages"][1], "正文使用不同标题")
    supplied = {
        **target,
        "source_sha256": source["source_sha256"],
        "match_method": "semantic",
        "match_score": 0.99,
    }
    doc["navigation"]["supplemental_candidates"] = {source["id"]: [supplied]}
    result = resolve_navigation(doc)["entries"][0]
    assert result["status"] == "conflict" and result["target"] is None
    assert result["candidates"][0]["physical_page"] == 2
    supplied["markdown_sha256"] = "0" * 64
    assert resolve_navigation(doc)["entries"][0]["candidates"] == []


def test_machine_verification_is_bound_to_current_evidence_and_human_decision_wins():
    title = "关于教育工作的决定"
    doc = document(
        [entry(title, "1")],
        [
            page(2, f"# {title}\n正文甲", "text", headings=[title]),
            page(3, f"# {title}\n正文乙", "text", headings=[title]),
        ],
    )
    row = resolve_navigation(doc)["entries"][0]
    assert row["status"] == "conflict"
    selected = row["candidates"][0]
    verification = {
        "source_sha256": row["source_sha256"],
        "verification_key": verification_key(row, row["candidates"]),
        "target": selected,
        "receipt": {"approval_type": "machine", "human_approval": False},
    }
    doc["navigation"]["link_verifications"] = {row["id"]: verification}
    result = resolve_navigation(doc)["entries"][0]
    assert result["status"] == "matched_verified"
    assert result["match_method"] == "original_review"
    assert result["verification"]["receipt"]["human_approval"] is False
    assert "decision" not in result
    doc["navigation"]["decisions"] = {
        row["id"]: {
            "source_sha256": row["source_sha256"],
            "target": target_evidence(doc["pages"][2], title),
            "final": True,
        }
    }
    manual = resolve_navigation(doc)["entries"][0]
    assert manual["status"] == "matched_manual"
    assert manual["target"]["physical_page"] == 3
    doc["navigation"]["decisions"].clear()
    verification["target"] = None
    unresolved = resolve_navigation(doc)["entries"][0]
    assert unresolved["status"] == "conflict" and unresolved["candidates"]
    verification["target"] = selected
    doc["pages"][1]["image_sha256"] = "b" * 64
    assert resolve_navigation(doc)["entries"][0]["status"] == "conflict"


def test_classified_index_does_not_propagate_body_order_assumptions():
    doc = document(
        [entry("关于甲工作的通知", "2"), entry("关于乙工作的通知", "1")],
        [
            page(2, "关于乙工作的通知", "text", headings=["关于乙工作的通知"]),
            page(3, "关于甲工作的通知\n2", "text", "2", ["关于甲工作的通知"]),
        ],
    )
    first = doc["navigation"]["source_entries"][0]
    doc["navigation"]["decisions"] = {
        first["id"]: {
            "source_sha256": first["source_sha256"],
            "target": target_evidence(doc["pages"][2], first["title"]),
            "final": True,
        }
    }
    candidate = resolve_navigation(doc)["entries"][1]["candidates"][0]
    assert not any("目录顺序" in signal for signal in candidate["match_signals"])


def test_directory_context_ranks_openings_without_granting_approval():
    title = "关于干部工作的通知"
    doc = document(
        [entry(title, "1", "（西北局）")],
        [
            page(2, f"# {title}\n华北局\n1", "text", "1", [title]),
            page(3, f"# {title}\n西北局\n1", "text", "1", [title]),
        ],
    )
    result = resolve_navigation(doc)["entries"][0]
    assert result["status"] == "conflict"
    assert result["candidates"][0]["physical_page"] == 3
    assert any("西北局" in signal for signal in result["candidates"][0]["match_signals"])
