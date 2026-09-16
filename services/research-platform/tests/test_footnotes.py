from hrs_platform.services.footnotes import resolve_footnotes


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

    from hrs_platform.services.retrieval_chunks import retrieval_chunks

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
