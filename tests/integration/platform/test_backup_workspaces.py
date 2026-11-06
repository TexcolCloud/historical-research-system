"""A whole SQL backup closes over every native authority referenced by its runs."""

import json
import os
import socket
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from pydantic import SecretStr
from pypdf import PdfWriter
from sqlalchemy import delete, insert, select, update
from sqlalchemy.engine import make_url

from hrs import backups
from hrs.config import Settings
from hrs.processing.application import prepare
from hrs.processing.snapshots import atomic_json
from hrs.processing.store import Store
from hrs.processing.workspace import identity
from hrs.storage import models as db
from hrs.storage.db import engine_for, migrate

from .test_backup_manifest import Objects
from .test_codex_bridge import finish

pytestmark = pytest.mark.skipif(
    os.environ.get("PLATFORM_TEST_BACKUP") != "1", reason="Dedicated PostgreSQL backup test"
)


@pytest.fixture
def linked_workspaces(tmp_path, monkeypatch):
    """New database only; source pixels and a pending draft use the normal Store API."""
    configured = Settings.load()
    identifier = uuid4().hex[:10]
    source_database = "hrs_restore_backupws_src_" + identifier
    suffix = "multi_" + identifier
    target_database = "hrs_restore_" + suffix + "_0"
    url = make_url(configured.database_url.get_secret_value())
    admin_url = url.set(drivername="postgresql", database="postgres").render_as_string(
        hide_password=False
    )
    source = tmp_path / "same-source.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=300)
    with source.open("wb") as stream:
        writer.write(stream)
    stores = [Store(tmp_path / "native-a"), Store(tmp_path / "native-b")]
    documents = [prepare(store, str(source), "Synthetic backup transport") for store in stores]
    workspace_ids = [identity(store)["workspace_id"] for store in stores]
    assert workspace_ids[0] != workspace_ids[1]
    assert documents[0]["id"] == documents[1]["id"]
    store_b, document_b = stores[1], documents[1]["id"]
    # Synthetic technical state, with no content approval or model invocation.
    finish(store_b, document_b, "ocr", {"markdown": "Blank technical backup fixture."})
    finish(
        store_b,
        document_b,
        "review",
        {
            "verdict": "pending",
            "disposition": "unreadable",
            "issues": ["Backup draft retention fixture"],
        },
    )
    packet = store_b.human_packet(document_b, 1)
    draft = store_b.record_human_review(
        document_b,
        1,
        input_sha256=packet["input_sha256"],
        markdown="Unsubmitted draft must survive for workspace B.",
        verdict="pending",
    )
    assert draft["receipt"]["human_approval"] is False
    stores[0].pause_document(documents[0]["id"])
    source_objects, target_objects = Objects("source"), Objects("target")
    source_ref = {
        "key": "hrs/v2/objects/original",
        "sha256": documents[0]["source"]["sha256"],
        "byte_length": source.stat().st_size,
        "media_type": "application/pdf",
    }
    source_objects.data[source_ref["key"]] = (
        source.read_bytes(),
        {"sha256": source_ref["sha256"]},
        "application/pdf",
    )
    monkeypatch.setattr(
        backups,
        "objects_for",
        lambda settings: source_objects if settings.s3_bucket == "source" else target_objects,
    )
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "hrs-backupws-" + identifier)
    with socket.socket() as port:
        port.bind(("127.0.0.1", 0))
        unused_port = port.getsockname()[1]
    settings = configured.model_copy(
        update={
            "database_url": SecretStr(
                url.set(database=source_database).render_as_string(hide_password=False)
            ),
            "project_root": tmp_path / "project",
            "codex_root": stores[0].root,
            "cache_root": tmp_path / "cache",
            "s3_bucket": "source",
            "tus_control_url": f"http://127.0.0.1:{unused_port}/uploads/",
        }
    )
    settings.project_root.mkdir()
    engine = None
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(source_database)))
        try:
            engine = engine_for(settings)
            migrate(engine)
            run_ids = [str(uuid4()), str(uuid4())]
            with engine.begin() as connection:
                for run_id, workspace_id, state in zip(
                    run_ids, workspace_ids, ["paused", "awaiting_review"], strict=True
                ):
                    book_id = str(uuid4())
                    connection.execute(
                        insert(db.books).values(
                            id=book_id, title="Isolated backup fixture", state=state
                        )
                    )
                    connection.execute(
                        insert(db.runs).values(
                            id=run_id,
                            book_id=book_id,
                            state=state,
                            stage="review",
                            source=source_ref,
                            result={
                                "processing_backend": "codex",
                                "codex_document_id": document_b,
                                "codex_workspace_id": workspace_id,
                            },
                        )
                    )
            yield SimpleNamespace(
                settings=settings,
                engine=engine,
                stores=stores,
                ids=workspace_ids,
                document_id=document_b,
                run_ids=run_ids,
                source=source_objects,
                target=target_objects,
                suffix=suffix,
                target_database=target_database,
            )
        finally:
            if engine is not None:
                engine.dispose()
            for name in (target_database, source_database):
                admin.execute(
                    sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
                )


