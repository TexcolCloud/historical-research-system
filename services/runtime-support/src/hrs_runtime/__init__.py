"""Dependency-free, streaming-safe diagnostics; business receipts remain authoritative."""

import ipaddress
import json
import logging
import os
import re
import secrets
import threading
import time
from collections import defaultdict
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from uuid import uuid4

correlation_id = ContextVar("hrs_correlation_id", default="")
IDENTIFIER = re.compile(r"^[A-Za-z0-9._:-]{1,96}$")
BUCKETS = (0.01, 0.05, 0.1, 0.5, 1, 5, 30, 120)


def outbound_headers(identity=None):
    value = identity or correlation_id.get()
    return {"X-Correlation-ID": value} if value and IDENTIFIER.fullmatch(value) else {}


def inject_correlation(request):
    for name, value in outbound_headers().items():
        request.headers.setdefault(name, value)


async def inject_correlation_async(request):
    inject_correlation(request)


@contextmanager
def work_scope(module, identity, *, attempt=None, stage=None):
    """Correlate one persisted work step; returning does not mean task acceptance."""
    token = correlation_id.set("job:" + str(identity))
    started = time.monotonic()
    outcome, error_class = "returned", None
    try:
        yield
    except BaseException as error:
        outcome, error_class = "raised", type(error).__name__
        raise
    finally:
        try:
            emit({"event": "worker_step", "module": module, "job_id": str(identity),
                  "correlation_id": correlation_id.get(), "attempt_id": str(attempt) if attempt else None,
                  "stage": stage, "outcome": outcome, "error_class": error_class,
                  "duration_seconds": round(time.monotonic() - started, 6)})
        except Exception:  # noqa: BLE001 -- diagnostic failures do not affect durable job outcomes
            pass
        finally:
            correlation_id.reset(token)


def emit(event):
    logger = logging.getLogger("hrs.http")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    logger.info(json.dumps({"time": datetime.now(UTC).isoformat(), **event}))


class Diagnostics:
    """ASGI middleware: never buffer bodies, log secrets, or alter application JSON."""

    def __init__(self, app, *, module, sink=emit, evaluation_settings=None):
        from .evaluation_scope import EvaluationScope
        app = EvaluationScope(app, module, evaluation_settings)
        token = os.environ.get("HRS_SERVICE_TOKEN")
        self.app, self.module, self.sink = InternalAccess(app, token) if token else app, module, sink
        self.rows = defaultdict(lambda: [0, 0.0, 0, [0] * len(BUCKETS)])
        self.lock = threading.Lock()

    def metrics(self):
        lines = [
            "# TYPE hrs_http_requests_total counter",
            "# TYPE hrs_http_duration_seconds histogram",
            "# TYPE hrs_http_response_bytes_total counter",
        ]
        with self.lock:
            for (method, route, status), (count, seconds, size, buckets) in sorted(self.rows.items()):
                labels = f'module={json.dumps(self.module)},method={json.dumps(method)},route={json.dumps(route)},status="{status}"'
                lines.extend([
                    f"hrs_http_requests_total{{{labels}}} {count}",
                    f"hrs_http_response_bytes_total{{{labels}}} {size}",
                    f"hrs_http_duration_seconds_count{{{labels}}} {count}",
                    f"hrs_http_duration_seconds_sum{{{labels}}} {seconds:.9f}",
                ])
                for bound, value in zip(BUCKETS, buckets, strict=True):
                    lines.append(f'hrs_http_duration_seconds_bucket{{{labels},le="{bound}"}} {value}')
                lines.append(f'hrs_http_duration_seconds_bucket{{{labels},le="+Inf"}} {count}')
        return ("\n".join(lines) + "\n").encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        if scope["path"] == "/_ops/metrics":
            try:
                local = ipaddress.ip_address(scope.get("client", ("", 0))[0]).is_loopback
            except (ValueError, TypeError):
                local = False
            allowed = local and scope["method"] in {"GET", "HEAD"}
            body = self.metrics() if allowed else b"Metrics are available on loopback only.\n"
            await send({"type": "http.response.start", "status": 200 if allowed else 403,
                        "headers": [(b"content-type", b"text/plain; version=0.0.4; charset=utf-8"),
                                    (b"cache-control", b"no-store")]})
            return await send({"type": "http.response.body",
                               "body": b"" if scope["method"] == "HEAD" else body})
        raw = dict(scope.get("headers", [])).get(b"x-correlation-id", b"").decode("latin1")
        identity = raw if IDENTIFIER.fullmatch(raw) else str(uuid4())
        token = correlation_id.set(identity)
        status, size, complete = 500, 0, False
        started = time.monotonic()

        async def observe(message):
            nonlocal status, size, complete
            if message["type"] == "http.response.start":
                status = message["status"]
                message = {**message, "headers": [
                    (k, v) for k, v in message.get("headers", [])
                    if k.lower() != b"x-correlation-id"
                ] + [(b"x-correlation-id", identity.encode("ascii"))]}
            if message["type"] == "http.response.body":
                size += len(message.get("body", b""))
            await send(message)
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                complete = True

        try:
            await self.app(scope, receive, observe)
        finally:
            elapsed = time.monotonic() - started
            route = getattr(scope.get("route"), "path", "unmatched")
            method = scope["method"] if scope["method"] in {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"} else "OTHER"
            request_id = scope.get("state", {}).get("request_id", "")
            if not isinstance(request_id, str) or not IDENTIFIER.fullmatch(request_id):
                request_id = ""
            with self.lock:
                row = self.rows[(method, route, status)]
                row[0] += 1
                row[1] += elapsed
                row[2] += size
                for index, bound in enumerate(BUCKETS):
                    row[3][index] += elapsed <= bound
            try:
                self.sink({"event": "http_request", "module": self.module,
                           "correlation_id": identity, "request_id": request_id,
                           "actor": scope.get("state", {}).get("actor"),
                           "method": method, "route": route, "status": status,
                           "duration_seconds": round(elapsed, 6), "response_bytes": size,
                           "response_complete": complete})
            except Exception:  # noqa: BLE001 -- a failed diagnostic sink must not change business outcomes
                pass
            finally:
                correlation_id.reset(token)


class InternalAccess:
    """Internal process credential, separate from user accounts at the TLS gateway."""

    def __init__(self, app, token):
        self.app, self.expected = app, ("Bearer " + token).encode("ascii")

    async def __call__(self, scope, receive, send):
        if scope["type"] == "websocket":
            return await send({"type": "websocket.close", "code": 1008})
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        if not secrets.compare_digest(headers.get(b"authorization", b""), self.expected):
            await send({"type": "http.response.start", "status": 401,
                        "headers": [(b"content-type", b"application/json"), (b"cache-control", b"no-store")]})
            return await send({"type": "http.response.body", "body": b'{"code":"internal_credential_required"}'})
        actor = headers.get(b"x-hrs-actor", b"").decode("latin1")
        scope.setdefault("state", {})["actor"] = actor if re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", actor) else "internal_service"
        return await self.app(scope, receive, send)
