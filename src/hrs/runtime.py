"""Discover the API instances this project actually owns and serves."""

import json
import os
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen
from uuid import uuid4

import psutil
from filelock import FileLock

from hrs.config import diagnostics_root, diagnostics_roots
from hrs.processing.snapshots import atomic_json
from hrs.processing.workspace import resolve_workspace


def lifecycle_lock(project, *, timeout=30):
    """New owned services and coordinated backups serialize their admission."""
    folder = diagnostics_root(project)
    folder.mkdir(parents=True, exist_ok=True)
    return FileLock(folder / "lifecycle.lock", timeout=timeout)


def _local_url(value):
    parsed = urlparse(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("Platform discovery requires a loopback HTTP origin")
    return value.rstrip("/")


def _public_url(value):
    parsed = urlparse(value)
    host = parsed.hostname or ""
    if ":" in host:
        host = "[" + host + "]"
    if parsed.port is not None:
        host += ":" + str(parsed.port)
    return parsed._replace(netloc=host, query="", fragment="").geturl()


def register_server(settings, url):
    root = resolve_workspace(settings.codex_root)
    identity = root / ".workspace.json"
    workspace = json.loads(identity.read_text("utf-8")) if identity.is_file() else {}
    process = psutil.Process(os.getpid())
    path = diagnostics_root(settings.project_root) / "servers" / (uuid4().hex + ".json")
    atomic_json(
        path,
        {
            "pid": process.pid,
            "process_create_time": process.create_time(),
            "project_root": str(settings.project_root.resolve()),
            "root": str(root),
            "workspace_id": workspace.get("workspace_id"),
            "url": _public_url(url).rstrip("/"),
            "retrieval_endpoint": _public_url(settings.retrieval_endpoint),
            "tus_control_url": _public_url(settings.tus_control_url),
            "s3_endpoint": _public_url(settings.s3_endpoint),
            "s3_bucket": settings.s3_bucket,
        },
    )
    return path


def live_servers(project):
    """An alive owned API remains visible even when its runtime route has failed."""
    project = Path(project).resolve()
    result = []
    paths = (
        path
        for folder in diagnostics_roots(project)
        for path in sorted((folder / "servers").glob("*.json"))
    )
    seen = set()
    for path in paths:
        try:
            row = json.loads(path.read_text("utf-8"))
            if Path(row["project_root"]).resolve() != project:
                continue
            row["url"] = _public_url(row["url"]).rstrip("/")
            process = psutil.Process(int(row["pid"]))
            if (
                process.status() == psutil.STATUS_ZOMBIE
                or abs(process.create_time() - float(row["process_create_time"])) > 0.01
            ):
                continue
        except (OSError, ValueError, KeyError, TypeError, psutil.Error):
            continue
        key = (row["pid"], row["process_create_time"], row["url"])
        if key not in seen:
            result.append({**row, "record": str(path)})
            seen.add(key)
    return result


def discover_servers(project):
    """Separate configured state from verified runtime state, including older launchers."""
    project = Path(project).resolve()
    candidates = {row["url"]: row for row in live_servers(project)}
    for folder in diagnostics_roots(project):
        previous = folder / "codex-platform.json"
        if not previous.is_file():
            continue
        try:
            row = json.loads(previous.read_text("utf-8"))
            url = _local_url(row["url"])
            candidates.setdefault(url, {"url": url})
        except (OSError, ValueError, KeyError, TypeError):
            pass
    candidates.setdefault("http://127.0.0.1:18170", {"url": "http://127.0.0.1:18170"})
    result = []
    for url, receipt in candidates.items():
        row = {**receipt, "available": False}
        try:
            _local_url(url)
            with urlopen(url + "/api/v2/runtime", timeout=1.5) as response:
                runtime = json.load(response)
            if (
                not isinstance(runtime, dict)
                or runtime.get("backend") != "codex"
                or not isinstance(runtime.get("workspace_id"), str)
                or not runtime["workspace_id"]
                or not isinstance(runtime.get("root"), str)
                or not Path(runtime["root"]).is_absolute()
            ):
                raise ValueError("The listening service is not a native historical workspace")
            row.update(available=True, runtime=runtime)
        except (OSError, ValueError, TypeError) as error:
            row["error_type"] = type(error).__name__
        result.append(row)
    return result
