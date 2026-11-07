"""Public Markdown retains physical source spans after note rewriting and old caches."""

import json
import re
from copy import deepcopy
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select

from hrs.api.main import create_app
from hrs.library.catalog import Library
from hrs.library.exports import export_markdown
from hrs.library.footnotes import export_footnotes
from hrs.library.runs.outputs import Outputs, fingerprint
from hrs.library.structure import reading_view
from hrs.storage import models as db
from hrs.storage.service import objects_for


@pytest.fixture
def published_source(platform):
    settings, engine = platform
    objects = objects_for(settings)
    book, run = (str(uuid4()) for _ in range(2))
    texts = [
        "# First article\n\nFirst page[^1].\n\n[^1]: First source.\n\n",
        "Shared page, first article[^1].\n\n[^1]: First article source.\n\n",
        "# Second article\n\nShared page, second article[^1].\n\n[^1]: Second article source.\n\n",
        "Third page[^1].\n\n[^1]: Third source.\n\n",
    ]
    chapters = []
    for position, indices in enumerate(((0, 1), (2, 3))):
        parts = []
        offset = 0
        for index in indices:
            page = (1, 2, 2, 3)[index]
            original_start = len(texts[1]) if index == 2 else 0
            parts.append(
                {
                    "span_id": f"source-span-{index}",
                    "start": offset,
                    "end": offset + len(texts[index]),
                    "text": texts[index],
                    "source": {
                        "page": page,
                        "pages": [page],
                        "original_start": original_start,
                        "original_end": original_start + len(texts[index]),
                        "image_sha256": str(page) * 64,
                        "markdown_sha256": "a" * 64,
                        "approval_type": "machine",
                    },
                }
            )
            offset += len(texts[index])
        content = objects.put_bytes(json.dumps({"parts": parts}).encode())
        chapters.append(
            dict(
                id=str(uuid4()),
                book_id=book,
                run_id=run,
                position=position,
                title=f"Article {position + 1}",
                kind="article",
                content=content,
                pages=sorted({part["source"]["page"] for part in parts}),
                codepoints=offset,
            )
        )
    with engine.begin() as connection:
        connection.execute(
            insert(db.books).values(id=book, title="Source-bound export", state="ready")
        )
        connection.execute(
            insert(db.runs).values(
                id=run,
                book_id=book,
                state="completed",
                stage="complete",
                result={"published": True},
            )
        )
        connection.execute(insert(db.chapters), chapters)
    return settings, engine, book, run, chapters


def test_public_export_keeps_each_source_span_without_reusing_pre_provenance_cache(
    published_source,
):
    settings, engine, book, run, rows = published_source
    library = Library(settings, engine)
    before = [library.chapter(row["id"]) for row in rows]
    canonical = deepcopy(before)
    old_text = "".join(
        export_footnotes(
            chapter["reading_text"],
            chapter["reading_footnotes"],
            f"c{index}",
            parts=chapter["reading_parts"],
        )
        for index, chapter in enumerate(before, 1)
    )
    outputs = Outputs(settings, engine)
    old = outputs.objects.put_bytes(old_text.encode(), run_id=run)
    dependencies = {"chapters": [row["content"]["sha256"] for row in rows], "workflow_test": False}
    old_key = "export:book-reading-v6-note-cross-references:" + fingerprint(dependencies)
    outputs.put(run, old_key, old, dependencies)
    with TestClient(create_app(settings, engine)) as client:
        canonical_api = [client.get(f"/api/v2/chapters/{row['id']}").json() for row in rows]
        first = client.get(f"/api/v2/books/{book}/export?download=latest")
        second = client.get(f"/api/v2/books/{book}/export?download=latest")
        assert first.status_code == second.status_code == 200
        assert first.content == second.content
        exported = first.text
        assert exported.count("<!-- physical-page:") == 4
        assert exported.count("physical-page: 2 image-sha256: " + "2" * 64) == 2
        assert exported.count("image-sha256:") == 4
        for chapter in before:
            for part in chapter["parts"]:
                source = part["source"]
                assert f"source-span: {part['span_id']}" in exported
                assert (
                    f"original-start: {source['original_start']} original-end: {source['original_end']}"
                    in exported
                )
        without_metadata = re.sub(r"<!-- physical-page:.*?-->\n\n", "", exported)
        assert without_metadata == old_text
        assert "[^c1-1]: First source." in exported
        assert "[^c1-2]: First article source." in exported
        assert "[^c2-1]: Second article source." in exported
        assert "[^c2-2]: Third source." in exported
        assert [client.get(f"/api/v2/chapters/{row['id']}").json() for row in rows] == canonical_api
    assert [library.chapter(row["id"]) for row in rows] == canonical
    assert outputs.get(run, old_key, dependencies) == old
    with engine.connect() as connection:
        assert list(
            connection.scalars(select(db.chapters.c.content).order_by(db.chapters.c.position))
        ) == [row["content"] for row in rows]


