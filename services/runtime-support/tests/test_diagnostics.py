import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from hrs_runtime import Diagnostics, InternalAccess, correlation_id, outbound_headers, work_scope


class DiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def test_internal_service_rejects_missing_token_and_forged_actor(self):
        calls, messages = [], []

        async def app(scope, receive, send):
            calls.append(scope["state"]["actor"])
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        async def send(message):
            messages.append(message)

        guard = InternalAccess(app, "secret")
        scope = {"type": "http", "headers": [(b"x-hrs-actor", b"forged")]}
        await guard(scope, None, send)
        self.assertEqual(messages[0]["status"], 401)
        self.assertFalse(calls)
        messages.clear()
        await guard({"type": "http", "headers": [(b"authorization", b"Bearer secret"),
                                                   (b"x-hrs-actor", b"reviewer")]}, None, send)
        self.assertEqual(calls, ["reviewer"])
        self.assertEqual(messages[0]["status"], 200)

    async def test_work_scope_propagates_identity_and_resets_after_failure(self):
        with patch("hrs_runtime.emit") as log:
            with self.assertRaises(ValueError), work_scope("cards", "task-1", attempt="attempt-1", stage="reading"):
                self.assertEqual(outbound_headers(), {"X-Correlation-ID": "job:task-1"})
                raise ValueError("private text")
        self.assertEqual(correlation_id.get(), "")
        self.assertEqual(log.call_args.args[0]["outcome"], "raised")
        self.assertNotIn("private", str(log.call_args))

    async def test_streaming_correlation_metrics_and_redaction(self):
        events, messages = [], []

        async def app(scope, receive, send):
            self.assertEqual(outbound_headers(), {"X-Correlation-ID": "chain-1"})
            scope["route"] = SimpleNamespace(path="/items/{id}")
            scope["state"] = {"request_id": "request-1"}
            await send({"type": "http.response.start", "status": 206, "headers": []})
            await send({"type": "http.response.body", "body": b"one", "more_body": True})
            await send({"type": "http.response.body", "body": b"two"})

        async def send(message):
            messages.append(message)

        middleware = Diagnostics(app, module="test", sink=events.append)
        scope = {"type": "http", "path": "/items/secret", "method": "GET",
                 "query_string": b"token=secret", "client": ("127.0.0.1", 1),
                 "headers": [(b"authorization", b"secret"), (b"x-correlation-id", b"chain-1")]}
        await middleware(scope, None, send)
        self.assertEqual([m.get("body") for m in messages[1:]], [b"one", b"two"])
        self.assertTrue(events[0]["response_complete"])
        self.assertEqual(events[0]["response_bytes"], 6)
        self.assertNotIn("secret", str(events))
        self.assertEqual(correlation_id.get(), "")
        self.assertIn(b'route="/items/{id}"', middleware.metrics())
        messages.clear()
        await middleware({**scope, "path": "/_ops/metrics"}, None, send)
        self.assertEqual(messages[0]["status"], 200)
        messages.clear()
        await middleware({**scope, "path": "/_ops/metrics", "client": ("192.0.2.1", 1)}, None, send)
        self.assertEqual(messages[0]["status"], 403)

    async def test_concurrent_requests_and_failures_do_not_leak_context(self):
        events = []

        async def broken(scope, receive, send):
            identity = correlation_id.get()
            await asyncio.sleep(0)
            self.assertEqual(identity, correlation_id.get())
            raise RuntimeError("private upstream response")

        middleware = Diagnostics(broken, module="test", sink=events.append)
        scope = {"type": "http", "path": "/secret", "method": "GET", "headers": []}
        results = await asyncio.gather(middleware(scope.copy(), None, None),
                                       middleware(scope.copy(), None, None), return_exceptions=True)
        self.assertTrue(all(isinstance(r, RuntimeError) for r in results))
        self.assertEqual(len({e["correlation_id"] for e in events}), 2)
        self.assertTrue(all(e["status"] == 500 and not e["response_complete"] for e in events))
        self.assertNotIn("private", str(events))
        self.assertEqual(correlation_id.get(), "")


if __name__ == "__main__":
    unittest.main()
