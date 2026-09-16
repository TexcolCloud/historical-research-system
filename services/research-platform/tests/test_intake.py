from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from hrs_platform import models as schema
from hrs_platform.main import create_app
from hrs_platform.core.db import migrate


def test_duplicate_finish_commits_one_run_without_temporal(platform):
    settings, engine = platform
    with TestClient(create_app(settings, engine)) as client:
        session = client.post("/api/v2/uploads", json={"filename": "新书.pdf", "byte_length": 128}).json()
        authorization = {"Authorization": f"Bearer {session['token']}"}
        upload = {
            "Size": 128,
            "Offset": 0,
            "MetaData": {"session_id": session["session_id"]},
            "Storage": None,
        }
        hook = {"Type": "pre-create", "Event": {"Upload": upload}}
        created = client.post("/internal/tus", json=hook, headers=authorization).json()
        upload["Storage"] = {
            "Type": "s3store",
            "Bucket": settings.s3_bucket,
            "Key": settings.upload_prefix + created["ChangeFileInfo"]["ID"],
        }
        upload["Offset"] = 128
        hook["Type"] = "post-finish"
        for _ in range(2):
            assert client.post("/internal/tus", json=hook, headers=authorization).status_code == 200
        books = client.get("/api/v2/books").json()
        assert len(books) == 1
        assert books[0]["stage"] == "verify_upload"
        with engine.connect() as connection:
            for table in (schema.runs, schema.outbox, schema.events):
                assert connection.scalar(select(func.count()).select_from(table)) == 1
            assert connection.scalar(select(schema.outbox.c.delivered)) is False


def test_upload_rejects_bad_ownership_and_unfinished_object(platform):
    settings, engine = platform
    with TestClient(create_app(settings, engine)) as client:
        assert (
            client.post("/api/v2/uploads", json={"filename": "../x.pdf", "byte_length": 1}).status_code == 422
        )
        session = client.post("/api/v2/uploads", json={"filename": "x.pdf", "byte_length": 128}).json()
        upload = {
            "Size": 128,
            "Offset": 127,
            "MetaData": {"session_id": session["session_id"]},
            "Storage": {
                "Type": "s3store",
                "Bucket": settings.s3_bucket,
                "Key": f"{settings.upload_prefix}{session['session_id']}/{uuid4()}",
            },
        }
        hook = {"Type": "post-finish", "Event": {"Upload": upload}}
        assert client.post("/internal/tus", json=hook).status_code == 403
        headers = {"Authorization": f"Bearer {session['token']}"}
        assert client.post("/internal/tus", json=hook, headers=headers).status_code == 422
        upload["Offset"] = 128
        upload["Storage"]["Key"] = settings.upload_prefix + str(uuid4())
        assert client.post("/internal/tus", json=hook, headers=headers).status_code == 422
        assert client.get("/api/v2/books").json()[0]["state"] == "uploading"


def test_migration_is_repeatable(platform):
    _, engine = platform
    migrate(engine)


def test_session_retry_after_lost_response_returns_same_book(platform):
    settings, engine = platform
    with TestClient(create_app(settings, engine)) as client:
        headers = {"Idempotency-Key": str(uuid4()), "X-Upload-Token": "x" * 43}
        body = {"filename": "same.pdf", "byte_length": 100}
        first = client.post("/api/v2/uploads", json=body, headers=headers)
        retry = client.post("/api/v2/uploads", json=body, headers=headers)
        assert first.status_code == retry.status_code == 201
        assert first.json() == retry.json()
        assert len(client.get("/api/v2/books").json()) == 1
        assert (
            client.post("/api/v2/uploads", json={**body, "byte_length": 200}, headers=headers).status_code
            == 409
        )
