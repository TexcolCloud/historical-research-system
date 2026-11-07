"""Cross-page evidence remains separate while every visible fragment can navigate."""

from copy import deepcopy
from hashlib import sha256

from hrs.library.reading_locations import target_in_chapter, toc_links


def published(pages):
    return {
        "id": "chapter",
        "run_id": "run",
        "text": "".join(text for _, text in pages),
        "parts": [
            {
                "text": text,
                "source": {
                    "page": page,
                    "pages": [page],
                    "original_start": 0,
                    "original_end": len(text),
                    "markdown_sha256": sha256(text.encode()).hexdigest(),
                    "image_sha256": f"image-{page}",
                },
            }
            for page, text in pages
        ],
    }


def span(page, text, anchor):
    return {
        "page": page,
        "anchor": anchor,
        "start": text.index(anchor),
        "end": text.index(anchor) + len(anchor),
        "markdown_sha256": sha256(text.encode()).hexdigest(),
        "image_sha256": f"image-{page}",
    }


def test_both_toc_fragments_link_without_rewriting_page_text():
    first, second = "## 目录\n\n关于甲乙\n1\n", "工作的决定……12\n"
    body = published([(1, first), (2, second)])
    fragments = [span(1, first, "关于甲乙"), span(2, second, "工作的决定……12")]
    body["navigation"] = {
        "entries": [
            {
                "id": "entry",
                "title": "关于甲乙工作的决定",
                "level": 1,
                "source_page": 1,
                "source_anchor": fragments[0]["anchor"],
                "source_spans": fragments,
            }
        ]
    }
    before = deepcopy(body)
    links = toc_links(body, {"entry": {"chapter_id": "body"}})
    assert [body["text"][r["source_start"] : r["source_end"]] for r in links] == [
        "关于甲乙",
        "工作的决定……12",
    ]
    assert len({r["id"] for r in links}) == 2
    assert body == before
    changed = deepcopy(body)
    changed["parts"][1]["source"]["image_sha256"] = "changed"
    assert toc_links(changed, {"entry": {"chapter_id": "body"}}) == []


def test_title_target_rejects_changed_continuation_evidence():
    first, second = "😀\n## 关于甲乙\n12\n", "工作的决定\n\n正文。"
    body = published([(7, first), (8, second)])
    fragments = [span(7, first, "关于甲乙"), span(8, second, "工作的决定")]
    target = {
        "physical_page": 7,
        "start": fragments[0]["start"],
        "anchor": fragments[0]["anchor"],
        "markdown_sha256": fragments[0]["markdown_sha256"],
        "image_sha256": fragments[0]["image_sha256"],
        "title": "关于甲乙工作的决定",
        "title_spans": fragments,
    }
    assert target_in_chapter(body, target)["anchor"] == "关于甲乙"
    changed = deepcopy(target)
    changed["title_spans"][1]["markdown_sha256"] = "stale"
    assert target_in_chapter(body, changed) is None
    changed = deepcopy(target)
    changed["title_spans"][1]["start"] += 1
    assert target_in_chapter(body, changed) is None
