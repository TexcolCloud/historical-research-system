"""Paddle document-level structure with immutable physical-page editing sources."""
import copy
import re
from collections import defaultdict

from .utils import sha256, write_json
from .provenance import text_hash


def restructure_document(pages, output, restructure):
    originals = []
    for page in pages:
        results = (page.get('ocr_evidence', {}).get('metadata') or {}).get('paddle_results', [])
        if len(results) != 1:
            raise ValueError('Paddle restructuring requires one retained provider result per physical page')
        originals.append(results[0])
    results = list(restructure([{'res':copy.deepcopy(r)} for r in originals],
        merge_tables=True, relevel_titles=True, concatenate_pages=False))
    if len(results) != len(pages):
        raise ValueError('Paddle restructuring changed physical page count')
    groups = defaultdict(list)
    titles, returned, identity = [], [], 0
    for page, original, result in zip(pages, originals, results):
        blocks = result['parsing_res_list']
        if len(blocks) != len(original['parsing_res_list']):
            raise ValueError('Paddle restructuring changed block identity; original pages retained')
        page['restructure_evidence'] = {'artifact':'paddle-restructure.json', 'tables':[], 'titles':[]}
        returned.append(result.json['res'])
        for source_block, block in zip(original['parsing_res_list'], blocks):
            if block.global_block_id != identity:
                raise ValueError('Paddle global block identity mismatch')
            if block.label == 'table':
                groups[block.global_group_id].append({'page':page['page'], 'global_block_id':identity,
                    'bbox':source_block['block_bbox'], 'original_html':source_block['block_content'],
                    'original_html_sha256':text_hash(source_block['block_content']),
                    'source_image_sha256':sha256(page['image_path']), 'candidate_html':block.content})
            if block.label == 'paragraph_title' and type(getattr(block, 'title_level', None)) is int:
                level = max(1, min(6, block.title_level + 1))
                title = re.sub(r'^#+\s', '', source_block['block_content']).strip()
                matches = list(re.finditer(r'^#{1,6}[ \t]+' + re.escape(title) + r'[ \t]*$', page['text'], re.M))
                evidence = {'page':page['page'], 'global_block_id':identity, 'title':title,
                    'level':level, 'applied':len(matches) == 1, 'basis':'paddle-title-hierarchy-not-article-identity'}
                if len(matches) == 1:
                    start, end = matches[0].span()
                    evidence['before'] = page['text'][start:end]
                    evidence['after'] = '#' * level + ' ' + title
                    page['text'] = page['text'][:start] + evidence['after'] + page['text'][end:]
                titles.append(evidence)
                page['restructure_evidence']['titles'].append(evidence)
            identity += 1
    tables = []
    for group_id, members in groups.items():
        page_numbers = sorted({m['page'] for m in members})
        if len(page_numbers) < 2:
            continue
        heads = [m for m in members if m['candidate_html'].strip()]
        if len(heads) != 1:
            raise ValueError('Cross-page table has ambiguous merged content')
        group = {'group_id':f'paddle-table-{group_id}', 'pages':page_numbers,
            'merged_html':heads[0]['candidate_html'],
            'page_text_sha256':{str(p['page']):text_hash(p['text']) for p in pages if p['page'] in page_numbers},
            'qualification':'structural-candidate-not-release-approval',
            'members':[{k:v for k,v in m.items() if k != 'candidate_html'} for m in members]}
        tables.append(group)
        for page in pages:
            if page['page'] in page_numbers:
                page['restructure_evidence']['tables'].append({'group_id':group['group_id'],
                    'pages':page_numbers, 'qualification':group['qualification']})
    for page in pages:
        page['restructure_evidence']['source_text_sha256'] = text_hash(page['text'])
    report = {'policy':'paddle-document-restructure-v1', 'status':'completed',
        'format_block_content':True, 'merge_tables':True, 'relevel_titles':True, 'concatenate_pages':False,
        'page_count':len(pages), 'merged_table_count':len(tables),
        'applied_titles':sum(t['applied'] for t in titles), 'unmatched_titles':sum(not t['applied'] for t in titles)}
    write_json(output/'paddle-restructure.json', {**report, 'titles':titles, 'tables':tables,
        'provider_results':returned, 'scope':'Derived structure; physical tables remain in canonical Markdown'})
    return report
