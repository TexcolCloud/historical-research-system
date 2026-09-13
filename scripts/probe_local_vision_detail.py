"""Isolated development probe of source-first local review; no application writes."""
import base64
import io
import json
from pathlib import Path
import sys
import time
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'services/runtime-support/src'))
from hrs_runtime.local_vision import chat

source = ROOT / 'state/workbench-conversion/761a23d9-4a60-4fcd-8431-217b691f7b26/ocr-checkpoint'
folder = ROOT / 'output/local-vision-20260912'
for number in (22, 170):
    parts = [{'type': 'text', 'text': '只看原图，独立读取其中所有含阿拉伯数字的文字行和完整表格；保留日期、数值、单位和行列归属。不要根据历史知识修正印刷值。无法辨认写[不清]。返回JSON {"numeric_lines":["原图实际文字行"],"tables":["完整表格Markdown"],"unclear":["无法读清的位置"]}。图片依次为同一物理页的完整图及三个有重叠的全宽局部，只读取一次，不重复拼接。'}]
    with Image.open(source / f'pages/page-{number:03d}.png') as im:
        width, height = im.size
        boxes = [(0, 0, width, height)] + [(0, max(0, int(height*i/3)-100), width, min(height, int(height*(i+1)/3)+100)) for i in range(3)]
        for box in boxes:
            out = io.BytesIO()
            im.crop(box).save(out, format='PNG')
            parts.append({'type':'image_url', 'image_url':{'url':'data:image/png;base64,' + base64.b64encode(out.getvalue()).decode()}})
    started = time.monotonic()
    result = chat([{'role':'user','content':parts}])
    (folder / f'blind-source-{number}.json').write_text(json.dumps({'seconds':time.monotonic()-started,'native':result}, ensure_ascii=False,indent=2),encoding='utf-8')
    print(number, result['choices'][0]['message']['content'], flush=True)
