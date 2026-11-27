import sys
import threading
import unittest
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "outputs" / "SQA_MVP"))
from bug_audit_worker.transport import HTTPClient, TransportError

class TransportTests(unittest.TestCase):
    def setUp(self):
        self.counts = Counter()
        counts = self.counts
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_GET(self):
                counts[self.path] += 1
                if self.path == "/redirect":
                    self.send_response(302); self.send_header("Location", "/destination"); self.end_headers(); return
                if self.path == "/retry" and counts[self.path] < 3:
                    self.send_response(503); self.end_headers(); return
                body = b"x" * 100 if self.path == "/large" else b'{"ok":true}'
                self.send_response(200); self.end_headers(); self.wfile.write(body)
            def do_POST(self):
                counts["POST"] += 1
                self.send_response(503); self.end_headers()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.url = "http://127.0.0.1:" + str(self.server.server_port)
        self.http = HTTPClient(self.url, allow_loopback_http=True, retry_delay=0)

    def stop(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(3)

    def test_get_retries_but_post_is_never_automatically_retried(self):
        self.assertEqual(self.http.json("GET", "retry"), {"ok": True})
        self.assertEqual(self.counts["/retry"], 3)
        with self.assertRaises(TransportError) as caught:
            self.http.json("POST", "write", payload={"value": "x"})
        self.assertTrue(caught.exception.uncertain)
        self.assertEqual(self.counts["POST"], 1)

    def test_redirect_is_not_followed_and_error_does_not_leak_query(self):
        with self.assertRaises(TransportError): self.http.json("GET", "redirect")
        self.assertEqual(self.counts["/destination"], 0)
        with self.assertRaises(TransportError) as caught:
            self.http.json("POST", "write", query={"access_token": "do-not-leak"})
        self.assertNotIn("do-not-leak", str(caught.exception))

    def test_body_limit_and_cross_origin_url_are_enforced(self):
        small = HTTPClient(self.url, allow_loopback_http=True, max_bytes=50)
        with self.assertRaises(TransportError): small.request("GET", "large")
        with self.assertRaises(TransportError): self.http.request("GET", "https://elsewhere.invalid/")

if __name__ == "__main__": unittest.main()
