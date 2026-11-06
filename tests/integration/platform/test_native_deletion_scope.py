"""Native deletion shares checkpoints only with verified owners in the same workspace."""

import hashlib
from uuid import uuid4

import pytest
from PIL import Image
from sqlalchemy import insert, select

from hrs.library.deletion import receipt, request_deletion
from hrs.library.processing_bridge import CodexBridge
from hrs.processing.application import prepare
from hrs.processing.store import Store
from hrs.processing.workspace import identity
from hrs.storage import models as db


def add_owner(engine, doc, workspace_id):
    book_id, run_id = str(uuid4()), str(uuid4())
    result = {"processing_backend": "codex", "codex_document_id": doc["id"]}
    if workspace_id is not None:
        result["codex_workspace_id"] = workspace_id
    with engine.begin() as connection:
        connection.execute(
            insert(db.books).values(id=book_id, title="Synthetic deletion scope", state="running")
        )
        connection.execute(
            insert(db.runs).values(
                id=run_id,
                book_id=book_id,
                state="running",
                stage="ocr",
                source={"sha256": doc["source"]["sha256"]},
                result=result,
            )
        )
    return book_id, run_id


def sql_owner(engine, book_id, run_id):
    with engine.connect() as connection:
        return (
            dict(
                connection.execute(select(db.books).where(db.books.c.id == book_id))
                .mappings()
                .one()
            ),
            dict(
                connection.execute(select(db.runs).where(db.runs.c.id == run_id)).mappings().one()
            ),
        )


def document_files(store, doc_id):
    directory = store.document_directory(doc_id)
    return {
        path.relative_to(directory).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in directory.rglob("*")
        if path.is_file()
    }


@pytest.fixture
def native_source(platform, tmp_path):
    settings, engine = platform
    source = tmp_path / "same-source.pdf"
    Image.new("RGB", (60, 80), "white").save(source)
    bridge = CodexBridge(settings, engine)
    document = prepare(bridge.store, str(source))
    return settings, engine, source, bridge, document


def test_foreign_workspace_same_pdf_is_not_a_shared_native_owner(native_source, tmp_path):
    settings, engine, source, bridge, document = native_source
    other = Store(tmp_path / "other-native")
    other_document = prepare(other, str(source))
    current_workspace = identity(bridge.store)["workspace_id"]
    other_workspace = identity(other)["workspace_id"]
    assert document["id"] == other_document["id"] and current_workspace != other_workspace
    book_id, _ = add_owner(engine, document, current_workspace)
    other_book, other_run = add_owner(engine, other_document, other_workspace)
    before_sql = sql_owner(engine, other_book, other_run)
    before_native = other.status(document["id"])
    before_files = document_files(other, document["id"])

    request_deletion(settings, engine, book_id)
    bridge.delete_pending()

    assert receipt(engine, book_id)["state"] == "completed"
    assert not bridge.store.document_directory(document["id"]).exists()
    assert document["id"] not in bridge.store.checkpoints.ids()
    assert sql_owner(engine, other_book, other_run) == before_sql
    assert other.status(document["id"]) == before_native
    assert document_files(other, document["id"]) == before_files
    assert source.exists()


@pytest.mark.parametrize("owner_scope", ["explicit_same", "legacy_matching", "legacy_mismatch"])
def test_shared_native_owner_requires_current_identity_or_verified_legacy_source(
    native_source, owner_scope
):
    settings, engine, _, bridge, document = native_source
    workspace_id = identity(bridge.store)["workspace_id"]
    book_id, _ = add_owner(engine, document, workspace_id)
    other_document = (
        {**document, "source": {"sha256": "f" * 64}}
        if owner_scope == "legacy_mismatch"
        else document
    )
    other_book, other_run = add_owner(
        engine, other_document, workspace_id if owner_scope == "explicit_same" else None
    )
    before = bridge.store.status(document["id"])
    before_files = document_files(bridge.store, document["id"])
    before_sql = sql_owner(engine, other_book, other_run)

    request_deletion(settings, engine, book_id)
    bridge.delete_pending()

    assert receipt(engine, book_id)["state"] == "completed"
    if owner_scope == "legacy_mismatch":
        assert not bridge.store.document_directory(document["id"]).exists()
        assert sql_owner(engine, other_book, other_run) == before_sql
    else:
        assert bridge.store.status(document["id"]) == before
        assert document_files(bridge.store, document["id"]) == before_files
        book, run = sql_owner(engine, other_book, other_run)
        assert book == before_sql[0]
        assert run["result"]["codex_workspace_id"] == workspace_id
        assert {k: v for k, v in run.items() if k != "result"} == {
            k: v for k, v in before_sql[1].items() if k != "result"
        }
