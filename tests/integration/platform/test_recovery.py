from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import func, insert, select

from hrs.api.main import create_app
from hrs.storage import models as db


def test_recovery_errors_explain_the_action_without_showing_internal_english():
    from hrs.library.runs.recovery import recovery_reason

    assert "原件或证据" in recovery_reason(ValueError("hash mismatch at pages/0155.png"))
    assert "故障详情" in recovery_reason(FileNotFoundError("pages/0155.png"))
    assert "故障详情" in recovery_reason(RuntimeError("executor exited unexpectedly"))
    assert (
        recovery_reason(ValueError("Codex 登录已失效，请重新登录后恢复。"))
        == "Codex 登录已失效，请重新登录后恢复。"
    )


def test_repeated_retry_only_starts_one_new_attempt_and_preserves_published_book(platform):
    settings, engine = platform
    book, run = str(uuid4()), str(uuid4())
    with engine.begin() as connection:
        connection.execute(insert(db.books).values(id=book, title="恢复协议测试", state="ready"))
        connection.execute(
            insert(db.runs).values(
                id=run,
                book_id=book,
                state="failed",
                stage="indexing",
                result={"published": True, "codex_document_id": "retained-native-checkpoint"},
                source={"sha256": "a" * 64},
            )
        )
    with TestClient(create_app(settings, engine)) as client:
        request = {"request_id": str(uuid4())}
        url = f"/api/v2/runs/{run}/retry"
        assert client.post(url, json=request).status_code == 200
        assert client.post(url, json=request).status_code == 200
        assert client.post(url, json={"request_id": str(uuid4())}).status_code == 409
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(db.outbox)) == 1
        assert connection.scalar(select(db.runs.c.recovery_attempt)) == 1
        assert connection.scalar(select(db.books.c.state)) == "ready"
        assert connection.scalar(select(db.runs.c.source)) == {"sha256": "a" * 64}


def test_native_recovery_preflight_failure_is_durable_and_can_be_retried(
    platform, tmp_path, monkeypatch
):
    from PIL import Image

    from hrs.codex.mcp import prepare
    from hrs.library.books import get_run
    from hrs.library.processing_bridge import CodexBridge

    settings, engine = platform
    settings = settings.model_copy(update={"codex_root": tmp_path / "native"})
    bridge = CodexBridge(settings, engine)
    source = tmp_path / "fixture.png"
    Image.new("RGB", (40, 50), "white").save(source)
    identity = prepare(bridge.store, str(source))["id"]
    book, run = str(uuid4()), str(uuid4())
    with engine.begin() as connection:
        connection.execute(
            insert(db.books).values(id=book, title="Recovery fixture", state="failed")
        )
        connection.execute(
            insert(db.runs).values(
                id=run,
                book_id=book,
                state="failed",
                stage="ocr",
                result={"processing_backend": "codex", "codex_document_id": identity},
            )
        )

    def offline(*args):
        raise ValueError("Codex 登录已失效，请重新登录后恢复。")

    monkeypatch.setattr("hrs.library.runs.recovery.preflight", offline)
    with TestClient(create_app(settings, engine)) as client:
        url = f"/api/v2/runs/{run}/retry"
        request = {"request_id": str(uuid4())}
        assert client.post(url, json=request).json()["recovery"]["state"] == "checking"
        with engine.connect() as connection:
            event = dict(connection.execute(select(db.outbox)).mappings().one())
        bridge.dispatch(event)
        assert get_run(engine, run)["state"] == "failed"
        report = client.get(f"/api/v2/runs/{run}/recovery").json()
        assert report["state"] == "blocked" and "登录" in report["reason"]
        assert not bridge.store.status(identity).get("run_request")
        assert client.post(url, json=request).json()["recovery"]["state"] == "blocked"
        monkeypatch.setattr("hrs.library.runs.recovery.preflight", lambda *_: None)
        assert client.post(url, json={"request_id": str(uuid4())}).status_code == 200
        with engine.connect() as connection:
            event = dict(
                connection.execute(select(db.outbox).where(db.outbox.c.delivered.is_(False)))
                .mappings()
                .one()
            )
        bridge.dispatch(event)
        assert bridge.store.status(identity)["run_request"]["state"] == "pending"
        assert client.get(f"/api/v2/runs/{run}/recovery").json()["state"] == "resumed"

    # A dispatcher restart after native commit must replay its receipt, even if login is now offline.
    monkeypatch.setattr("hrs.library.runs.recovery.preflight", offline)
    bridge.dispatch(event)
    assert get_run(engine, run)["state"] == "queued"
    assert len(bridge.store.status(identity)["recoveries"]) == 1


