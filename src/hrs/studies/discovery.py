"""Topic and current-workspace selection over the existing evidence search engine."""

from hrs.library.errors import ServiceError


def search_materials(research, query, *, topic=None, book_ids=None, semantic=True, limit=12):
    available = set(research.workspace.available_books())
    materials = (topic or {}).get("materials", [])
    whole_books = set((topic or {}).get("book_ids", []))
    selected = (
        whole_books | {item["book_id"] for item in materials}
        if topic is not None
        else set(book_ids)
        if book_ids is not None
        else available
    )
    if selected - available:
        raise ServiceError(409, "检索范围包含当前工作区不可用的资料，请更新选材范围。")
    if not selected:
        return {
            "query": query,
            "available": True,
            "backend": "hybrid" if semantic else "lexical",
            "hits": [],
            "evidence_status": "no_published_sources",
            "metrics": {},
        }
    options = {}
    if topic is not None and not whole_books:
        options["chapter_ids"] = sorted({item["article_id"] for item in materials})
    elif topic is not None and materials and selected - whole_books:
        # Whole-book members and individually selected articles form a union.
        options["chapter_ids"] = sorted(
            research.evidence.article_scope(book_ids=sorted(whole_books))
            | {item["article_id"] for item in materials}
        )
    return research.evidence.search(
        query,
        book_ids=sorted(selected),
        semantic=semantic,
        limit=limit,
        trusted_scope=True,
        **options,
    )
