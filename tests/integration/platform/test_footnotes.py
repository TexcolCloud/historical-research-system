from hrs.library.footnotes import export_footnotes, resolve_footnotes


def test_export_scopes_repeated_page_note_numbers_and_keeps_continuations():
    pages = ["One[^1]\n\n[^1]: First\ncontinued.\n\n", "Two[^1]\n\n[^1]: Second.\n\n"]
    text = "".join(pages)
    notes = resolve_footnotes(
        text, [{"text": value, "source": {"pages": [i + 1]}} for i, value in enumerate(pages)]
    )
    assert len(notes) == 2
    exported = export_footnotes(text, notes, "c1")
    assert exported.count("[^c1-1]") == 2 and exported.count("[^c1-2]") == 2
    assert "\n    continued." in exported and "[^1]" not in exported
    assert text == "".join(pages)


def source_parts(*pages):
    return [{"text": text, "source": {"pages": [number]}} for number, text in enumerate(pages, 1)]


def test_repeated_circled_numbers_link_only_within_their_original_page():
    parts = source_parts("甲地记载①。\n\n① 不包括乙地。\n\n", "丙地记载①。\n\n① 仅计本年。\n")
    text = "".join(p["text"] for p in parts)
    links = resolve_footnotes(text, parts)
    assert len(links) == 2
    for link, expected in zip(links, ["不包括乙地", "仅计本年"], strict=True):
        note = link["note"]
        assert expected in text[note["start"] : note["end"]]
        assert len(link["references"]) == 1
        assert text[link["references"][0]["start"] : link["references"][0]["end"]] == "①"


def test_markdown_endnote_and_multiple_backlinks_keep_original_ranges():
    text = "第一次[^a]。再次[^a]。\n\n[^a]: 实际注释。\n    第二行注释。\n\n后续正文。"
    links = resolve_footnotes(text, source_parts(text))
    assert len(links) == 1 and len(links[0]["references"]) == 2
    note = links[0]["note"]
    assert "第二行注释" in text[note["start"] : note["end"]]
    assert "后续正文" not in text[note["start"] : note["end"]]


def test_unpaired_ambiguous_markers_and_code_are_not_guessed():
    assert (
        resolve_footnotes(
            "甲①。\n\n① 注释甲。\n\n① 注释乙。", source_parts("甲①。\n\n① 注释甲。\n\n① 注释乙。")
        )
        == []
    )
    text = "`示例[^a]`\n\n```md\n伪正文[^a]\n```\n\n[^a]: 没有正文注号。"
    assert resolve_footnotes(text, source_parts(text)) == []
    parts = source_parts("正文①。\n", "① 下一页的注，不猜归属。")
    assert resolve_footnotes("".join(p["text"] for p in parts), parts) == []


def test_table_reference_can_point_to_its_translator_note():
    text = "<table><tr><td>某人①</td></tr></table>\n\n① 此人为代理职务。——译者\n\n普通正文。"
    links = resolve_footnotes(text, source_parts(text))
    assert len(links) == 1
    assert links[0]["references"][0]["start"] == text.index("①")
    assert "普通正文" not in text[links[0]["note"]["start"] : links[0]["note"]["end"]]


def test_separate_translator_signature_stays_with_note_not_next_body():
    text = "正文①。\n\n① 注释说明。\n\n——译者\n\n后续正文。"
    link = resolve_footnotes(text, source_parts(text))[0]
    value = text[link["note"]["start"] : link["note"]["end"]]
    assert "——译者" in value and "后续正文" not in value


def test_retrieval_uses_same_page_scoping_for_printed_notes():
    from uuid import uuid4

    from hrs.search.chunks import retrieval_chunks

    pages = ["甲地记录①。\n\n① 不包括乙地。\n\n", "丙地记录①。\n\n① 仅计本年。\n"]
    parts = [{**p, "span_id": str(i), "start": 0} for i, p in enumerate(source_parts(*pages))]
    chapter = {
        "id": str(uuid4()),
        "book_id": str(uuid4()),
        "run_id": str(uuid4()),
        "title": "正文",
        "text": "".join(pages),
        "parts": parts,
    }
    hits = list(retrieval_chunks(chapter, "合成样本"))
    first = next(h for h in hits if "甲地记录" in h["text"])
    assert any(c["role"] == "footnote" and "不包括乙地" in c["text"] for c in first["context"])
    assert all("仅计本年" not in c["text"] for c in first["context"])
    note = next(h for h in hits if h["kind"] == "note" and "仅计本年" in h["text"])
    assert any(c["role"] == "note_owner" and "丙地记录" in c["text"] for c in note["context"])


def test_inline_parenthesized_markers_link_unique_explicit_cross_page_notes():
    first = "（1）这是条目开头，不是脚注。\n\n叶管部（1）与韩冷〔2〕会合(3)。\n\n"
    second = "[^1]: 部队说明。\n\n[^2]: 人物说明。\n\n[^3]: 地点说明。"
    text = first + second
    parts = [{"text": first, "source": {"pages": [1]}}, {"text": second, "source": {"pages": [2]}}]
    links = resolve_footnotes(text, parts)
    assert len(links) == 3 and all(len(n["references"]) == 1 for n in links)
    assert all(r["start"] > 0 for n in links for r in n["references"])
    duplicate = "\n\n[^1]: 另一项同号说明。"
    assert (
        len(
            resolve_footnotes(
                text + duplicate, parts + [{"text": duplicate, "source": {"pages": [3]}}]
            )
        )
        == 2
    )
