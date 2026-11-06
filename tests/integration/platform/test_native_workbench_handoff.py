"""A chat-imported source joins the existing platform without another import or run request."""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import func, select, update

from hrs.api.main import create_app
from hrs.library.books import get_run
from hrs.processing.application import prepare
from hrs.processing.store import Store
from hrs.processing.workspace import identity
from hrs.storage import models as db


def native_source(settings, tmp_path):
    settings = settings.model_copy(update={"codex_root": tmp_path / "native"})
    source = tmp_path / "original.png"
    Image.new("RGB", (40, 50), "white").save(source)
    store = Store(settings.codex_root)
    document = prepare(store, str(source), "Chat source")
    request = store.request_run(document["id"])
    payload = {"document_id": document["id"], "workspace_id": identity(store)["workspace_id"]}
    return settings, store, document, request, payload


def test_native_handoff_is_idempotent_and_does_not_repeat_processing(platform, tmp_path):
    settings, engine = platform
    settings, store, document, request, payload = native_source(settings, tmp_path)
    with TestClient(create_app(settings, engine)) as client:
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(
                pool.map(lambda _: client.post("/api/v2/native-workbench", json=payload), range(2))
            )
        assert all(response.status_code == 201 for response in responses)
        first, second = [response.json() for response in responses]
        assert first == second
        assert first["workspace_id"] == payload["workspace_id"]
        assert first["document_id"] == document["id"]
        assert first["url"] == f"/#/books/{first['book_id']}?tab=progress"
        with engine.connect() as connection:
            assert connection.scalar(select(func.count()).select_from(db.books)) == 1
            assert connection.scalar(select(func.count()).select_from(db.runs)) == 1
            assert connection.scalar(select(func.count()).select_from(db.outbox)) == 1
        assert store.status(document["id"])["run_request"] == request
        from hrs.library.processing_bridge import CodexBridge

        bridge = CodexBridge(settings, engine)
        with engine.connect() as connection:
            event = dict(connection.execute(select(db.outbox)).mappings().one())
        bridge.dispatch(event)
        run = get_run(engine, first["run_id"])
        assert run["result"]["codex_document_id"] == document["id"]
        assert run["source"]["sha256"] == document["source"]["sha256"]
        assert store.status(document["id"])["run_request"] == request
        assert client.post("/api/v2/native-workbench", json=payload).json() == first


def test_native_handoff_rejects_another_workspace_and_cancelled_documents(platform, tmp_path):
    settings, engine = platform
    settings, store, document, _, payload = native_source(settings, tmp_path)
    with TestClient(create_app(settings, engine)) as client:
        assert (
            client.post(
                "/api/v2/native-workbench", json={**payload, "workspace_id": "0" * 32}
            ).status_code
            == 409
        )
        store.cancel_document(document["id"])
        assert client.post("/api/v2/native-workbench", json=payload).status_code == 409
        with engine.connect() as connection:
            assert connection.scalar(select(func.count()).select_from(db.books)) == 0


def test_native_handoff_storage_failure_recovers_through_the_normal_retry(
    platform, tmp_path, monkeypatch
):
    settings, engine = platform
    settings, store, document, request, payload = native_source(settings, tmp_path)
    from hrs.library.processing_bridge import CodexBridge

    bridge = CodexBridge(settings, engine)
    original = bridge.objects.put_file
    with TestClient(create_app(settings, engine)) as client:
        attached = client.post("/api/v2/native-workbench", json=payload).json()

        def fail(*args, **kwargs):
            raise OSError("Synthetic S3 interruption")

        monkeypatch.setattr(bridge.objects, "put_file", fail)
        bridge.tick()
        failed = get_run(engine, attached["run_id"])
        assert failed["state"] == "failed"
        assert failed["result"]["native_attach_document_id"] == document["id"]
        assert not failed["result"].get("codex_document_id")
        assert store.status(document["id"])["run_request"] == request
        monkeypatch.setattr(bridge.objects, "put_file", original)
        response = client.post(
            f"/api/v2/runs/{attached['run_id']}/retry", json={"request_id": str(uuid4())}
        )
        assert response.status_code == 200, response.text
        bridge.tick()
        recovered = get_run(engine, attached["run_id"])
        assert recovered["result"]["codex_document_id"] == document["id"]
        assert recovered["source"]["sha256"] == document["source"]["sha256"]
        assert store.status(document["id"])["run_request"] == request
        assert client.post("/api/v2/native-workbench", json=payload).json() == attached


def test_native_handoff_adopts_legacy_mapping_only_when_its_retained_source_matches(
    platform, tmp_path
):
    settings, engine = platform
    settings, store, document, _, payload = native_source(settings, tmp_path)
    from hrs.library.processing_bridge import CodexBridge

    with TestClient(create_app(settings, engine)) as client:
        attached = client.post("/api/v2/native-workbench", json=payload).json()
        CodexBridge(settings, engine).tick()
        run = get_run(engine, attached["run_id"])
        legacy = {key: value for key, value in run["result"].items() if key != "codex_workspace_id"}
        with engine.begin() as connection:
            connection.execute(
                update(db.runs).where(db.runs.c.id == attached["run_id"]).values(result=legacy)
            )
        assert client.post("/api/v2/native-workbench", json=payload).json() == attached
        assert (
            get_run(engine, attached["run_id"])["result"]["codex_workspace_id"]
            == identity(store)["workspace_id"]
        )
        with engine.begin() as connection:
            connection.execute(
                update(db.runs)
                .where(db.runs.c.id == attached["run_id"])
                .values(result=legacy, source={**run["source"], "sha256": "0" * 64})
            )
        assert client.post("/api/v2/native-workbench", json=payload).status_code == 409
        with engine.connect() as connection:
            assert connection.scalar(select(func.count()).select_from(db.runs)) == 1
            assert connection.scalar(select(func.count()).select_from(db.books)) == 1
        assert "codex_workspace_id" not in get_run(engine, attached["run_id"])["result"]
        assert store.status(document["id"])["source"] == document["source"]


def test_native_handoff_cannot_recreate_a_deleted_reading_projection(platform, tmp_path):
    settings, engine = platform
    settings, _, _, _, payload = native_source(settings, tmp_path)
    with TestClient(create_app(settings, engine)) as client:
        attached = client.post("/api/v2/native-workbench", json=payload).json()
        with engine.begin() as connection:
            connection.execute(
                update(db.books).where(db.books.c.id == attached["book_id"]).values(state="deleted")
            )
        assert client.post("/api/v2/native-workbench", json=payload).status_code == 409
        with engine.connect() as connection:
            assert connection.scalar(select(func.count()).select_from(db.books)) == 1
