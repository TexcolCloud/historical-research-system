import json

from fastapi.testclient import TestClient
from sqlalchemy import select, update
from test_review import seed
from test_structure_views import table

from hrs_platform import models as db
from hrs_platform.main import create_app
from hrs_platform.services.exports import Exports
from hrs_platform.services.library import Library


def seed_structure_book(settings, engine):
    run, issues = seed(engine)
    library = Library(settings, engine)
    objects = library.review.objects

    def saved(value):
        return objects.put_bytes(json.dumps(value, ensure_ascii=False).encode())

    first, second = table('甲县'), table('乙县')
    values = ['# 第一章 运输统计\n\n单位：吨\n\n' + first, second + '\n\n另见注释[^a]。\n\n[^a]: 只计本月。']
    pages, offset = [], 0
    for n, value in enumerate(values, 1):
        pages.append({'page': n, 'start': offset, 'end': offset + len(value)})
        offset += len(value) + 2
    files = {
        'document.md': objects.put_bytes('\n\n'.join(values).encode()),
        'pages.json': saved({'pages': pages}),
        'completion.json': saved({'pages': [{'page': n} for n in (1, 2)]}),
        'paddle-restructure.json': saved({'tables': [{'pages': [1, 2],
            'merged_html': first.replace('</table>', '<tr><td>乙县</td><td>120</td></tr></table>'),
            'members': [{'page': 1, 'original_html': first}, {'page': 2, 'original_html': second}]}],
            'titles': [{'page': 1, 'title': '第一章 运输统计', 'level': 1, 'before': '## 第一章 运输统计', 'after': '# 第一章 运输统计'}]}),
    }
    bundle = {'manifest': {'candidate_markdown': 'document.md', 'page_boundaries': 'pages.json', 'page_count': 2,
                          'pages': [{'page': n, 'status': 'pending-human-review'} for n in (1, 2)]}, 'files': files}
    with engine.begin() as connection:
        connection.execute(update(db.runs).where(db.runs.c.id == run).values(
            source=objects.put_bytes(b'synthetic source'), conversion=saved(bundle)))
        for issue, page, value in zip(issues, pages, values, strict=True):
            connection.execute(update(db.review_issues).where(db.review_issues.c.id == issue).values(
                content=saved({'text': value, 'pages': [page['page']], 'start': page['start'], 'end': page['end']})))
    return run, library


def test_structure_read_api_cache_and_published_reader_share_checked_sources(platform):
    settings, engine = platform
    run, library = seed_structure_book(settings, engine)
    client = TestClient(create_app(settings, engine))
    pending = client.get(f'/api/v2/runs/{run}/structure')
    assert pending.status_code == 200
    assert all(i['status'] == 'retained' for i in pending.json()['items'])
    with engine.begin() as connection:
        connection.execute(update(db.review_issues).where(db.review_issues.c.run_id == run).values(state='approved'))
        connection.execute(update(db.runs).where(db.runs.c.id == run).values(pending_count=0, revision=2))
    ready = client.get(f'/api/v2/runs/{run}/structure').json()
    assert all(i['status'] == 'ready' for i in ready['items'])
    assert client.get(f'/api/v2/runs/{run}/structure').json() == ready
    source = library.prepare(run)
    parts = [{'span_id': s['id'], 'start': s['start_offset'], 'end': s['end_offset'],
              'text': s['selected_text'], 'source': s['source_record']} for s in source['spans']]
    library.outputs.put(run, 'organization', {'groups': [{'title': '第一章 运输统计', 'kind': 'article', 'parts': parts}]}, source)
    published = library.publish(run)
    chapter_id = library.chapters(published['book_id'])[0]['id']
    response = client.get(f'/api/v2/chapters/{chapter_id}')
    assert response.status_code == 200
    chapter = response.json()
    assert chapter['text'].count('<table>') == 2
    assert chapter['reading_text'].count('<table>') == 1
    assert len(chapter['reading_footnotes']) == 1
    assert chapter['structure'][0]['pages'] == [1, 2]
    title, exported = Exports(settings, engine).book(published['book_id'])
    assert title
    assert library.review.objects.read_bytes(exported).decode() == chapter['reading_text']
    with engine.connect() as connection:
        saved_steps = connection.scalars(select(db.stage_outputs.c.step).where(db.stage_outputs.c.run_id == run)).all()
    assert sum(step.startswith('reading-structure:') for step in saved_steps) == 2


def test_confirm_without_replacement_and_correction_both_prepare_for_ingestion(platform):
    from uuid import uuid4

    from hrs_platform.schemas import ReviewDecision
    from hrs_platform.services.review import digest

    settings, engine = platform
    run, library = seed_structure_book(settings, engine)
    issues = library.review.list(run)
    for index, issue in enumerate(issues):
        current = {**issue, **library.review.read_json(issue['content'])}
        changed = current['text'].replace('乙县', '丙县') if index else None
        library.review.decide(issue['id'], ReviewDecision(
            decision_id=uuid4(), expected_revision=current['revision'],
            expected_text_sha256=digest('unchanged'),
            action='correct' if index else 'confirm', text=changed))
    assert library.review.status(run)['pending_count'] == 0
    prepared = library.prepare(run)
    text = ''.join(span['selected_text'] for span in prepared['spans'])
    assert '甲县' in text and '丙县' in text and '乙县' not in text
