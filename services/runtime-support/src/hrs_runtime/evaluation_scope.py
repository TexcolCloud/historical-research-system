"""Opt-in isolation attestation from effective module settings; never provisions targets.

This internal probe is deliberately outside the public business route catalogue.
It requires a service credential even when normal local development is unauthenticated.
"""

import hashlib
import json
import os
import re
import secrets
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def local_service(value):
    url = urlsplit(value)
    if (url.scheme != "http" or url.hostname != "127.0.0.1" or not url.port
        or not 18460 <= url.port <= 18999 or url.username or url.password or url.query or url.fragment
        or url.path.rstrip("/") not in {"", "/api/v1"}):
        raise ValueError("evaluation_service_address_invalid")
    return value


def inspect_scope(module, settings, scope, root):
    if not re.fullmatch(r"[a-z][a-z0-9]{2,20}", scope):
        raise ValueError("evaluation_scope_invalid")
    root = Path(root)
    if not root.is_absolute() or root.resolve() == Path(root.anchor) or not root.is_dir():
        raise ValueError("evaluation_root_invalid")
    root = root.resolve()
    values = settings.model_dump() if hasattr(settings, "model_dump") else dict(settings)
    for key, value in list(values.items()):
        if hasattr(value, "get_secret_value"):
            values[key] = value.get_secret_value()
    paths = ("storage_root", "receive_root") if module == "ingestion" else ("state_root",)
    for key in paths:
        path = Path(values.get(key) or ".").resolve()
        if path == root or not path.is_relative_to(root / module):
            raise ValueError("evaluation_storage_not_isolated")
    if values.get("storage_backend", "local") != "local" or values.get("auto_sync", False):
        raise ValueError("evaluation_automatic_or_remote_writes_forbidden")
    if module in {"ingestion", "cards"}:
        database = urlsplit(values.get("database_url") or "")
        if (database.hostname != "127.0.0.1" or database.port != 55437
            or database.path != f"/hrs_eval_{scope}_{module}" or database.query or database.fragment
            or values.get("database_name") not in {None, f"hrs_eval_{scope}_{module}"}):
            raise ValueError("evaluation_database_not_isolated")
    if module in {"cards", "retrieval"}:
        if values.get("index_prefix") != f"hrs-eval-{scope}-{module}" or values.get("opensearch_url") != "http://127.0.0.1:19261":
            raise ValueError("evaluation_index_not_isolated")
        local_service(values["ingestion_url"])
    if module == "cards":
        local_service(values["retrieval_url"])
    public = {k: v for k, v in values.items() if not re.search(r"(?:password|secret_key|access_key|api_key|database_url|_token)$", k)}
    return {"scope": scope, "module": module, "isolated": True, "target_sha256": digest(public),
            "root_sha256": digest(str(root)), "configuration": public,
            "observed_at": datetime.now(UTC).isoformat(), "business_adoption": False}


class EvaluationScope:
    def __init__(self, app, module, settings):
        self.app, self.module, self.settings = app, module, settings
        self.scope = os.environ.get("HRS_EVALUATION_SCOPE", "")
        self.root = os.environ.get("HRS_EVALUATION_ROOT", "")
        self.token = os.environ.get("HRS_SERVICE_TOKEN", "")

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] != "/_ops/evaluation":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        status, value = 403, {"code": "evaluation_probe_unavailable"}
        if (scope["method"] == "GET" and self.token and self.scope and self.root and self.settings
            and secrets.compare_digest(headers.get(b"authorization", b""), ("Bearer " + self.token).encode())):
            try:
                value = inspect_scope(self.module, self.settings(), self.scope, self.root)
                status = 200
            except (ValueError, TypeError, KeyError, AttributeError):
                status, value = 409, {"code": "evaluation_isolation_invalid"}
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json"), (b"cache-control", b"no-store")]})
        await send({"type": "http.response.body", "body": json.dumps(value, default=str).encode()})
