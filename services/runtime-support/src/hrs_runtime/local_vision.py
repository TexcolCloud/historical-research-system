"""One local vision route and a cross-process GPU lease shared with OCR."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request
from urllib.parse import urlsplit

MODEL = 'Qwen3-VL-8B-Instruct-Q4_K_M'
POLICY = 'local-qwen3-vl-8b-q4-v1-f982a075-b10930'


def project_root():
    for parent in Path(__file__).resolve().parents:
        if (parent / 'AGENTS.md').is_file():
            return parent
    raise RuntimeError('Project root unavailable')


def endpoint():
    value = os.getenv('HRS_LOCAL_VISION_URL', 'http://127.0.0.1:18160').rstrip('/')
    parsed = urlsplit(value)
    if parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1', '::1', 'localhost'} or parsed.username or parsed.password:
        raise ValueError('Local vision must use a loopback HTTP endpoint')
    return value


def request(path, body=None, timeout=900):
    req = urllib.request.Request(endpoint() + path,
        data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None,
        headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.load(response)


@contextmanager
def gpu_lease(timeout=1800):
    """OS releases the file lock on crash; never delete a lock another process holds."""
    path = Path(os.getenv('HRS_GPU_LOCK', str(project_root() / 'state/local-vision/gpu.lock')))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as stream:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b'0')
            stream.flush()
        deadline = time.monotonic() + timeout
        while True:
            try:
                stream.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError('GPU queue wait exceeded; completed work is retained')
                time.sleep(0.2)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def free_gib():
    value = subprocess.check_output(['nvidia-smi', '--id=0', '--query-gpu=memory.free',
        '--format=csv,noheader,nounits'], text=True, timeout=10,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    return float(value.strip().splitlines()[0]) / 1024


def prepare_ocr():
    # Called while OCR owns the GPU lease; the broker only unloads its own process.
    return request('/prepare-ocr', {}, timeout=60)


from contextvars import ContextVar

task_scope = ContextVar('vision_task_scope', default=None)


def chat(messages, *, schema=None, max_tokens=4096, timeout=900):
    body = {'model': MODEL, 'messages': messages, 'temperature': 0, 'max_tokens': max_tokens,
            'response_format': {'type': 'json_object'}}
    if schema:
        body['response_format'] = {'type': 'json_schema', 'json_schema': {'name': 'review', 'schema': schema}}
    task_id = task_scope.get() or os.environ.get('HRS_TASK_ID')
    if task_id:
        body['hrs_task_id'] = task_id
    result = request('/v1/chat/completions', body, timeout)
    if result.get('model') != MODEL:
        raise ValueError('Unexpected local vision response model')
    choice = result['choices'][0]
    if choice.get('finish_reason') != 'stop':
        raise ValueError('Local vision output incomplete; not a review pass')
    return result


def response_input(messages):
    """Translate image/text Responses inputs, keeping ordering and original bytes."""
    converted = []
    for row in messages:
        content = row['content']
        if isinstance(content, list):
            parts = []
            for part in content:
                if part['type'] == 'input_text':
                    parts.append({'type': 'text', 'text': part['text']})
                elif part['type'] == 'input_image':
                    parts.append({'type': 'image_url', 'image_url': {'url': part['image_url']}})
                else:
                    raise ValueError('Unsupported local vision input')
            content = parts
        converted.append({'role': row['role'], 'content': content})
    return converted
