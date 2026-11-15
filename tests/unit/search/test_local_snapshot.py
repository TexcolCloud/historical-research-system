from copy import deepcopy
from types import SimpleNamespace

from hrs.processing.hashing import digest
from hrs.search.local_snapshot import search_published


def document(text, fragments):
    structure = {
        "schema_version": 1,
        "coverage": [],
        "fragments": fragments,
        "relations": [],
        "image_sha256": "image",
        "markdown_sha256": digest(text),
        "source_task_id": "review",
        "source_result_sha256": "result",
        "source_reading_sha256": "reading",
    }
    return {
        "id": "a" * 24,
        "state": "published",
        "title": "测试来源",
        "source": {"sha256": "original"},
        "pages": [
            {
                "page": 1,
                "image": "p1.png",
                "image_sha256": "image",
                "approved": {"markdown": text, "sha256": digest(text)},
                "source_structure": structure,
            }
        ],
    }


def store_for(doc, active=None):
    return SimpleNamespace(
        list_documents=lambda: [{"id": doc["id"]}],
        status=lambda _: deepcopy(doc),
        verify_evidence=lambda _: None,
        artifact_path=lambda _doc, relative: relative,
        publications=SimpleNamespace(published_snapshot=lambda _doc, _record: deepcopy(active)),
    )


def test_local_search_uses_the_same_conservative_roles_and_unicode_source_ranges():
    text = "重复页眉\n正文😀事实123，不能删掉正文数字。\n编者注：补充依据。"
    fragments = [
        {
            "fragment_id": "p1:h",
            "role": "header",
            "layer": "source",
            "start": 0,
            "end": 4,
            "text_sha256": digest(text[:4]),
        }
    ]
    doc = document(text, fragments)
    before = deepcopy(doc)
    store = store_for(doc)
    assert search_published(store, "重复页眉") == []
    assert search_published(store, "重复页眉", source_scope="all")
    hit = search_published(store, "😀事实123")[0]
    assert "😀事实123" in hit["excerpt"] and "重复页眉" not in hit["excerpt"]
    for piece in hit["snippet_segments"]:
        if not piece["synthetic"]:
            assert text[piece["start"] : piece["end"]] == piece["text"]
    assert doc == before


def test_local_search_never_substitutes_a_revision_candidate_for_the_active_publication():
    old = document("仍可引用的历史原文", [])
    draft = document("尚未核对的新候选", [])
    draft.update(
        state="processing",
        active_publication={"revision": 1},
        revision_history=[{"reason": "重新复核"}],
    )
    store = store_for(draft, old)
    assert search_published(store, "新候选") == []
    hit = search_published(store, "历史原文")[0]
    assert hit["revision_pending"] is True and hit["revision_reason"] == "重新复核"
