import json
from types import SimpleNamespace

from pydantic import SecretStr

from hrs_platform.services import chapter_content as module


def settings(bucket='one'):
    return SimpleNamespace(s3_endpoint='https://s3.invalid', s3_bucket=bucket, s3_region='test',
        s3_access_key=SecretStr('test'), s3_secret_key=SecretStr('secret'))


def test_content_cache_is_shared_by_reference_but_never_shares_mutable_results(monkeypatch):
    calls = []
    def read(storage, reference):
        calls.append((storage, reference))
        return {'parts': [{'text': json.loads(reference)['sha256']}], 'text': 'immutable'}
    monkeypatch.setattr(module, 'read_content', read)
    module.cached_content.cache_clear()
    ref = {'sha256': 'a', 'byte_length': 3}
    first = module.chapter_content(settings(), ref)
    first['parts'][0]['text'] = 'caller modification'
    assert module.chapter_content(settings(), dict(ref))['parts'][0]['text'] == 'a'
    assert len(calls) == 1
    module.chapter_content(settings(), {**ref, 'sha256': 'b'})
    module.chapter_content(settings('two'), ref)
    assert len(calls) == 3
    assert module.cached_content.cache_info().maxsize == 16
    module.cached_content.cache_clear()


def test_oversized_content_is_not_retained(monkeypatch):
    calls = []
    monkeypatch.setattr(module, 'read_content', lambda *a: calls.append(a) or {'text': 'large'})
    module.cached_content.cache_clear()
    for _ in range(2):
        assert module.chapter_content(settings(), {'byte_length':module.MAX_CACHED_BYTES + 1})['text'] == 'large'
    assert len(calls) == 2 and module.cached_content.cache_info().currsize == 0


def test_cached_chapter_still_checks_current_sql_reference_and_deletion(platform):
    from uuid import uuid4

    import pytest
    from fastapi import HTTPException
    from sqlalchemy import delete, insert, update

    from hrs_platform import models as db
    from hrs_platform.services.library import Library

    settings, engine = platform
    library = Library(settings, engine)
    book, run, chapter = [str(uuid4()) for _ in range(3)]
    def save(text):
        return library.review.objects.put_bytes(json.dumps({'parts':[{'span_id':'p','start':0,
            'text':text,'source':{'pages':[1]}}]}).encode())
    first, second = save('原文①。\n\n① 原注。'), save('修改后的正文。')
    with engine.begin() as conn:
        conn.execute(insert(db.books).values(id=book,title='样本',state='ready'))
        conn.execute(insert(db.runs).values(id=run,book_id=book,state='completed',stage='complete'))
        conn.execute(insert(db.chapters).values(id=chapter,book_id=book,run_id=run,position=0,
            title='章',kind='article',content=first,pages=[1],codepoints=10))
    assert library.chapter(chapter)['footnotes']
    assert Library(settings,engine).chapter(chapter)['text'].startswith('原文')
    with engine.begin() as conn:
        conn.execute(update(db.chapters).where(db.chapters.c.id==chapter).values(content=second,title='新标题'))
    assert library.chapter(chapter)['text']=='修改后的正文。'
    assert library.chapter(chapter)['title']=='新标题'
    with engine.begin() as conn:
        conn.execute(delete(db.chapters).where(db.chapters.c.id==chapter))
    with pytest.raises(HTTPException) as error:
        library.chapter(chapter)
    assert error.value.status_code==404
