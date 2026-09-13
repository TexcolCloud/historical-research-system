"""Real CPU retrieval, OpenSearch, S3 export and cache-loss recovery over technical text."""

import json
import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert

from hrs_platform import schema as db
from hrs_platform.activities import objects_for
from hrs_platform.api import create_app
from hrs_platform.search import Search


@pytest.mark.skipif(
    os.environ.get("PLATFORM_TEST_SEARCH") != "1", reason="Explicit local CPU model/search integration"
)
def test_real_search_retains_source_scopes_and_exports_after_cache_loss(platform, tmp_path, monkeypatch):
    settings, engine = platform
    name = "hrs-v2-test-" + uuid4().hex
    settings = settings.model_copy(update={"opensearch_index": name, "cache_root": tmp_path})
    search = Search(settings, engine)
    objects = objects_for(settings)
    book, run, chapter, span = [str(uuid4()) for _ in range(4)]
    source = "# 仓储记录\n这是一段软件验收文本。仓库登记粮食110吨，不包括其他月份。\n"
    body = {
        "parts": [
            {
                "span_id": span,
                "start": 0,
                "end": len(source),
                "text": source,
                "source": {"pages": [7], "original_start": 0, "original_end": len(source)},
            }
        ]
    }
    ref = objects.put_bytes(json.dumps(body, ensure_ascii=False).encode())
    with engine.begin() as connection:
        connection.execute(insert(db.books).values(id=book, title="检索与导出技术验收", state="ready"))
        connection.execute(
            insert(db.runs).values(
                id=run, book_id=book, state="ready", stage="indexing", result={"published": True}
            )
        )
        connection.execute(
            insert(db.chapters).values(
                id=chapter,
                book_id=book,
                run_id=run,
                position=0,
                title="仓储记录",
                kind="article",
                content=ref,
                pages=[7],
                codepoints=len(source),
            )
        )
    try:
        assert search.index(run)["chunks"] == 1
        assert search.search("倉儲", book)[0]["text"] == source
        assert search.search("粮食入库数量", book, semantic=True)[0]["pages"] == [7]
        assert search.search("粮食", str(uuid4())) == []
        with TestClient(create_app(settings, engine)) as client:
            response = client.get(f"/api/v2/books/{book}/export")
            assert response.status_code == 200 and response.text == source
            for file in (tmp_path / "downloads").iterdir():
                if file.is_file():
                    file.unlink()
            assert client.get(f"/api/v2/books/{book}/export").text == source
        search.client.indices.delete(index=name)
        monkeypatch.setattr(
            search, "compute", lambda *a, **k: pytest.fail("Index recovery must reuse S3 vectors.")
        )
        assert search.index(run)["chunks"] == 1
    finally:
        search.client.indices.delete(index=name, ignore=[404])
