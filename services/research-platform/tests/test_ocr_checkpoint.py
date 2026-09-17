import shutil
import json
from uuid import uuid4

from sqlalchemy import insert

from hrs_platform import models as db
from hrs_platform.jobs.conversion import Activities


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
        output.mkdir(exist_ok=True)
        (output / "ocr-checkpoint.json").write_bytes(b'{"technical_test":true}')
        (output / "raw.md").write_text("原始识别内容", encoding="utf-8")
    monkeypatch.setattr("hrs_platform.jobs.conversion.activity.heartbeat", lambda *_: None)
    activities = Activities(settings, engine)
    monkeypatch.setattr(activities, "_extract", extract)
    activities._ocr_checkpoint(run, source, output)
    shutil.rmtree(output)
    activities._ocr_checkpoint(run, source, output)
    assert calls == ["ocr"]
    assert (output / "raw.md").read_text("utf-8") == "原始识别内容"


def test_conversion_configuration_is_frozen_before_first_attempt(platform, tmp_path, monkeypatch):
    import pytest
    from hrs_platform.services.runs.outputs import Outputs

    settings, engine = platform
    settings = settings.model_copy(update={'project_root': tmp_path})
    config = tmp_path / 'services/document-extraction/config/default.json'
    config.parent.mkdir(parents=True)
    original = {'vision_review': {'review_mode': 'full'}, 'native_pdf': {'policy': 'native-pdf-simple-text-v1'}}
    config.write_text(json.dumps(original))
    book, run = str(uuid4()), str(uuid4())
    with engine.begin() as connection:
        connection.execute(insert(db.books).values(id=book, title='frozen-config fixture', state='processing'))
        connection.execute(insert(db.runs).values(id=run, book_id=book, state='processing', stage='conversion', source={}))
    source = tmp_path / 'source.pdf'
    source.write_bytes(b'synthetic immutable source')
    output = tmp_path / 'out'
    activity = Activities(settings, engine)
    observed = []
    def fail(*_, phase):
        observed.append(json.loads((output / 'conversion-config.json').read_text('utf-8')))
        raise RuntimeError('simulated process failure before extraction commit')
    monkeypatch.setattr(activity, '_extract', fail)
    for changed in (original, {'vision_review': {'review_mode': 'full'}}):
        config.write_text(json.dumps(changed))
        with pytest.raises(RuntimeError, match='simulated'):
            activity._ocr_checkpoint(run, source, output)
    assert observed == [original, original]
    assert Outputs(settings, engine).get(run, 'conversion-config') == original


def test_native_and_visual_progress_are_counted_once_without_releasing(platform, tmp_path, monkeypatch):
    from unittest.mock import Mock
    from hrs_platform.services.runs.lifecycle import RunLifecycle

    settings, engine = platform
    output = tmp_path / 'out'
    (output / 'reviews').mkdir(parents=True)
    (output / 'conversion-config.json').write_text(json.dumps({'vision_review': {'review_mode': 'full'}}))
    (output / 'ocr-checkpoint.json').write_text(json.dumps({'pages': [{'page': 1}, {'page': 2}]}))
    (output / 'review-routing.json').write_text(json.dumps({'decisions': [{'page': 1, 'route': 'native-pass'}, {'page': 2, 'route': 'deepseek'}]}))
    (output / 'reviews/completion-002-initial.json').write_text(json.dumps({'phase': 'initial', 'verdict': {'review_state': 'completed'}}))
    process = Mock(returncode=0)
    process.poll.return_value = 0
    monkeypatch.setattr('hrs_platform.jobs.conversion.subprocess.Popen', lambda *_, **__: process)
    progress = Mock()
    monkeypatch.setattr(RunLifecycle, 'report_progress', progress)
    Activities(settings, engine)._extract('synthetic-run', tmp_path / 'source.pdf', output, phase='review')
    progress.assert_called_once_with('synthetic-run', 2, 2)
