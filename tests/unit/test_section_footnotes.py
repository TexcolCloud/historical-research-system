"""A unique explicit note section can bind printed definitions without rewriting sources."""

from copy import deepcopy

import pytest

from hrs.library.footnotes import export_footnotes, resolve_footnotes
from hrs.processing.hashing import digest


def fixture_parts():
    pages = [
        "# 一封复信\n\n😀𠮷地甲[^7]，行程乙[^11]。再次甲[^7]。\n\n",
        "## 注释\n\n〔7〕甲地的旧名，编者说明。\n\n续段仍归第七注，——编者\n\n",
        "另页续文不可丢。\n\n〔11〕乙指渡口，不表明已经抵达。\n\n## 附件\n\n附件正文不是注释。\n",
    ]
    parts = []
    offset = 0
    for number, text in zip([17, 23, 29], pages, strict=True):
        parts.append(
            {
                "text": text,
                "span_id": f"part-{number}",
                "start": offset,
                "end": offset + len(text),
                "source": {"pages": [number], "original_start": 0, "original_end": len(text)},
            }
        )
        offset += len(text)
    return parts


def test_unique_section_binds_explicit_cross_page_references_and_long_note_bodies():
    parts = fixture_parts()
    before = deepcopy(parts)
    text = "".join(part["text"] for part in parts)
    sha = digest(text)
    notes = resolve_footnotes(text, parts)
    assert [note["label"] for note in notes] == ["7", "11"]
    assert [len(note["references"]) for note in notes] == [2, 1]
    span = notes[0]["note"]
    assert text[span["start"] : span["marker_end"]] == "〔7〕"
    assert "另页续文不可丢" in text[span["start"] : span["end"]]
    assert "附件正文" not in text[notes[1]["note"]["start"] : notes[1]["note"]["end"]]
    exported = export_footnotes(text, notes, "source", parts=parts)
    assert "[^source-1]: 甲地的旧名" in exported
    assert exported.count("[^source-1]") == 3
    assert "[^source-2]: 乙指渡口" in exported
    assert "## 附件" in exported and "附件正文不是注释" in exported
    assert digest(text) == sha and parts == before


@pytest.mark.parametrize(
    "pages",
    [
        ["正文[^7]。\n\n", "〔7〕没有明确注释节。\n"],
        ["正文[^7]。\n\n## 注释\n\n〔7〕第一项。\n\n〔7〕第二项。\n"],
        ["正文[^7]。\n\n## 注释\n\n〔7〕第一节。\n\n## 注釋\n\n〔7〕第二节。\n"],
        ["正文[^7]。\n\n## 注释\n\n〔7〕印刷注。\n\n[^7]: 另一同号定义。\n"],
        ["正文〔7〕是普通括号。\n\n## 注释\n\n〔7〕不据普通数字猜跨页。\n"],
        ["正文[^7]。\n\n## 附录\n\n〔7〕普通列表。\n"],
        ["正文[^7]。\n\n```md\n## 注释\n〔7〕代码不是定义。\n```\n"],
        ["正文[^7]。\n\n## 注释\n\n> 〔7〕引用块不是定义。\n"],
        ["`正文[^7]`\n\n## 注释\n\n〔7〕代码内注号不归属。\n"],
        ["正文[^7]。\n\n## 注释\n\n- 普通列表\n  〔7〕列表里的编号不是定义。\n"],
        ["正文[^7]。\n\n## 注释\n\n〔07〕不猜补零编号。\n"],
        ["\\[^7]字面示例。\n\n## 注释\n\n〔7〕反斜线注号不归属。\n"],
        ["正文[^7]。\n\n## 注释\n\n〔7〕\n\n"],
    ],
)
def test_ambiguous_or_unscoped_printed_definitions_remain_literal(pages):
    parts = [{"text": text, "source": {"pages": [i + 1]}} for i, text in enumerate(pages)]
    assert resolve_footnotes("".join(pages), parts) == []


def test_new_section_rule_does_not_expand_cross_page_circled_number_ownership():
    pages = ["甲地①。\n\n", "## 注释\n\n① 仍不能凭圈号跨页配对。\n"]
    parts = [{"text": text, "source": {"pages": [i + 1]}} for i, text in enumerate(pages)]
    assert resolve_footnotes("".join(pages), parts) == []


