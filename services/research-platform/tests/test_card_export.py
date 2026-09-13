import json
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import insert, select

from hrs_platform import schema as db
from hrs_platform.activities import objects_for
from hrs_platform.api import create_app
from test_review import seed


def test_candidate_export_keeps_full_contract_exact_quote_pages_and_survives_cache_loss(platform, tmp_path):
    settings, engine = platform
    settings = settings.model_copy(update={"cache_root": tmp_path})
    run, _ = seed(engine)
    objects = objects_for(settings)
    candidate = {
        "title": "技术导出测试",
        "document_type": "合成文本",
        "source_layer": "技术夹具",
        "formation_date": {"state": "unknown"},
        "entities": [{"name": "测试主体"}],
        "relations": [{"reason": "保留限制条件"}],
        "items": [
            {
                "title": "出处",
                "text": "只引用第二段",
                "selections": [{"unit_id": "u", "quote": "第二段。", "occurrence": 0}],
                "limitations": ["仅为软件测试"],
            }
        ],
    }
    units = [
        {
            "unit_id": "u",
            "title": "测试章节",
            "text": "第一段。第二段。第三段。",
            "pages": [3, 4, 5],
            "sources": [
                {"unit_start": 0, "unit_end": 4, "pages": [3]},
                {"unit_start": 4, "unit_end": 8, "pages": [4]},
                {"unit_start": 8, "unit_end": 12, "pages": [5]},
            ],
        }
    ]
    checks = {"conclusion": "needs_revision", "technical_fixture": True}
    content = objects.put_bytes(json.dumps({"candidate": candidate, "units": units}).encode())
    verdict = objects.put_bytes(json.dumps(checks).encode())
    identity = str(uuid4())
    with engine.begin() as connection:
        book = connection.scalar(select(db.runs.c.book_id).where(db.runs.c.id == run))
        connection.execute(
            insert(db.cards).values(
                id=identity,
                run_id=run,
                book_id=book,
                title=candidate["title"],
                state="needs_revision",
                content=content,
                checks=verdict,
            )
        )
    with TestClient(create_app(settings, engine)) as client:
        response = client.get(f"/api/v2/cards/{identity}/export")
        assert response.status_code == 200
        assert "候选卡：机器核验尚未通过" in response.text
        assert "出处：测试章节，PDF 物理页 4；" in response.text
        exported = json.loads(response.text.split("```json\n")[1].split("\n```")[0])
        assert exported["candidate"] == candidate and exported["units"] == units
        assert exported["machine_checks"] == checks
        for path in (tmp_path / "downloads").iterdir():
            path.unlink()
        assert client.get(f"/api/v2/cards/{identity}/export").content == response.content
