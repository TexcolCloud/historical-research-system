"""Expired permits recover the same unfinished upload, not another book."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import func, select, update

from hrs.api.main import create_app
from hrs.storage import models as db


def test_expired_upload_renews_same_book_and_owner_without_replacing_objects(platform):
    settings, engine = platform
    identity = {"Idempotency-Key": str(uuid4()), "X-Upload-Token": "owner-token-" * 4}
    payload = {"filename": "resume.pdf", "byte_length": 128}
    with TestClient(create_app(settings, engine)) as client:
        first = client.post("/api/v2/uploads", json=payload, headers=identity).json()
        expired = datetime.now(UTC) - timedelta(seconds=1)
        with engine.begin() as connection:
            connection.execute(
                update(db.uploads)
                .where(db.uploads.c.id == first["session_id"])
                .values(expires_at=expired)
            )
        hook = {
            "Type": "pre-create",
            "Event": {
                "Upload": {
                    "Size": 128,
                    "Offset": 0,
                    "MetaData": {"session_id": first["session_id"]},
                    "Storage": None,
                }
            },
        }
        authorization = {"Authorization": "Bearer " + first["token"]}
        assert client.post("/internal/tus", json=hook, headers=authorization).json()["RejectUpload"]
        renewed = (
            client.post("/api/v2/uploads", json=payload, headers=identity).raise_for_status().json()
        )
        assert renewed["session_id"] == first["session_id"]
        assert renewed["book_id"] == first["book_id"]
        assert renewed["token"] == first["token"]
        assert datetime.fromisoformat(renewed["expires_at"]) > datetime.now(UTC)
        assert (
            "ChangeFileInfo"
            in client.post("/internal/tus", json=hook, headers=authorization).json()
        )
        retry = client.post("/api/v2/uploads", json=payload, headers=identity).json()
        assert retry == renewed
        wrong_owner = {**identity, "X-Upload-Token": "different-owner-token-" * 3}
        assert client.post("/api/v2/uploads", json=payload, headers=wrong_owner).status_code == 409
        with engine.connect() as connection:
            assert connection.scalar(select(func.count()).select_from(db.books)) == 1
            assert connection.scalar(select(func.count()).select_from(db.uploads)) == 1
            assert connection.scalar(select(func.count()).select_from(db.runs)) == 0


def test_completed_upload_permit_is_not_renewed(platform):
    settings, engine = platform
    identity = {"Idempotency-Key": str(uuid4()), "X-Upload-Token": "owner-token-" * 4}
    payload = {"filename": "done.pdf", "byte_length": 128}
    expired = datetime.now(UTC) - timedelta(days=1)
    with TestClient(create_app(settings, engine)) as client:
        first = client.post("/api/v2/uploads", json=payload, headers=identity).json()
        with engine.begin() as connection:
            connection.execute(
                update(db.uploads)
                .where(db.uploads.c.id == first["session_id"])
                .values(state="received", expires_at=expired)
            )
        existing = client.post("/api/v2/uploads", json=payload, headers=identity).json()
        assert datetime.fromisoformat(existing["expires_at"]) == expired