def test_reader_and_search_share_resolved_note_and_owner_ranges(monkeypatch):
    from hrs.library import chapter_content
    from hrs.search.chunks import retrieval_chunks

    parts = fixture_parts()
    text = "".join(p["text"] for p in parts)
    body = {"parts": parts, "structure": []}
    monkeypatch.setattr(chapter_content, "read_json", lambda *_: deepcopy(body))
    reading = chapter_content.read_content(None, None)
    assert [note["label"] for note in reading["footnotes"]] == ["7", "11"]
    chapter = {
        "id": "11111111-1111-4111-8111-111111111111",
        "book_id": "book",
        "run_id": "run",
        "title": "复信",
        "text": text,
        "parts": parts,
    }
    hits = list(retrieval_chunks(chapter, "独立结构样本"))
    body_hit = next(hit for hit in hits if "行程乙" in hit["text"])
    assert any(c["role"] == "footnote" and "旧名" in c["text"] for c in body_hit["context"])
    note_hit = next(hit for hit in hits if hit["kind"] == "note" and "旧名" in hit["text"])
    assert any(c["role"] == "note_owner" and "行程乙" in c["text"] for c in note_hit["context"])
    assert reading["text_sha256"] == digest(text)


def test_printed_note_needs_unambiguous_physical_source_pages():
    parts = [
        {"text": "正文[^7]。\n\n", "source": {"pages": [1, 2]}},
        {"text": "## 注釋\n\n〔7〕不能猜正文来自哪一页。\n", "source": {"pages": [3]}},
    ]
    assert resolve_footnotes("".join(p["text"] for p in parts), parts) == []


def test_mixed_explicit_and_printed_notes_export_without_overlapping_definitions():
    pages = [
        "😀正文[^*]，甲地[^7]。\n\n",
        "[^*]: 题注。\n## 注釋\n〔7〕地名解释。\n\n## 后记\n\n〔7〕后记普通列表。\n",
    ]
    parts = [{"text": t, "source": {"pages": [i + 1]}} for i, t in enumerate(pages)]
    text = "".join(pages)
    notes = resolve_footnotes(text, parts)
    assert [n["label"] for n in notes] == ["*", "7"]
    assert "## 注釋" not in text[notes[0]["note"]["start"] : notes[0]["note"]["end"]]
    exported = export_footnotes(text, notes, "mixed", parts=parts)
    assert "[^mixed-1]: 题注。" in exported and "[^mixed-2]: 地名解释。" in exported
    assert "〔7〕后记普通列表" in exported


def test_new_research_snapshot_uses_the_same_note_map_without_changing_source(monkeypatch):
    import json
    from contextlib import contextmanager
    from types import SimpleNamespace
    from uuid import uuid4

    from hrs.research import sources

    parts = fixture_parts()
    text = "".join(p["text"] for p in parts)
    version = {"publication_sha256": "a" * 64}
    body = {
        "parts": parts,
        "acceptance": {
            "content_acceptance": "machine_or_human",
            "publication": {
                "markdown_sha256": version["publication_sha256"],
                "content_verified": True,
            },
        },
    }
    run = {
        "id": str(uuid4()),
        "book_id": str(uuid4()),
        "result": {"codex_document_id": "d" * 24},
        "source": {},
        "conversion": {},
    }
    row = {
        "id": str(uuid4()),
        "book_id": run["book_id"],
        "run_id": run["id"],
        "title": "独立结构复信",
        "kind": "article",
        "pages": [17, 23, 29],
        "content": {"key": "synthetic-source"},
    }

    class Result:
        def mappings(self):
            return self

        def one_or_none(self):
            return run

        def all(self):
            return [row]

    class Connection:
        def execute(self, *_):
            return Result()

        def scalar(self, *_):
            return "结构验收样本"

    class Engine:
        @contextmanager
        def begin(self):
            yield Connection()

    objects = SimpleNamespace(read_bytes=lambda _: json.dumps(body, ensure_ascii=False).encode())
    monkeypatch.setattr(sources, "require_workspace", lambda _engine, _run, _store: _run)
    monkeypatch.setattr(sources, "indexed_version", lambda _: version)
    monkeypatch.setattr(sources, "identity", lambda _: {"workspace_id": "isolated-synthetic"})
    snapshot = sources.publication_snapshot(None, Engine(), None, objects, run)
    article = snapshot["articles"][0]
    assert article["text"] == text and article["text_sha256"] == digest(text)
    assert article["footnotes"] == resolve_footnotes(text, parts)
    assert [n["label"] for n in article["footnotes"]] == ["7", "11"]
    assert article["pages"] == [17, 23, 29]
