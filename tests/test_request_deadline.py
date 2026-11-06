"""A request cannot outlive its deadline, and Retry-After cannot stall it."""

import http.server
import os
import sys
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from urllib3.response import HTTPResponse  # noqa: E402

import settings  # noqa: E402
from providers import transport  # noqa: E402


class _Handler(http.server.BaseHTTPRequestHandler):

    def do_GET(self):
        body = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.path == "/slow":
            # A byte every 0.1 s: never idle long enough for a read timeout.
            for byte in body:
                self.wfile.write(bytes([byte]))
                self.wfile.flush()
                time.sleep(0.1)
        else:
            self.wfile.write(body)

    def log_message(self, *args):
        pass


class DeadlineSessionTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = "http://127.0.0.1:%d" % cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def session(self, deadline):
        session = transport.DeadlineSession()
        session.trust_env = False
        session.deadline = deadline
        return session

    def test_a_trickling_body_is_cut_at_the_deadline(self):
        started = time.monotonic()
        with self.assertRaises(transport.RequestDeadlineExceeded):
            self.session(0.4).get(self.base + "/slow", timeout=(2, 2))
        self.assertLess(time.monotonic() - started, 1.0)

    def test_the_deadline_counts_as_a_timeout_for_callers(self):
        self.assertTrue(issubclass(transport.RequestDeadlineExceeded,
                                   transport.requests.exceptions.Timeout))

    def test_a_normal_response_reads_as_before(self):
        response = self.session(5).get(self.base + "/fast", timeout=(2, 2))
        self.assertEqual(response.json(), {"ok": True})
        self.assertEqual(response.content, b'{"ok": true}')

    def test_build_session_returns_one(self):
        with mock.patch.object(transport, "discover_source_ip", return_value=None):
            self.assertIsInstance(transport.build_session(bind_source=False),
                                  transport.DeadlineSession)


class CappedRetryTests(unittest.TestCase):

    def retry_after(self, value):
        response = HTTPResponse(body=b"", status=503,
                                headers={"Retry-After": value})
        return transport.CappedRetry(total=3).get_retry_after(response)

    def test_a_long_retry_after_is_capped(self):
        self.assertEqual(self.retry_after("3600"), settings.RETRY_AFTER_CAP)

    def test_a_short_retry_after_is_obeyed(self):
        self.assertEqual(self.retry_after("2"), 2)

    def test_the_cap_survives_the_retry_counter(self):
        # urllib3 builds a new Retry for every attempt; it must stay capped.
        retry = transport.CappedRetry(total=3).increment(method="GET", url="/")
        self.assertIsInstance(retry, transport.CappedRetry)


if __name__ == "__main__":
    unittest.main()
