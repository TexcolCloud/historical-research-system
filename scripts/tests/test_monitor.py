import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from monitor import assess, collect, parse_metrics


class MonitorTests(unittest.TestCase):
    def test_real_http_collector_detects_server_failures_without_proxy_or_credentials(self):
        observed = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                observed.append((self.path, self.headers.get("Authorization")))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'hrs_http_requests_total{module="cards",status="503"} 1\n')
            def log_message(self, *_):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            name, metrics = collect("cards", f"http://127.0.0.1:{server.server_port}")
            self.assertEqual(observed, [("/_ops/metrics", None)])
            self.assertEqual(assess({name: metrics}, {})[0]["code"], "new_server_errors")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)

    def test_counter_deltas_alert_on_new_failures_not_old_totals(self):
        old = {"ingestion": {"requests": 100, "errors": 5}}
        current = {"ingestion": parse_metrics('hrs_http_requests_total{module="ingestion",status="200"} 99\nhrs_http_requests_total{module="ingestion",status="503"} 6')}
        self.assertEqual(assess(current, old), [{"module": "ingestion", "code": "new_server_errors", "count": 1}])
        self.assertEqual(assess(current, current), [])
        self.assertEqual(assess({"ingestion": {"error": "unreachable"}}, current)[0]["code"], "unreachable")
        with self.assertRaises(ValueError):
            parse_metrics("<html>login</html>")
