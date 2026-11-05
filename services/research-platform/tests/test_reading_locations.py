"""Published navigation resolves against canonical text, never title guesses or live revisions."""

from hashlib import sha256
from uuid import uuid4

from hrs_platform.services.documents.structure import reading_view


def chapter(text, *, page=7, original_start=0, page_text=None):
    return {
        "id": str(uuid4()),
        "run_id": str(uuid4()),
        "text": text,
        "parts": [
            {
                "span_id": "part",
                "start": 900,
                "end": 900 + len(text),
                "text": text,
                "source": {
                    "page": page,
                    "pages": [page],
                    "original_start": original_start,
                    "original_end": original_start + len(text),
                    "markdown_sha256": sha256((page_text or text).encode()).hexdigest(),
                    "image_sha256": "a" * 64,
                },
            }
        ],
    }


def test_same_page_duplicate_heading_uses_verified_page_offset_and_article_range():
    from hrs_platform.services.documents.reading_locations import target_in_chapter

    first = "😀前文。\n\n## 同名标题\n\n甲段。\n\n"
    second = "## 同名标题\n\n乙段。\n"
    page_text = first + second
    a = chapter(first, page_text=page_text)
    b = chapter(second, original_start=len(first), page_text=page_text)
    target = {
        "physical_page": 7,
        "start": len(first),
        "anchor": "## 同名标题",
        "markdown_sha256": sha256(page_text.encode()).hexdigest(),
        "image_sha256": "a" * 64,
    }
    assert target_in_chapter(a, target) is None
    resolved = target_in_chapter(b, target)
    assert resolved["chapter_id"] == b["id"]
    assert resolved["start"] == 3
    assert resolved["end"] == 7
    assert resolved["anchor"] == "同名标题"
    assert resolved["text_sha256"] == sha256(second.encode()).hexdigest()
    assert target_in_chapter(b, {**target, "markdown_sha256": "stale"}) is None


def test_search_target_keeps_unicode_offsets_and_current_canonical_hash():
    from hrs_platform.services.documents.reading_locations import reading_target

    body = chapter("😀正文和原注[^1]。\n\n[^1]: 出处。")
    target = reading_target(body, 1, 3)
    assert target["anchor"] == "正文"
    assert (target["start"], target["end"], target["physical_page"]) == (1, 3, 7)
    assert reading_target(body, 1, 3, physical_page=99) is None
    assert reading_target(body, -1, 3) is None


def test_multiline_heading_search_range_starts_at_visible_text_without_truncating_body():
    from hrs_platform.services.documents.reading_locations import reading_target

    text = "## 第二章\n\n😀正文。后续内容。"
    body = chapter(text)
    target = reading_target(body, 0, len(text))
    assert target["start"] == 3
    assert target["end"] == len(text)
    assert target["anchor"] == "第二章\n\n😀正文。后续内容。"
    assert target["text_sha256"] == sha256(text.encode()).hexdigest()
    for literal in ("    # 代码文字", "前文 # 普通文字"):
        begin = literal.index("#")
        assert reading_target(chapter(literal), begin, len(literal))["start"] == begin


def test_toc_links_require_unique_literal_source_on_the_declared_page():
    from hrs_platform.services.documents.reading_locations import toc_links

    text = "## 目录\n\n同名标题……12\n"
    body = chapter(text, page=1)
    target = {
        "chapter_id": str(uuid4()),
        "run_id": body["run_id"],
        "physical_page": 7,
        "start": 3,
        "end": 7,
        "text_sha256": "b" * 64,
        "anchor": "同名标题",
    }
    entry = {
        "id": "e",
        "title": "同名标题",
        "level": 1,
        "status": "matched",
        "source_page": 1,
        "source_anchor": "同名标题……12",
    }
    body["navigation"] = {"entries": [entry]}
    links = toc_links(body, {"e": target})
    assert len(links) == 1
    assert text[links[0]["source_start"] : links[0]["source_end"]] == entry["source_anchor"]
    assert (
        toc_links(
            {**body, "text": text + text, "parts": [{**body["parts"][0], "text": text + text}]}, {"e": target}
        )
        == []
    )
    assert toc_links(body, {}) == []


def test_reading_projection_declares_precise_and_transformed_ranges_without_rewriting_source():
    text = "😀前文\n\n<table><tr><td>甲</td></tr></table>\n\n后文[^1]。\n\n[^1]: 原注。"
    start, end = text.index("<table>"), text.index("</table>") + len("</table>")
    body = chapter(text)
    body["structure"] = [
        {
            "members": [{"start": start, "end": end}],
            "status": "ready",
            "display_text": "| 甲 |\n| --- |",
            "pages": [7],
        }
    ]
    result = reading_view(body)
    assert body["text"] == text
    segments = result["segments"]
    assert any(not segment["exact"] for segment in segments)
    for segment in segments:
        if segment["exact"]:
            assert (
                text[segment["start"] : segment["end"]]
                == result["text"][segment["reading_start"] : segment["reading_end"]]
            )
    assert segments[0]["start"] == 0 and segments[-1]["end"] == len(text)
    assert result["footnotes"]


