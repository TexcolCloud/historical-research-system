"""One local vision route and a cross-process GPU lease shared with OCR."""
import json
import os
import subprocess
import time
import urllib.request
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from urllib.parse import urlsplit

MODEL = 'Qwen3-VL-8B-Instruct-Q4_K_M'
POLICY = 'local-qwen3-vl-8b-q4-v1-f982a075-b10930'


def project_root():
    configured = os.getenv('HRS_PROJECT_ROOT')
    if configured:
        return Path(configured).expanduser().resolve()
    for parent in Path(__file__).resolve().parents:
        if (parent / '.env.platform.example').is_file() and (parent / 'services').is_dir():
            return parent
    raise RuntimeError('Project root unavailable; set HRS_PROJECT_ROOT')


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
def gpu_lease(timeout=1800, *, check_cancelled=None):
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
            if check_cancelled:
                check_cancelled()
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