def test_backup_rejects_missing_sql_workspace_before_creating_destination(
    linked_workspaces, tmp_path
):
    fixture = linked_workspaces
    destination = tmp_path / "missing-root"
    with pytest.raises(ValueError) as failure:
        backups.backup(fixture.settings, destination=destination)
    assert fixture.ids[1] in str(failure.value)
    assert not destination.exists()


def test_full_sql_restore_retains_two_native_authorities_and_pending_draft(
    linked_workspaces, tmp_path
):
    fixture = linked_workspaces
    before = [store.status(fixture.document_id) for store in fixture.stores]
    receipt = backups.backup(
        fixture.settings, destination=tmp_path / "complete", native_roots=[fixture.stores[1].root]
    )
    manifest = json.loads(Path(receipt["manifest"]).read_text("utf-8"))
    expected = [
        {
            "workspace_id": fixture.ids[0],
            "archive": "native-workspace.zip",
            "document_ids": [fixture.document_id],
            "primary": True,
        },
        {
            "workspace_id": fixture.ids[1],
            "archive": "native-workspace-1.zip",
            "document_ids": [fixture.document_id],
            "primary": False,
        },
    ]
    assert manifest["native_workspaces"] == expected
    assert "native-workspace-1.zip" in manifest["files"]
    fixture.source.data.clear()  # Independent object snapshot must supply restored source bytes.
    destination = tmp_path / "restored-authorities"
    result = backups.restore(
        fixture.settings.model_copy(update={"s3_bucket": "target"}),
        receipt,
        fixture.suffix,
        native_destination=destination,
    )
    assert result["native_workspaces"] == {key: str(destination / key) for key in fixture.ids}
    assert result["native_workspace"] == str(destination / fixture.ids[0])
    for workspace_id, original in zip(fixture.ids, before, strict=True):
        restored = Store(Path(result["native_workspaces"][workspace_id]))
        after = restored.status(fixture.document_id)
        assert identity(restored)["workspace_id"] == workspace_id
        assert {k: v for k, v in after.items() if k != "directory"} == {
            k: v for k, v in original.items() if k != "directory"
        }
        restored.verify_evidence(after)
    restored_b = Store(Path(result["native_workspaces"][fixture.ids[1]])).status(
        fixture.document_id
    )
    draft = next(task for task in reversed(restored_b["tasks"]) if task["stage"] == "human_review")
    assert draft["result"]["markdown"] == "Unsubmitted draft must survive for workspace B."
    assert draft["receipt"]["human_approval"] is False
    assert draft["receipt"]["approval_type"] == "human_draft"
    assert fixture.target.data["hrs/v2/objects/original"][0]
    restored_settings = fixture.settings.model_copy(
        update={
            "database_url": SecretStr(
                make_url(fixture.settings.database_url.get_secret_value())
                .set(
                    database=result[
                        make_url(fixture.settings.database_url.get_secret_value()).database
                    ]
                )
                .render_as_string(hide_password=False)
            )
        }
    )
    restored_engine = engine_for(restored_settings)
    try:
        with restored_engine.connect() as connection:
            retained = list(
                connection.execute(
                    select(db.runs.c.id, db.runs.c.state, db.runs.c.result)
                ).mappings()
            )
        assert {row["id"] for row in retained} == set(fixture.run_ids)
        assert {row["result"]["codex_workspace_id"] for row in retained} == set(fixture.ids)
        assert {row["state"] for row in retained} == {"paused", "awaiting_review"}
    finally:
        restored_engine.dispose()


