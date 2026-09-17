"""Exercise real resident-model unloading and reloading, without changing book state."""
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'packages/runtime-support/src'))
from hrs_runtime.local_vision import request, gpu_lease, prepare_ocr, chat

before = request('/health')
with gpu_lease():
    admitted = prepare_ocr()
after_unload = request('/health')
started = time.monotonic()
native = chat([{'role':'user', 'content':'Return JSON {"ready":true}.'}], max_tokens=32)
result = {'before':before, 'ocr_admission':admitted, 'after_unload':after_unload,
          'after_reload':request('/health'), 'reload_request_seconds':time.monotonic()-started,
          'native':native, 'scope':'real GPU handoff and reload; OCR inference itself is not invoked'}
path = ROOT / 'output/local-vision-20260912/memory-handoff.json'
path.write_text(json.dumps(result, ensure_ascii=False,indent=2),encoding='utf-8')
assert before['resident'] and not after_unload['resident'] and result['after_reload']['resident']
assert admitted['free_gib'] > before['free_gib']
print(json.dumps({k:v for k,v in result.items() if k != 'native'},ensure_ascii=False), flush=True)
