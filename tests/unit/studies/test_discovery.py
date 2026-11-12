from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from hrs.library.errors import ServiceError
from hrs.studies.discovery import search_materials


def test_topic_search_never_expands_an_empty_or_foreign_scope():
    search = Mock(return_value={"hits": [], "available": True})
    research = SimpleNamespace(
        workspace=SimpleNamespace(available_books=lambda: ["local"]),
        evidence=SimpleNamespace(search=search),
    )
    assert search_materials(research, "粮食", topic={"book_ids": [], "materials": []})["hits"] == []
    search.assert_not_called()
    with pytest.raises(ServiceError):
        search_materials(research, "粮食", book_ids=["foreign"])
    search.assert_not_called()
    search_materials(
        research,
        "粮食",
        topic={"book_ids": [], "materials": [{"book_id": "local", "article_id": "article"}]},
    )
    assert search.call_args.kwargs["book_ids"] == ["local"]
    assert search.call_args.kwargs["chapter_ids"] == ["article"]


def test_topic_search_unions_whole_books_with_pinned_articles():
    search = Mock(return_value={"hits": [], "available": True})
    research = SimpleNamespace(
        workspace=SimpleNamespace(available_books=lambda: ["whole", "selected", "outside"]),
        evidence=SimpleNamespace(search=search, article_scope=Mock(return_value={"whole-article"})),
    )
    search_materials(
        research,
        "粮食",
        topic={
            "book_ids": ["whole"],
            "materials": [{"book_id": "selected", "article_id": "selected-article"}],
        },
    )
    assert search.call_args.kwargs["book_ids"] == ["selected", "whole"]
    assert search.call_args.kwargs["chapter_ids"] == ["selected-article", "whole-article"]
