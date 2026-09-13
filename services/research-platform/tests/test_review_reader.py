import json

from fastapi.testclient import TestClient
from sqlalchemy import select, update

from hrs_platform import schema as db
from hrs_platform.activities import objects_for
from hrs_platform.api import create_app
from hrs_platform.outputs import Outputs
from test_review import seed


def test_clean_conversion_pages_remain_readable_while_the_book_awaits_review(platform, tmp_path):
    settings, engine = platform
    settings = settings.model_copy(update={"cache_root": tmp_path})
    run, _ = seed(engine)
    objects = objects_for(settings)
    raw = "待核对页。正常正文页。"
    files = {
        "candidate.md": objects.put_bytes(raw.encode()),
        "pages.json": objects.put_bytes(
            json.dumps(
                {"pages": [{"page": 1, "start": 0, "end": 5}, {"page": 2, "start": 5, "end": len(raw)}]}
            ).encode()
        ),
    }
    manifest = {
        "candidate_markdown": "candidate.md",
        "page_boundaries": "pages.json",
        "page_count": 2,
        "pages": [
            {"page": 1, "image": "page1.png", "status": "pending-review"},
            {"page": 2, "image": "page2.png", "status": "release-accepted"},
        ],
    }
    ref = objects.put_bytes(json.dumps({"manifest": manifest, "files": files}).encode())
    with engine.begin() as connection:
        connection.execute(update(db.runs).where(db.runs.c.id == run).values(conversion=ref))
    with TestClient(create_app(settings, engine)) as client:
        response = client.get(f"/api/v2/runs/{run}/review-pages/2")
        assert response.status_code == 200
        assert response.json()["text"] == "正常正文页。"
        assert response.json()["machine_status"] == "release-accepted"
        assert client.get(f"/api/v2/runs/{run}/review-pages/3").status_code == 404
    with engine.connect() as connection:
        state = connection.execute(select(db.runs).where(db.runs.c.id == run)).mappings().one()
    assert state["state"] == "awaiting_review" and state["pending_count"] == 2


def test_committed_ocr_draft_and_images_are_readable_before_full_visual_review(platform, tmp_path):
    settings, engine = platform
    settings = settings.model_copy(update={"cache_root": tmp_path})
    run, _ = seed(engine)
    with engine.begin() as connection:
        connection.execute(
            update(db.runs)
            .where(db.runs.c.id == run)
            .values(state="processing", stage="vision", source={"sha256": "a" * 64}, result={})
        )
    objects = objects_for(settings)
    image = objects.put_bytes(b"technical-image-bytes", "image/png")
    checkpoint = objects.put_bytes(
        json.dumps(
            {
                "source_sha256": "a" * 64,
                "pages": [{"page": 1, "text": "技术识别底稿，尚未核验。", "image_path": "page.png"}],
            }
        ).encode()
    )
    with TestClient(create_app(settings, engine)) as client:
        assert client.get(f"/api/v2/runs/{run}/review-pages/1").status_code == 409
        Outputs(settings, engine).put(
            run, "ocr-evidence", {"files": {"ocr-checkpoint.json": checkpoint, "page.png": image}}, {}
        )
        response = client.get(f"/api/v2/runs/{run}/review-pages/1")
        assert response.status_code == 200
        page = response.json()
        assert page["text"] == "技术识别底稿，尚未核验。" and page["machine_status"] == "unreviewed-source"
        assert client.get(page["image"]).content == b"technical-image-bytes"
        for cached in (tmp_path / "review-reader").iterdir():
            cached.unlink()
        assert client.get(f"/api/v2/runs/{run}/review-pages/1").json() == page
    with engine.connect() as connection:
        state = connection.execute(select(db.runs).where(db.runs.c.id == run)).mappings().one()
    assert state["state"] == "processing" and state["conversion"] is None
    assert state["result"] == {} and state["pending_count"] == 2
