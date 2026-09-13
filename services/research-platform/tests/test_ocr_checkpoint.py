import shutil
from uuid import uuid4

from sqlalchemy import insert

from hrs_platform import schema as db
from hrs_platform.activities import Activities


def test_committed_ocr_is_restored_from_s3_without_another_recognizer_call(platform, tmp_path, monkeypatch):
    settings, engine = platform
    book, run = str(uuid4()), str(uuid4())
    with engine.begin() as connection:
        connection.execute(insert(db.books).values(id=book, title="OCR checkpoint technical test", state="processing"))
        connection.execute(insert(db.runs).values(id=run, book_id=book, state="processing", stage="conversion", source={}))
    source = tmp_path / "source.pdf"
    source.write_bytes(b"synthetic source; not PDF recognition evidence")
    output = tmp_path / "conversion"
    calls = []
    def extract(*args, phase):
        calls.append(phase)
        output.mkdir()
        (output / "ocr-checkpoint.json").write_bytes(b'{"technical_test":true}')
        (output / "raw.md").write_text("原始识别内容", encoding="utf-8")
    monkeypatch.setattr("hrs_platform.activities.activity.heartbeat", lambda *_: None)
    activities = Activities(settings, engine)
    monkeypatch.setattr(activities, "_extract", extract)
    activities._ocr_checkpoint(run, source, output)
    shutil.rmtree(output)
    activities._ocr_checkpoint(run, source, output)
    assert calls == ["ocr"]
    assert (output / "raw.md").read_text("utf-8") == "原始识别内容"
