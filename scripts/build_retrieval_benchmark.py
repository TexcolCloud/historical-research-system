"""Create 100 synthetic retrieval cases; never historical or human-approved gold.

The output deliberately has no development approval. Review rendered originals
and attach evidence before using evaluate-offline. No model or database needed.
"""

import argparse
import json
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5


def build():
    book = str(uuid5(NAMESPACE_URL, 'hrs:synthetic-retrieval-v1'))
    chapters, cases = [], []

    def chapter(title, text, page):
        identity = str(uuid5(NAMESPACE_URL, book + title))
        value = {'id': identity, 'book_id': book, 'run_id': book, 'title': title, 'text': text,
                 'parts': [{'span_id': f'synthetic-{page}', 'start': 0, 'text': text,
                            'source': {'pages': [page], 'synthetic': True}}], 'development_review': {}}
        chapters.append(value)
        return value

    def evidence(source, text):
        start = source['text'].index(text)
        return {'chapter_id': source['id'], 'start': start, 'end': start + len(text)}

    for i, place in enumerate(['青河', '白石', '松林', '南溪', '北岭', '东原', '西泉', '柳湾', '竹山', '沙湖']):
        year, quantity, officer = 1930 + i, 120 + i * 13, '记录员' + '甲乙丙丁戊己庚辛壬癸'[i]
        heading = f'{place}县{year}年运输登记'
        body = f'{year}年，{place}县运输登记由{officer}负责。登记对象为县内粮食运输，未包含邻县。'
        table = f'| 地点 | 登记数量 |\n| --- | --- |\n| 县城 | {quantity} |\n| 河口 | {quantity + 20} |\n'
        note = '单位：吨。\n\n登记范围另见说明[^scope]。\n\n[^scope]: 数量不包括军用运输，也不包括仓库结存。'
        source = chapter(heading, f'# {heading}\n\n{body}\n\n{table}\n{note}\n', i * 4 + 1)
        neighbor_body = f'{year}年，{place}邻县的县城登记粮食运输{quantity + 50}吨；此数单独核算，未并入{place}县。'
        neighbor = chapter(heading + '·邻县', neighbor_body, i * 4 + 2)
        chapter(heading + '·次年', f'{year + 1}年，{place}县县城运输为{quantity + 90}吨，由另一名记录员负责。', i * 4 + 3)
        chapter(heading + '·军用', f'{year}年，{place}县军用运输登记{quantity + 200}吨，不计入县内粮食运输表。', i * 4 + 4)
        def add(query, category, expected):
            cases.append({'query': query, 'category': category, 'expected': expected})
        add(f'{place}县{year}年的粮食运输登记由谁负责？', 'person', [evidence(source, body)])
        add(f'{officer}负责的是哪个县哪一年的登记？', 'place_year', [evidence(source, body)])
        add(f'{place}县{year}年县城登记粮食运输多少？请给出单位。', 'table', [evidence(source, f'| 县城 | {quantity} |'), evidence(source, '单位：吨。')])
        add(f'{place}县{year}年河口登记粮食运输多少吨？', 'table', [evidence(source, f'| 河口 | {quantity + 20} |')])
        add(f'{place}县{year}年的运输登记是否包括军用运输？', 'footnote', [evidence(source, '[^scope]: 数量不包括军用运输，也不包括仓库结存。')])
        add(f'{place}县{year}年的粮食运输表涵盖哪些地点，计量单位是什么？', 'table_context', [evidence(source, table), evidence(source, '单位：吨。')])
        add(f'比较{year}年{place}县与其邻县的县城粮食运输数量。', 'cross_chapter', [evidence(source, f'| 县城 | {quantity} |'), evidence(neighbor, neighbor_body)])
        add(f'{place}县{year}年的运输数量是否包含仓库结存？', 'negation', [evidence(source, '[^scope]: 数量不包括军用运输，也不包括仓库结存。')])
        add(f'负责{place}县{year}年登记的{officer}出生在哪里？', 'unanswerable', [])
        add(f'{place}县{year}年运输途中损失了多少粮食？', 'unanswerable', [])
    return {'title': '合成检索回归资料（非史料）', 'synthetic': True, 'chapters': chapters, 'cases': cases,
            'variants': [['lexical', False, {}], ['hybrid_30', True, {}],
                         ['hybrid_50', True, {'rerank_limit': 50}], ['hybrid_reserved', True, {'lane_quota': 3}]]}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(build(), ensure_ascii=False, indent=2), encoding='utf-8')