def test_published_snapshot_and_inline_links_remain_bound_when_live_run_has_a_new_revision(platform):
    import json

    from sqlalchemy import insert, update

    from hrs_platform import models as db
    from hrs_platform.schemas import Chapter, NavigationReport
    from hrs_platform.services.documents.library import Library

    settings, engine = platform
    library = Library(settings, engine)
    book_id, run_id, toc_id, body_id = [str(uuid4()) for _ in range(4)]
    directory = "## 目录\n\n第二章……12\n"
    text = "😀前言。\n\n## 第二章\n\n正文[^1]。\n\n[^1]: 原注。"
    target = {
        "physical_page": 7,
        "printed_label": "12",
        "sequence": "body",
        "start": text.index("## 第二章"),
        "anchor": "## 第二章",
        "markdown_sha256": sha256(text.encode()).hexdigest(),
        "image_sha256": "a" * 64,
    }
    entry = {
        "id": "toc-entry",
        "title": "第二章",
        "level": 1,
        "path": ["第二章"],
        "source_page": 1,
        "source_anchor": "第二章……12",
        "target_label": "12",
        "status": "matched",
        "reason": "",
        "target": target,
    }
    navigation = {
        "state": "ready",
        "version": 4,
        "sha256": "old-published",
        "entries": [entry],
        "coverage_complete": True,
    }

    def save(value, page):
        body = chapter(value, page=page)
        body["navigation"] = navigation
        return library.review.objects.put_bytes(json.dumps(body, ensure_ascii=False).encode())

    toc_ref, body_ref = save(directory, 1), save(text, 7)
    with engine.begin() as conn:
        conn.execute(insert(db.books).values(id=book_id, title="发布样本", state="ready"))
        conn.execute(
            insert(db.runs).values(
                id=run_id,
                book_id=book_id,
                state="completed",
                stage="complete",
                result={"published": True, "live_navigation": "new-unpublished"},
            )
        )
        conn.execute(
            insert(db.chapters),
            [
                dict(
                    id=toc_id,
                    run_id=run_id,
                    book_id=book_id,
                    position=0,
                    title="目录",
                    kind="directory",
                    content=toc_ref,
                    pages=[1],
                    codepoints=len(directory),
                ),
                dict(
                    id=body_id,
                    run_id=run_id,
                    book_id=book_id,
                    position=1,
                    title="正文",
                    kind="article",
                    content=body_ref,
                    pages=[7],
                    codepoints=len(text),
                ),
            ],
        )
    report = library.reading_navigation(book_id)
    NavigationReport.model_validate(report)
    assert report["sha256"] == "old-published"
    location = report["entries"][0]["reading_target"]
    assert location["chapter_id"] == body_id
    assert text[location["start"] : location["end"]] == "第二章"
    visible = library.chapter(toc_id)
    Chapter.model_validate(visible)
    assert visible["toc_links"][0]["target"] == location
    assert visible["text"] == directory
    assert library.chapter(body_id)["footnotes"]
    stale = {**navigation, "entries": [{**entry, "target": {**target, "markdown_sha256": "new-page"}}]}
    assert library.navigation_targets(run_id, stale) == {}
    # Deleting/replacing a SQL publication invalidates target ownership despite cached objects.
    with engine.begin() as conn:
        conn.execute(update(db.chapters).where(db.chapters.c.id == body_id).values(pages=[99]))
    assert library.navigation_targets(run_id, navigation) == {}


def test_search_api_reading_target_matches_the_shared_renderer_contract(platform):
    import json
    from pathlib import Path
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    from hrs_platform.api.deps import get_search
    from hrs_platform.main import create_app
    from hrs_platform.services.retrieval.chunks import source_excerpt
    from hrs_platform.services.retrieval.search import Search

    saved = json.loads((Path(__file__).parent / "fixtures/reading_heading_target.json").read_text("utf-8"))
    body = saved["chapter"]
    retrieval = object.__new__(Search)
    retrieval.library = SimpleNamespace(chapter=lambda _: body)

    def search(*args, **kwargs):
        # Only result selection is fixed; source coordinates and response targets
        # cross the same production expansion/serialization path as real hits.
        return retrieval.reading_results(
            [{**source_excerpt(body, 0, len(body["text"])), "score": 1.0, "context": []}]
        )

    settings, engine = platform
    app = create_app(settings, engine)
    app.dependency_overrides[get_search] = lambda: SimpleNamespace(search=search)
    with TestClient(app) as client:
        response = client.get(
            "/api/v2/search", params={"q": "第二章", "book_id": body["book_id"], "semantic": False}
        )
        assert response.status_code == 200, response.text
    assert response.json()[0]["reading_target"] == saved["target"]
    assert response.json()[0]["text"] == body["text"]