def test_backup_rejects_distinct_roots_with_duplicate_workspace_identity(
    linked_workspaces, tmp_path
):
    fixture = linked_workspaces
    marker = fixture.stores[1].root / ".workspace.json"
    original = json.loads(marker.read_text("utf-8"))
    atomic_json(marker, {**original, "workspace_id": fixture.ids[0]})
    destination = tmp_path / "duplicate-identity"
    try:
        with pytest.raises(ValueError, match="duplicate workspace identity"):
            backups.backup(
                fixture.settings, destination=destination, native_roots=[fixture.stores[1].root]
            )
        assert not destination.exists()
    finally:
        atomic_json(marker, original)


def test_backup_accepts_a_legacy_run_only_with_one_matching_supplied_authority(
    linked_workspaces, tmp_path
):
    fixture = linked_workspaces
    with fixture.engine.begin() as connection:
        legacy = connection.scalar(
            select(db.runs.c.result).where(db.runs.c.id == fixture.run_ids[0])
        )
        legacy = {key: value for key, value in legacy.items() if key != "codex_workspace_id"}
        connection.execute(
            update(db.runs).where(db.runs.c.id == fixture.run_ids[0]).values(result=legacy)
        )
        other_book = connection.scalar(
            select(db.runs.c.book_id).where(db.runs.c.id == fixture.run_ids[1])
        )
        connection.execute(delete(db.runs).where(db.runs.c.id == fixture.run_ids[1]))
        connection.execute(delete(db.books).where(db.books.c.id == other_book))
    receipt = backups.backup(fixture.settings, destination=tmp_path / "legacy-unique")
    manifest = json.loads(Path(receipt["manifest"]).read_text("utf-8"))
    assert manifest["native_workspaces"] == [
        {
            "workspace_id": fixture.ids[0],
            "archive": "native-workspace.zip",
            "document_ids": [fixture.document_id],
            "primary": True,
        }
    ]
    with fixture.engine.connect() as connection:
        assert (
            connection.scalar(select(db.runs.c.result).where(db.runs.c.id == fixture.run_ids[0]))
            == legacy
        )


def test_backup_rejects_legacy_mapping_ambiguous_across_same_source_roots(
    linked_workspaces, tmp_path
):
    fixture = linked_workspaces
    with fixture.engine.begin() as connection:
        legacy = connection.scalar(
            select(db.runs.c.result).where(db.runs.c.id == fixture.run_ids[0])
        )
        connection.execute(
            update(db.runs)
            .where(db.runs.c.id == fixture.run_ids[0])
            .values(
                result={key: value for key, value in legacy.items() if key != "codex_workspace_id"}
            )
        )
    destination = tmp_path / "legacy-ambiguous"
    with pytest.raises(ValueError, match="ambiguous") as failure:
        backups.backup(
            fixture.settings, destination=destination, native_roots=[fixture.stores[1].root]
        )
    assert fixture.run_ids[0] in str(failure.value)
    assert all(workspace_id in str(failure.value) for workspace_id in fixture.ids)
    assert not destination.exists()


@pytest.mark.parametrize("mismatch", ["missing_document", "source_hash_mismatch"])
def test_backup_rejects_scoped_native_evidence_mismatch_before_creating_destination(
    linked_workspaces, tmp_path, mismatch
):
    fixture = linked_workspaces
    with fixture.engine.begin() as connection:
        run = (
            connection.execute(select(db.runs).where(db.runs.c.id == fixture.run_ids[1]))
            .mappings()
            .one()
        )
        document_id = "e" * 24 if mismatch == "missing_document" else fixture.document_id
        connection.execute(
            update(db.runs)
            .where(db.runs.c.id == fixture.run_ids[1])
            .values(
                result={**run["result"], "codex_document_id": document_id},
                source={**run["source"], "sha256": "f" * 64}
                if mismatch == "source_hash_mismatch"
                else run["source"],
            )
        )
    destination = tmp_path / mismatch
    with pytest.raises(ValueError) as failure:
        backups.backup(
            fixture.settings, destination=destination, native_roots=[fixture.stores[1].root]
        )
    assert fixture.ids[1] in str(failure.value)
    assert document_id in str(failure.value)
    assert not destination.exists()
