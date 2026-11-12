"""Synthetic Markdown exports preserve canonical footnote ownership and literal failures."""

import re
from types import SimpleNamespace

import pytest

from hrs.library.errors import ServiceError
from hrs.library.footnotes import resolve_footnotes
from hrs.processing.hashing import digest
from hrs.studies.exporting import _quote_markdown, export_canvas


def test_canvas_export_namespaces_quote_definitions_nested_notes_and_duplicate_labels(monkeypatch):
    prefix = "前言。\n\n"
    first = "正文甲[^1]，缺注[^9]。\n\n[^1]: 第一页原注，引向[^2]。\n[^2]: 深层原注。\n\n"
    second = "正文乙[^1]。\n\n[^1]: 第二页原注。\n"
    text = prefix + first + second
    parts = [
        {"start": 0, "end": len(prefix + first), "text": prefix + first, "source": {"pages": [9]}},
        {"start": len(prefix + first), "end": len(text), "text": second, "source": {"pages": [10]}},
    ]
    article = {"id": "article", "text": text, "text_sha256": digest(text), "parts": parts}
    notes = resolve_footnotes(text, parts)
    spans = []
    for identity, start, end in (
        ("e1", len(prefix), len(prefix + first)),
        ("e2", len(prefix + first), text.index("\n\n", len(prefix + first))),
    ):
        spans.append(
            {
                "id": identity,
                "article_id": "article",
                "quote": text[start:end],
                "start": start,
                "end": end,
                "text_sha256": digest(text),
                "pages": [9 if identity == "e1" else 10],
                "footnotes": [
                    {**note, "text": text[note["note"]["start"] : note["note"]["end"]]}
                    for note in notes
                    if any(start <= reference["start"] < end for reference in note["references"])
                ],
            }
        )
    other_text = "另一篇正文[^1]。\n\n[^1]: 另一篇原注。\n"
    other_parts = [
        {"start": 0, "end": len(other_text), "text": other_text, "source": {"pages": [9]}}
    ]
    other = {"id": "other-article", "text": other_text, "parts": other_parts}
    other_notes = resolve_footnotes(other_text, other_parts)
    spans.append(
        {
            "id": "e3",
            "article_id": other["id"],
            "quote": other_text[: other_text.index("\n\n")],
            "start": 0,
            "end": other_text.index("\n\n"),
            "text_sha256": digest(other_text),
            "pages": [9],
            "footnotes": [
                {**note, "text": other_text[note["note"]["start"] : note["note"]["end"]]}
                for note in other_notes
            ],
        }
    )
    source = {
        "id": "pin",
        "card_id": "card",
        "card_version": 1,
        "title": "保留材料",
        "source_current": True,
        "evidence": spans,
    }
    bundle = {
        "canvas": {"title": "画布", "document": {"nodes": [], "edges": []}},
        "topic": {"materials": [source]},
        "records": [],
    }
    monkeypatch.setattr("hrs.studies.exporting.canvas_bundle", lambda service, canvas: bundle)
    workflow = SimpleNamespace(
        card=lambda *args, **kwargs: {"run_id": "retained-run"},
        snapshot=lambda run: {"articles": [article, other]},
        sources=lambda run: [],
    )
    service = SimpleNamespace(research=SimpleNamespace(workflow=workflow))
    result = export_canvas(service, "canvas", format="markdown")
    references = set(re.findall(r"(?<!\\)\[\^([^\]\n]+)\](?!:)", result))
    definitions = re.findall(r"^\[\^([^\]\n]+)\]:", result, re.M)
    assert references == set(definitions)
    assert len(definitions) == len(set(definitions)) == 4
    assert all(key.startswith("study-") for key in references)
    assert "\\[^9]" in result
    assert "第一页原注" in result and "第二页原注" in result and "深层原注" in result
    assert "原文范围：5–" in result
    # Repeated printed numbers must retain different source-owned definitions.
    first_key = re.search(r"^\[\^([^\]]+)\]: 第一页原注", result, re.M)[1]
    second_key = re.search(r"^\[\^([^\]]+)\]: 第二页原注", result, re.M)[1]
    assert first_key != second_key
    assert f"正文甲[^{first_key}]" in result and f"正文乙[^{second_key}]" in result
    third_key = re.search(r"^\[\^([^\]]+)\]: 另一篇原注", result, re.M)[1]
    assert third_key not in {first_key, second_key}
    assert f"另一篇正文[^{third_key}]" in result


def test_legacy_note_without_canonical_geometry_exports_literal_markers(monkeypatch):
    source = {
        "id": "old-pin",
        "card_id": "old-card",
        "card_version": 1,
        "title": "旧选材",
        "source_current": False,
        "evidence": [
            {
                "id": "e",
                "quote": "旧引文[^1]。",
                "start": 0,
                "end": 9,
                "pages": [3],
                "footnotes": [{"id": "old-note", "text": "[^1]: 旧注，参见[^2]。"}],
            }
        ],
    }
    bundle = {
        "canvas": {"title": "画布", "document": {"nodes": [], "edges": []}},
        "topic": {"materials": [source]},
        "records": [],
    }
    monkeypatch.setattr("hrs.studies.exporting.canvas_bundle", lambda service, canvas: bundle)
    result = export_canvas(SimpleNamespace(research=SimpleNamespace()), "canvas", format="markdown")
    assert not re.findall(r"(?<!\\)\[\^[^\]\n]+\]", result)
    assert "旧注" in result and "old-note" in result
    assert "位置" in result and "保留" in result


def test_canonical_quote_hash_mismatch_blocks_markdown_export():
    article = {"text": "保留正文", "parts": []}
    span = {
        "article_id": "article",
        "quote": "保留正文",
        "start": 0,
        "end": 4,
        "text_sha256": digest("另一版本"),
    }
    with pytest.raises(ServiceError) as conflict:
        _quote_markdown(span, {"article": article}, "study-pinned")
    assert conflict.value.status_code == 409