def source_chapter(*texts):
    parts = [
        {
            "span_id": f"span-{number}",
            "text": text,
            "source": {
                "pages": [number],
                "image_sha256": str(number) * 64,
                "original_start": 0,
                "original_end": len(text),
            },
        }
        for number, text in enumerate(texts, 1)
    ]
    return {"text": "".join(texts), "parts": parts, "structure": []}


def with_reading(chapter):
    reading = reading_view(chapter)
    return {
        **chapter,
        "reading_text": reading["text"],
        "reading_parts": reading["parts"],
        "reading_footnotes": reading["footnotes"],
        "reading_segments": reading["segments"],
    }


def test_source_boundary_inside_a_cross_page_note_keeps_original_link_coordinates():
    chapter = with_reading(
        source_chapter(
            "Body[^1].\n\n[^1]: Starts on page one.\n",
            "    Continued on page two, same[^2].\n\nBody two[^2].\n\n[^2]: Page two note.\n\n",
        )
    )
    canonical = deepcopy(chapter)
    exported = export_markdown(chapter, "article")
    assert exported.count("<!-- physical-page:") == 2
    assert (
        exported.index("Starts on page one.")
        < exported.index("physical-page: 2")
        < exported.index("Continued on page two")
    )
    assert "    <!-- physical-page: 2 image-sha256: " + "2" * 64 in exported
    assert "same[^article-2]." in exported
    assert exported.count("[^article-1]") == 2
    assert exported.count("[^article-2]") == 3
    assert chapter == canonical


def test_merged_reading_table_keeps_all_canonical_source_spans_in_original_order():
    first = "<table><tr><th>Place</th></tr><tr><td>First</td></tr></table>\n\n"
    second = "<table><tr><th>Place</th></tr><tr><td>Second</td></tr></table>\n\n"
    chapter = source_chapter(first, second)
    merged = first.replace("</table>", "<tr><td>Second</td></tr></table>")
    chapter["structure"] = [
        {
            "status": "ready",
            "display_text": merged,
            "pages": [1, 2],
            "members": [
                {"start": 0, "end": len(first)},
                {"start": len(first), "end": len(first + second)},
            ],
        }
    ]
    chapter = with_reading(chapter)
    canonical = deepcopy(chapter)
    exported = export_markdown(chapter, "article")
    assert exported.count("<!-- physical-page:") == 2
    assert (
        exported.index("physical-page: 1")
        < exported.index("physical-page: 2")
        < exported.index("<table>")
    )
    assert re.sub(r"<!-- physical-page:.*?-->\n\n", "", exported) == chapter["reading_text"]
    assert exported.count("<table>") == 1
    assert "First" in exported and "Second" in exported
    assert chapter == canonical


def test_legacy_multiple_page_span_does_not_invent_missing_image_hash_or_exact_page():
    chapter = with_reading(
        {
            "text": "Legacy approved correction.\n",
            "parts": [
                {
                    "text": "Legacy approved correction.\n",
                    "source": {"pages": [4, 5], "original_start": 17, "original_end": 31},
                }
            ],
        }
    )
    exported = export_markdown(chapter, "legacy")
    assert (
        exported
        == "<!-- physical-pages: 4,5 original-start: 17 original-end: 31 -->\n\nLegacy approved correction.\n"
    )
    assert "image-sha256" not in exported and "physical-page:" not in exported


def test_boundaries_inside_inline_note_markers_and_code_keep_both_tokens_and_paragraphs_intact():
    chapter = with_reading(source_chapter("Body[", "^1] and `co", "de`.\n\n[^1]: Note.\n"))
    expected = export_footnotes(
        chapter["reading_text"],
        chapter["reading_footnotes"],
        "article",
        parts=chapter["reading_parts"],
    )
    exported = export_markdown(chapter, "article")
    assert exported.count("<!-- physical-page:") == 3
    assert "[^article-1]" in exported and "`code`" in exported
    assert re.sub(r"<!-- physical-page:.*?-->(?:\n\n)?", "", exported) == expected
    assert "Body<!-- physical-page: 2" in exported
    assert "and <!-- physical-page: 3" in exported
