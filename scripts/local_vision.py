"""Loopback broker owning one Qwen llama.cpp process; shared serial GPU scheduling."""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import multiprocessing
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'services/runtime-support/src'))
from hrs_runtime.local_vision import MODEL, POLICY, free_gib, gpu_lease


def retrieval_worker(connection):
    """One owned CUDA process; embeddings and reranker stay resident between requests."""
    sys.path.insert(0, str(ROOT / 'services/research-platform/src'))
    from hrs_platform.domain.retrieval_models import LocalModels
    from hrs_platform.domain.settings import RetrievalSettings
    models = LocalModels(RetrievalSettings(models_root=ROOT / 'models/document-retrieval', device='cuda'))
    try:
        while True:
            body = connection.recv()
            started = time.monotonic()
            try:
                if body['operation'] == 'embed':
                    value = {'vectors': models.embed(body['texts'], query=body.get('query', False)), 'identity': models.identity()}
                else:
                    value = {'scores': models.rerank(body['query'], body['texts'])}
                connection.send({'result': value, 'seconds': time.monotonic() - started})
            except Exception as error:
                connection.send({'error': type(error).__name__})
    except EOFError:
        pass
    finally:
        models.unload()
        connection.close()


class Vision:
    def __init__(self):
        self.process = None
        self.log = None
        self.folder = ROOT / 'state/local-vision'
        self.folder.mkdir(parents=True, exist_ok=True)
        self.reserve = float(os.getenv('HRS_GPU_RESERVE_GIB', '1.5'))
        self.ocr_peak = float(os.getenv('HRS_OCR_PEAK_GIB', '6'))
        self.vision_peak = float(os.getenv('HRS_VISION_PEAK_GIB', '9'))
        self.last_used = time.monotonic()
        self.closing = threading.Event()
        self.task_lock = threading.RLock()
        self.active_task = None
        self.last_task = None
        self.cancelled_tasks = set()
        self.retrieval_process = None
        self.retrieval_pipe = None

    def unload_retrieval(self):
        process, self.retrieval_process = self.retrieval_process, None
        pipe, self.retrieval_pipe = self.retrieval_pipe, None
        if process is not None:
            process.terminate()
            process.join(timeout=20)
            if process.is_alive():
                process.kill()
                process.join(timeout=10)
            if process.is_alive():
                raise RuntimeError('retrieval_process_did_not_release_gpu')
            process.close()
            self.event({'event': 'retrieval_unloaded'})
        if pipe is not None:
            pipe.close()

    def retrieve(self, body):
        operation, texts = body.get('operation'), body.get('texts')
        if operation not in {'embed', 'rerank'} or not isinstance(texts, list) or not 1 <= len(texts) <= 64 or any(not isinstance(t, str) or len(t) > 40000 for t in texts):
            raise ValueError('Invalid retrieval request')
        if operation == 'rerank' and (not isinstance(body.get('query'), str) or not 1 <= len(body['query']) <= 1000):
            raise ValueError('Invalid rerank query')
        with gpu_lease():
            if self.closing.is_set():
                raise RuntimeError('local_runtime_stopping')
            self.unload()
            if self.retrieval_process is None or not self.retrieval_process.is_alive():
                self.unload_retrieval()
                if free_gib() < float(os.getenv('HRS_RETRIEVAL_PEAK_GIB', '6')) + self.reserve:
                    raise RuntimeError('gpu_memory_unavailable_for_retrieval')
                context = multiprocessing.get_context('spawn')
                self.retrieval_pipe, child = context.Pipe()
                self.retrieval_process = context.Process(target=retrieval_worker, args=(child,), daemon=True)
                self.retrieval_process.start()
                child.close()
            try:
                self.retrieval_pipe.send(body)
                if not self.retrieval_pipe.poll(900):
                    raise TimeoutError('retrieval_timeout')
                response = self.retrieval_pipe.recv()
                if 'error' in response:
                    raise RuntimeError('retrieval_failed:' + response['error'])
                self.event({'event': 'retrieval_completed', 'operation': operation, 'items': len(texts), 'seconds': response['seconds']})
                return response['result']
            except Exception:
                self.unload_retrieval()
                raise

    def cancel_tasks(self, task_ids):
        identities = {str(UUID(value)) for value in task_ids}
        with self.task_lock:
            self.cancelled_tasks.update(identities)
            if self.active_task in identities or (self.active_task is None and self.last_task in identities):
                self.unload()
        return {'cancelled': sorted(identities)}

    def event(self, value):
        with (self.folder / 'events.jsonl').open('a', encoding='utf-8') as out:
            out.write(json.dumps({'at': time.time(), **value}, ensure_ascii=False) + '\n')

    def unload(self):
        process, log = self.process, self.log
        self.process, self.log = None, None
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
            log.close()
            self.event({'event': 'vision_unloaded'})

    def prepare_ocr(self):
        self.unload_retrieval()
        available = free_gib()
        if available < self.ocr_peak + self.reserve:
            self.unload()
        available = free_gib()
        if available < self.ocr_peak + self.reserve:
            raise RuntimeError('gpu_memory_unavailable_for_ocr')
        result = {'event': 'ocr_admitted', 'free_gib': available, 'vision_resident': self.process is not None}
        self.event(result)
        return result

    def start(self):
        self.unload_retrieval()
        if self.process is not None and self.process.poll() is None:
            return
        self.unload()
        if free_gib() < self.vision_peak + self.reserve:
            raise RuntimeError('gpu_memory_unavailable_for_vision')
        model = ROOT / 'models/local-vision'
        install = json.loads((model / 'installation.json').read_text('utf-8'))
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 18161))
        command = [install['executable'], '-m', str(model / 'Qwen3VL-8B-Instruct-Q4_K_M.gguf'),
                   '--mmproj', str(model / 'mmproj-Qwen3VL-8B-Instruct-F16.gguf'),
                   '--alias', MODEL, '--host', '127.0.0.1', '--port', '18161',
                   '--ctx-size', '32768', '--parallel', '1', '--gpu-layers', '99',
                   '--batch-size', '256', '--ubatch-size', '128', '--flash-attn', 'on',
                   '--cache-type-k', 'q8_0', '--cache-type-v', 'q8_0',
                   '--image-max-tokens', '2048', '--jinja', '--no-webui']
        self.log = (self.folder / 'llama.log').open('ab')
        self.process = subprocess.Popen(command, stdout=self.log, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError('local_vision_start_failed')
            try:
                with urllib.request.urlopen('http://127.0.0.1:18161/health', timeout=2) as response:
                    if response.status == 200:
                        self.event({'event': 'vision_loaded', 'free_gib': free_gib(), 'policy': POLICY})
                        return
            except OSError:
                time.sleep(0.5)
        self.unload()
        raise RuntimeError('local_vision_start_timeout')

    def complete(self, body):
        task_id = body.pop('hrs_task_id', None)
        if task_id is not None:
            task_id = str(UUID(task_id))
        if body.get('model') != MODEL or body.get('stream'):
            raise ValueError('Unsupported local model or streaming request')
        for message in body.get('messages', []):
            for part in message.get('content', []) if isinstance(message.get('content'), list) else []:
                if part.get('type') == 'image_url' and not part['image_url']['url'].startswith('data:image/'):
                    raise ValueError('Original image bytes must be supplied inline')
        with gpu_lease():
            if self.closing.is_set():
                raise RuntimeError('local_vision_stopping')
            with self.task_lock:
                if task_id and task_id in self.cancelled_tasks:
                    raise RuntimeError('book_task_cancelled')
                self.active_task = task_id
                self.last_task = task_id
                self.start()
            if free_gib() < self.reserve:
                self.unload()
                raise RuntimeError('gpu_memory_pressure')
            started = time.monotonic()
            raw = json.dumps(body, ensure_ascii=False).encode()
            req = urllib.request.Request('http://127.0.0.1:18161/v1/chat/completions', raw,
                                         {'Content-Type': 'application/json'})
            try:
                with urllib.request.urlopen(req, timeout=900) as response:
                    value = json.load(response)
                response_hash = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
                receipts = self.folder / 'receipts'
                if task_id:
                    receipts = receipts / task_id
                receipts.mkdir(parents=True, exist_ok=True)
                (receipts / f'{response_hash}.json').write_text(json.dumps({'response': value,
                    'request_sha256': hashlib.sha256(raw).hexdigest(), 'policy': POLICY}, ensure_ascii=False), encoding='utf-8')
                if value.get('model') != MODEL:
                    raise ValueError('Local response model mismatch')
                value['local_vision'] = {'policy': POLICY, 'machine_review': True, 'human_review': False}
                self.last_used = time.monotonic()
                self.event({'event': 'completed', 'request_sha256': hashlib.sha256(raw).hexdigest(),
                    'response_sha256': hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest(),
                    'seconds': time.monotonic() - started, 'usage': value.get('usage'), 'free_gib': free_gib()})
                return value
            except Exception:
                # Stop only our owned model process, so an abandoned generation cannot overlap OCR.
                self.unload()
                raise
            finally:
                with self.task_lock:
                    self.active_task = None


def main():
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env', override=False)
    vision = Vision()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.reply(200, {'model': MODEL, 'policy': POLICY, 'resident': vision.process is not None,
                             'free_gib': free_gib()})

        def do_POST(self):
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 80 * 1024 * 1024:
                    raise ValueError('Invalid request length')
                body = json.loads(self.rfile.read(size))
                if self.path == '/prepare-ocr':
                    # Caller holds the OS GPU lease across this call and the OCR phase.
                    value = vision.prepare_ocr()
                elif self.path == '/v1/chat/completions':
                    value = vision.complete(body)
                elif self.path == '/retrieval':
                    value = vision.retrieve(body)
                elif self.path == '/cancel-tasks':
                    value = vision.cancel_tasks(body['task_ids'])
                else:
                    self.reply(404, {'error': 'unknown_route'})
                    return
                self.reply(200, value)
            except Exception as error:
                vision.event({'event': 'failed', 'error_class': type(error).__name__, 'reason': str(error)[:200]})
                self.reply(503, {'error': 'local_vision_unavailable', 'error_class': type(error).__name__})

        def reply(self, status, body):
            raw = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(('127.0.0.1', 18160), Handler)
    def stop(*_):
        vision.closing.set()
        threading.Thread(target=server.shutdown, daemon=True).start()
    for sig in (signal.SIGINT, signal.SIGTERM, *([signal.SIGBREAK] if os.name == 'nt' else [])):
        signal.signal(sig, stop)
    try:
        server.serve_forever()
    finally:
        vision.closing.set()
        vision.unload()
        vision.unload_retrieval()
        server.server_close()


if __name__ == '__main__':
    main()