def test_native_intake_failure_retries_saved_pdf_before_a_document_id_exists(platform, tmp_path):
    from io import BytesIO

    from pypdf import PdfWriter

    from hrs.library.books import get_run
    from hrs.library.processing_bridge import CodexBridge
    from hrs.storage.service import objects_for

    settings, engine = platform
    settings = settings.model_copy(update={"codex_root": tmp_path / "intake-native"})
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=300)
    content = BytesIO()
    writer.write(content)
    source = objects_for(settings, engine).put_bytes(content.getvalue(), "application/pdf")
    book, run = str(uuid4()), str(uuid4())
    with engine.begin() as connection:
        connection.execute(
            insert(db.books).values(id=book, title="原件准备恢复技术夹具", state="failed")
        )
        connection.execute(
            insert(db.runs).values(
                id=run,
                book_id=book,
                state="failed",
                stage="verify_upload",
                source=source,
                result={
                    "processing_backend": "codex",
                    "bridge_failures": 1,
                    "bridge_not_before": 9999999999,
                },
                error={"code": "codex_bridge", "detail": "synthetic failed intake"},
            )
        )
    request = {"request_id": str(uuid4())}
    with TestClient(create_app(settings, engine)) as client:
        report = client.get(f"/api/v2/runs/{run}/recovery").raise_for_status().json()
        assert report["state"] == "available" and report["preserved_ocr"] == 0
        assert "原件准备" in report["action"]
        assert client.post(f"/api/v2/runs/{run}/retry", json=request).status_code == 200
        bridge = CodexBridge(settings, engine)
        bridge.tick()
        native_id = get_run(engine, run)["result"]["codex_document_id"]
        assert bridge.store.status(native_id)["run_request"]["state"] == "pending"
        assert get_run(engine, run)["source"]["sha256"] == source["sha256"]
        bridge.tick()
        assert len(bridge.store.list_documents()) == 1
        assert client.post(f"/api/v2/runs/{run}/retry", json=request).status_code == 200
        with engine.connect() as connection:
            assert connection.scalar(select(func.count()).select_from(db.outbox)) == 1
            assert connection.scalar(select(db.outbox.c.delivered)) is True


def test_retired_intake_requires_explicit_import_instead_of_creating_native_work(platform):
    settings, engine = platform
    book, run = str(uuid4()), str(uuid4())
    with engine.begin() as connection:
        connection.execute(insert(db.books).values(id=book, title="旧资料技术夹具", state="failed"))
        connection.execute(
            insert(db.runs).values(id=run, book_id=book, state="failed", stage="conversion")
        )
    with TestClient(create_app(settings, engine)) as client:
        report = client.get(f"/api/v2/runs/{run}/recovery").raise_for_status().json()
        assert report["state"] == "blocked" and "旧执行链已退役" in report["reason"]
        response = client.post(f"/api/v2/runs/{run}/retry", json={"request_id": str(uuid4())})
        assert response.status_code == 409 and "原生处理流程" in response.json()["detail"]
    with engine.connect() as connection:
        assert connection.execute(select(db.outbox)).first() is None
        assert connection.scalar(select(db.runs.c.state).where(db.runs.c.id == run)) == "failed"
