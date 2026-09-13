from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import insert

from hrs_platform import schema as db
from hrs_platform.activities import objects_for
from hrs_platform.api import create_app


def test_original_supports_http_range_and_recovers_from_s3_without_its_cache(platform, tmp_path):
    settings, engine = platform
    settings = settings.model_copy(update={"cache_root": tmp_path})
    book, run = str(uuid4()), str(uuid4())
    content = b"%PDF-1.7\n" + b"technical-range-fixture\n" * 100
    reference = objects_for(settings).put_bytes(content, "application/pdf")
    with engine.begin() as connection:
        connection.execute(insert(db.books).values(id=book, title="原件读取协议测试", state="processing"))
        connection.execute(
            insert(db.runs).values(id=run, book_id=book, source=reference, state="processing", stage="test")
        )
    with TestClient(create_app(settings, engine)) as client:
        url = f"/api/v2/runs/{run}/artifacts/original.pdf"
        response = client.get(url, headers={"Range": "bytes=0-8"})
        assert response.status_code == 206 and response.content == content[:9]
        (tmp_path / "downloads" / reference["sha256"]).unlink()
        assert client.get(url).content == content
