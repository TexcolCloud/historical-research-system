"""Own the loopback workbench process without coupling processing to a transport."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qsl, quote, urlencode, urlparse
from urllib.request import Request, urlopen

from filelock import FileLock

from hrs.config import project_root
from hrs.processing.store import Store
from hrs.processing.workspace import identity, running_workbench
from hrs.runtime import discover_servers

PROJECT = project_root()


def platform_workbench(
    store: Store, document_id: str | None, page: int | None, entry_id: str | None = None
) -> dict | None:
    """Attach to the existing projection only after both workspace identity checks pass."""
    workspace = identity(store)
    for server in discover_servers(PROJECT):
        runtime = server.get("runtime") or {}
        if (
            not server.get("available")
            or runtime.get("workspace_id") != workspace["workspace_id"]
            or Path(runtime.get("root", "")).resolve() != store.root.resolve()
        ):
            continue
        origin = server["url"]
        route = "/#/"
        if document_id:
            request = Request(
                origin + "/api/v2/native-workbench",
                data=json.dumps(
                    {"document_id": document_id, "workspace_id": workspace["workspace_id"]}
                ).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urlopen(request, timeout=10) as response:
                    result = json.load(response)
                book_id = result["book_id"]
                if not isinstance(book_id, str) or not book_id:
                    raise ValueError("Platform returned no reading projection")
            except HTTPError as error:
                if error.code in {404, 405, 502, 503, 504}:
                    continue
                raise ValueError(
                    f"Platform rejected native task attachment (HTTP {error.code}); reopen the task after resolving its state."
                ) from error
            except OSError:
                continue
            query = (
                {"tab": "review", "task": "navigation", "entry": entry_id}
                if entry_id
                else {"tab": "review", "task": "content", "reviewPage": page}
                if page
                else {"tab": "progress"}
            )
            route = f"/#/books/{quote(book_id, safe='')}?{urlencode(query)}"
        return {**workspace, "url": origin + route, "surface": "platform"}
    return None


def document_workbench(
    record: dict, document_id: str | None, page: int | None, entry_id: str | None = None
) -> dict:
    if not document_id:
        return record
    parsed = urlparse(record["url"])
    query = dict(parse_qsl(parsed.query))
    query.update(
        view="navigation" if entry_id else "review" if page else "document", document=document_id
    )
    if entry_id:
        query["entry"] = entry_id
    if page:
        query["page"] = str(page)
    return {**record, "url": parsed._replace(query=urlencode(query)).geturl()}


def ensure_workbench(
    store: Store,
    document_id: str | None = None,
    page: int | None = None,
    entry_id: str | None = None,
) -> dict:
    """Start one owned loopback workbench/runner for this exact workspace."""
    if page is not None and (not document_id or page < 1):
        raise ValueError("A review page requires a document and positive physical page")
    if entry_id and (not document_id or page):
        raise ValueError(
            "A directory entry requires a document and cannot select a content review page"
        )
    platform = platform_workbench(store, document_id, page, entry_id)
    if platform:
        return platform
    with FileLock(store.root / ".workbench-start.lock", timeout=30):
        existing = running_workbench(store)
        if existing:
            return document_workbench(existing, document_id, page, entry_id)
        logs = store.root / "workbench.log"
        with logs.open("ab") as output:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "hrs",
                    "--root",
                    str(store.root),
                    "workbench",
                    "--port",
                    "0",
                ],
                cwd=PROJECT,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=output,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                start_new_session=os.name != "nt",
                env={**os.environ, "PYTHONUTF8": "1"},
            )
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            record = running_workbench(store)
            if record:
                return document_workbench(record, document_id, page, entry_id)
            if process.poll() is not None:
                raise RuntimeError(f"Workbench exited {process.returncode}; inspect {logs}")
            time.sleep(0.1)
        process.terminate()
        raise RuntimeError(f"Workbench startup timed out; inspect {logs}")
