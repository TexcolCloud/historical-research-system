from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import insert, select
from test_review import seed

from hrs_platform import schema as db
from hrs_platform.api import create_app


def test_lists_return_all_pages_without_duplicates(platform):
    settings, engine = platform
    run, _ = seed(engine)
    with engine.begin() as connection:
        book = connection.scalar(select(db.runs.c.book_id).where(db.runs.c.id == run))
        connection.execute(
            insert(db.cards),
            [
                {
                    "id": str(uuid4()),
                    "run_id": run,
                    "book_id": book,
                    "title": f"Card {index}",
                    "state": "adopted",
                    "content": {},
                    "checks": {},
                }
                for index in range(103)
            ],
        )
    with TestClient(create_app(settings, engine)) as client:
        cards = client.get("/api/v2/cards", params={"book_id": book, "limit": 100}).json()
        tail = client.get("/api/v2/cards", params={"book_id": book, "offset": 100, "limit": 100}).json()
        assert len(cards) == 100 and len(tail) == 3
        assert len({row["id"] for row in cards + tail}) == 103
        issues = [
            client.get("/api/v2/reviews", params={"run_id": run, "offset": offset, "limit": 1}).json()
            for offset in range(3)
        ]
        assert [len(page) for page in issues] == [1, 1, 0]
        assert issues[0][0]["id"] != issues[1][0]["id"]
        assert client.get("/api/v2/cards", params={"offset": -1}).status_code == 422
