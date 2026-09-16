from uuid import uuid4

import pytest

from hrs_platform.domain.book_structure import validate_outline_boundaries
from hrs_platform.services.library import reviewed_spans


def test_corrected_span_keeps_both_original_pages_and_continuation_whitespace():
    source = "第一章\n原文甲\n\n原文乙\n第二章"
    pages = [{"page": 1, "start": 0, "end": 8}, {"page": 2, "start": 9, "end": len(source)}]
    spans = reviewed_spans(source, pages, [], str(uuid4()))
    assert "".join(row["selected_text"] for row in spans) == source
    corrected = reviewed_spans(
        source,
        pages,
        [{"start": 6, "end": 12, "text": "校正后的文字", "decision_id": "technical-test"}],
        str(uuid4()),
    )
    assert "".join(row["selected_text"] for row in corrected) == source[:6] + "校正后的文字" + source[12:]
    edited = next(row for row in corrected if row["source_record"]["human_decision_id"])
    assert edited["source_record"]["pages"] == [1, 2]


def test_existing_chapter_rule_rejects_a_subsection_as_new_logical_page():
    outline = {
        "mode": "chaptered",
        "items": [{"title": "第一章", "role": "chapter"}, {"title": "第二章", "role": "chapter"}],
    }
    previous = [{"kind": "article", "title": "第一章"}]
    with pytest.raises(ValueError):
        validate_outline_boundaries(outline, previous, [{"kind": "article", "title": "一、行政组织"}])
    validate_outline_boundaries(outline, previous, [])


def test_machine_correction_provenance_is_never_labeled_human():
    rows = reviewed_spans('原文。', [{'page':1,'start':0,'end':3}],
                          [{'start':0,'end':3,'text':'修正。','decision_id':'machine-id','reviewer':'local-qwen-machine'}], str(uuid4()))
    record = rows[0]['source_record']
    assert record['machine_decision_id']=='machine-id'
    assert record['human_decision_id'] is None


@pytest.mark.parametrize('bad', ['changed', 'length', 'human', 'overlap', 'duplicate', 'negative-offset'])
def test_published_errata_reject_unsafe_changes_before_visual_review(monkeypatch, bad):
    from copy import deepcopy
    from types import SimpleNamespace

    from hrs_platform.services import library as module
    source = {'id':'chapter','content':{'sha256':'current'},'text':'甲地。',
              'parts':[{'text':'甲地。','source':{'pages':[1]}}]}
    if bad == 'human':
        source['parts'][0]['source']['human_decision_id']='explicit-human-edit'
    library = object.__new__(module.Library)
    library.engine=None
    library.outputs=SimpleNamespace(get=lambda *_:None)
    library.chapters=lambda _: [{'id':'chapter'}]
    library.chapter=lambda _:deepcopy(source)
    library.review=SimpleNamespace(read_json=lambda _:deepcopy(source))
    monkeypatch.setattr(module,'get_run',lambda *_:{'result':{'published':True},'pending_count':0,
                                                 'conversion':{},'book_id':'book'})
    monkeypatch.setattr('subprocess.run',lambda *a,**k:pytest.fail('Invalid errata reached the vision runtime'))
    edit={'start':-1 if bad=='negative-offset' else 0,'before':'甲','after':'乙地' if bad=='length' else '乙'}
    request={'chapter_id':'chapter','expected_content_sha256':'old' if bad=='changed' else 'current',
             'changes':[edit,edit] if bad=='overlap' else [edit]}
    with pytest.raises(ValueError):
        library.amend('run',[request, request] if bad=='duplicate' else [request])


@pytest.mark.parametrize('accepted',[True,False])
def test_verified_published_errata_commit_once_and_invalidate_old_search(platform, monkeypatch, tmp_path, accepted):
    import json
    from pathlib import Path
    from types import SimpleNamespace

    from sqlalchemy import insert, select, update
    from test_review import seed

    from hrs_platform import models as db
    from hrs_platform.services.books import get_run
    from hrs_platform.services.library import Library

    settings, engine = platform
    library=Library(settings.model_copy(update={'cache_root':tmp_path}),engine)
    run, _ = seed(engine)
    book=get_run(engine,run)['book_id']
    chapter=str(uuid4())
    original=library.review.objects.put_bytes(json.dumps({'parts':[
        {'span_id':'s','start':0,'text':'甲地。','source':{'pages':[1]}}]}).encode())
    image=library.review.objects.put_bytes(b'technical image transport fixture')
    conversion=library.review.objects.put_bytes(json.dumps({'files':{'page.png':image},
        'manifest':{'pages':[{'page':1,'image':'page.png'}]}}).encode())
    with engine.begin() as conn:
        conn.execute(update(db.runs).where(db.runs.c.id==run).values(pending_count=0,conversion=conversion,
            result={'published':True,'retrieval_generation':'old-generation'}))
        conn.execute(insert(db.chapters).values(id=chapter,book_id=book,run_id=run,position=0,
            title='技术样本',kind='chapter',pages=[1],codepoints=3,content=original))
    calls=[]
    def review(command,**kwargs):
        calls.append(command)
        page=json.loads(Path(command[3]).read_text('utf-8'))[0]
        assert page['text']=='乙地。' and page['review_scope'][0]['text']=='乙'
        page.update(verified=accepted,input_text_sha256='technical-input',final_text_sha256='technical-output')
        Path(command[-1]).write_text(json.dumps({'pages':[page],'reviewer':'technical-local-vision-stub'}),'utf-8')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr('subprocess.run',review)
    request=[{'chapter_id':chapter,'expected_content_sha256':original['sha256'],
              'changes':[{'start':0,'before':'甲','after':'乙'}]}]
    if accepted:
        receipt=library.amend(run,request)
        assert library.amend(run,request)==receipt and len(calls)==1
        assert library.chapter(chapter)['text']=='乙地。'
        assert get_run(engine,run)['result']['retrieval_generation'].startswith('pending-amendment-')
        assert library.review.read_json(original)['parts'][0]['text']=='甲地。'
        with engine.connect() as conn:
            assert conn.scalar(select(db.events.c.kind).where(db.events.c.run_id==run))=='library.revised'
    else:
        with pytest.raises(ValueError,match='source unchanged'):
            library.amend(run,request)
        assert library.chapter(chapter)['text']=='甲地。'
        assert get_run(engine,run)['result']['retrieval_generation']=='old-generation'
