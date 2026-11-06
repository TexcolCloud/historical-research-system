import json

from fastapi.testclient import TestClient
from sqlalchemy import insert, select, update

from hrs.api.main import create_app
from hrs.library.catalog import Library
from hrs.library.exports import Exports
from hrs.storage import models as db

from .test_review import seed
from .test_structure_views import table


def seed_structure_book(settings, engine):
    run, issues = seed(engine, native=False)
    library = Library(settings, engine)
    objects = library.review.objects

    def saved(value):
        return objects.put_bytes(json.dumps(value, ensure_ascii=False).encode())

    first, second = table("甲县"), table("乙县")
    values = [
        "# 第一章 运输统计\n\n单位：吨\n\n" + first,
        second + "\n\n另见注释[^a]。\n\n[^a]: 只计本月。",
    ]
    pages, offset = [], 0
    for n, value in enumerate(values, 1):
        pages.append({"page": n, "start": offset, "end": offset + len(value)})
        offset += len(value) + 2
    files = {
        "document.md": objects.put_bytes("\n\n".join(values).encode()),
        "pages.json": saved({"pages": pages}),
        "completion.json": saved({"pages": [{"page": n} for n in (1, 2)]}),
        "paddle-restructure.json": saved(
            {
                "tables": [
                    {
                        "pages": [1, 2],
                        "merged_html": first.replace(
                            "</table>", "<tr><td>乙县</td><td>120</td></tr></table>"
                        ),
                        "members": [
                            {"page": 1, "original_html": first},
                            {"page": 2, "original_html": second},
                        ],
                    }
                ],
                "titles": [
                    {
                        "page": 1,
                        "title": "第一章 运输统计",
                        "level": 1,
                        "before": "## 第一章 运输统计",
                        "after": "# 第一章 运输统计",
                    }
                ],
            }
        ),
    }
    bundle = {
        "manifest": {
            "candidate_markdown": "document.md",
            "page_boundaries": "pages.json",
            "page_count": 2,
            "pages": [{"page": n, "status": "pending-human-review"} for n in (1, 2)],
        },
        "files": files,
    }
    with engine.begin() as connection:
        connection.execute(
            update(db.runs)
            .where(db.runs.c.id == run)
            .values(source=objects.put_bytes(b"synthetic source"), conversion=saved(bundle))
        )
        for issue, page, value in zip(issues, pages, values, strict=True):
            connection.execute(
                update(db.review_issues)
                .where(db.review_issues.c.id == issue)
                .values(
                    content=saved(
                        {
                            "text": value,
                            "pages": [page["page"]],
                            "start": page["start"],
                            "end": page["end"],
                        }
                    )
                )
            )
    return run, library


def test_structure_read_api_cache_and_published_reader_share_checked_sources(platform):
    settings, engine = platform
    run, library = seed_structure_book(settings, engine)
    client = TestClient(create_app(settings, engine))
    pending = client.get(f"/api/v2/runs/{run}/structure")
    assert pending.status_code == 200
    assert all(i["status"] == "retained" for i in pending.json()["items"])
    with engine.begin() as connection:
        connection.execute(
            update(db.review_issues)
            .where(db.review_issues.c.run_id == run)
            .values(state="approved")
        )
        connection.execute(
            update(db.runs).where(db.runs.c.id == run).values(pending_count=0, revision=2)
        )
    ready = client.get(f"/api/v2/runs/{run}/structure").json()
    assert all(i["status"] == "ready" for i in ready["items"])
    assert client.get(f"/api/v2/runs/{run}/structure").json() == ready
    source = library.prepare(run)
    parts = [
        {
            "span_id": s["id"],
            "start": s["start_offset"],
            "end": s["end_offset"],
            "text": s["selected_text"],
            "source": s["source_record"],
        }
        for s in source["spans"]
    ]
    # Existing immutable publications remain readable after retiring their executor.
    from uuid import uuid4

    from hrs.library.structure import project_structure

    chapter_id = str(uuid4())
    with engine.begin() as connection:
        book_id = connection.scalar(select(db.runs.c.book_id).where(db.runs.c.id == run))
        content = library.review.objects.put_bytes(
            json.dumps(
                {
                    "parts": parts,
                    "structure": project_structure(library.structure(run), parts),
                },
                ensure_ascii=False,
            ).encode()
        )
        connection.execute(
            insert(db.chapters).values(
                id=chapter_id,
                book_id=book_id,
                run_id=run,
                position=0,
                title="第一章 运输统计",
                kind="article",
                content=content,
                pages=[1, 2],
                codepoints=sum(len(p["text"]) for p in parts),
            )
        )
        connection.execute(
            update(db.runs)
            .where(db.runs.c.id == run)
            .values(
                state="completed",
                stage="complete",
                result={"published": True},
            )
        )
    response = client.get(f"/api/v2/chapters/{chapter_id}")
    assert response.status_code == 200
    chapter = response.json()
    assert chapter["text"].count("<table>") == 2
    assert chapter["reading_text"].count("<table>") == 1
    assert len(chapter["reading_footnotes"]) == 1
    assert chapter["structure"][0]["pages"] == [1, 2]
    title, exported = Exports(settings, engine).book(book_id)
    assert title
    exported_text = library.review.objects.read_bytes(exported).decode()
    assert exported_text.count("<table>") == 1
    assert exported_text.count("[^c1-1]") == 2
    assert "[^a]" not in exported_text
    assert "只计本月" in exported_text
    with engine.connect() as connection:
        saved_steps = connection.scalars(
            select(db.stage_outputs.c.step).where(db.stage_outputs.c.run_id == run)
        ).all()
    assert sum(step.startswith("reading-structure:") for step in saved_steps) == 2


def test_retired_structure_review_keeps_drafts_but_requires_native_migration(platform):
    from uuid import uuid4

    import pytest

    from hrs.api.schemas import ReviewDecision
    from hrs.library.errors import ServiceError
    from hrs.library.review import digest

    settings, engine = platform
    run, library = seed_structure_book(settings, engine)
    issue = library.review.list(run)[0]
    with pytest.raises(ServiceError, match="原生流程"):
        library.review.decide(
            issue["id"],
            ReviewDecision(
                decision_id=uuid4(),
                expected_revision=issue["revision"],
                expected_text_sha256=digest("unchanged"),
                action="confirm",
            ),
        )
    assert library.review.status(run)["pending_count"] == 2
