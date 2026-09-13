"""Download pinned official Qwen vision weights and Windows llama.cpp runtime."""
import concurrent.futures
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TAG = 'b10930'
REV = 'f982a07559d4a2f6c8744d840bf6fccab30eea96'
REPO = 'Qwen/Qwen3-VL-8B-Instruct-GGUF'


def install():
    model = ROOT / 'models/local-vision'
    runtime = ROOT / '.cache/llama.cpp' / TAG
    model.mkdir(parents=True, exist_ok=True)
    runtime.mkdir(parents=True, exist_ok=True)
    metadata = json.load(urllib.request.urlopen(f'https://huggingface.co/api/models/{REPO}/revision/{REV}?blobs=true'))
    names = ['Qwen3VL-8B-Instruct-Q4_K_M.gguf', 'mmproj-Qwen3VL-8B-Instruct-F16.gguf']
    jobs = []
    for name in names:
        entry = next(x for x in metadata['siblings'] if x['rfilename'] == name)
        jobs.append((f'https://huggingface.co/{REPO}/resolve/{REV}/{name}', model / name, entry['lfs']['sha256'], entry['lfs']['size']))
    release = json.load(urllib.request.urlopen(f'https://api.github.com/repos/ggml-org/llama.cpp/releases/tags/{TAG}'))
    for name in [f'llama-{TAG}-bin-win-cuda-13.3-x64.zip', 'cudart-llama-bin-win-cuda-13.3-x64.zip']:
        entry = next(x for x in release['assets'] if x['name'] == name)
        jobs.append((entry['browser_download_url'], runtime / name, entry.get('digest', '').removeprefix('sha256:'), entry['size']))

    if '--runtime-only' in sys.argv:
        jobs = [job for job in jobs if job[1].suffix == '.zip']

    def download(job):
        url, path, expected, size = job
        if not path.exists():
            partial = path.with_suffix(path.suffix + '.part')
            chunk_size = (2 if path.suffix == '.zip' else 32) * 1024 * 1024
            def chunk(start):
                end = min(size, start + chunk_size) - 1
                part = path.with_suffix(path.suffix + f'.chunk-{chunk_size}-{start}')
                previous = path.with_suffix(path.suffix + f'.chunk-{start}')
                if previous.exists() and previous.stat().st_size == end - start + 1:
                    return previous
                if part.exists() and part.stat().st_size == end - start + 1:
                    return part
                subprocess.run(['curl.exe', '-fL', '--http1.1', '--connect-timeout', '20', '--max-time', '180', '--retry', '5', '--retry-all-errors',
                    '--range', f'{start}-{end}', '-o', str(part), url + f'?download=true&part={start}-{end}'],
                    check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if part.stat().st_size != end - start + 1:
                    raise ValueError('Download range size mismatch')
                return part
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as chunks:
                parts = list(chunks.map(chunk, range(0, size, chunk_size)))
            with partial.open('wb') as out:
                for part in parts:
                    with part.open('rb') as source:
                        import shutil
                        shutil.copyfileobj(source, out)
            partial.replace(path)
            for part in parts:
                part.unlink()
        with path.open('rb') as stream:
            actual = hashlib.file_digest(stream, 'sha256').hexdigest()
        if not expected or expected != actual:
            raise ValueError(f'Checksum mismatch or missing upstream digest: {path.name}')
        if path.suffix == '.zip':
            with zipfile.ZipFile(path) as archive:
                archive.extractall(runtime)
        print(json.dumps({'file': path.name, 'sha256': actual}), flush=True)
        return {'path': str(path.relative_to(ROOT)), 'sha256': actual, 'url': url}

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        files = list(pool.map(download, jobs))
    if '--runtime-only' in sys.argv:
        return
    executable = next(runtime.rglob('llama-server.exe'))
    (model / 'installation.json').write_text(json.dumps({'model': REPO, 'revision': REV, 'runtime': TAG,
        'executable': str(executable), 'files': files}, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    install()
