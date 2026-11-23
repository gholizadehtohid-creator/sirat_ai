"""Every answer carries the panel's policy, and one report builds at a time.

The security review, 2026-11-23:

    NO ANSWER CARRIED A POLICY. A symbol with markup in it, written into a
    table as it came, would have run in the panel with the reader's holdings
    in reach, and any site could put the panel in a frame. Every answer now
    says what the page may load (itself) and who may frame it (nobody).

    THE PAGE MUST NEED NOTHING THE POLICY FORBIDS. Under it an inline script
    or an on* handler does not fail loudly: it just does not run, and a card
    stays empty. So the page's own sources are pinned here too -- the first
    inline handler somebody adds fails this file, not an evening of the
    panel.

    A PDF KEEPS TWO OF THE FOUR. The browser's own viewer shows it.

    ONE REPORT AT A TIME. A build starts a headless browser; a second
    request while one runs is told to come back, and starts nothing.
"""

import os
import re
import shutil
import sys
import tempfile
import threading
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import app as panel                                             # noqa: E402
import stock_report                                             # noqa: E402


def source(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as handle:
        return handle.read()


def code_lines(text):
    """`text` without its comment-only lines (// ... and block-comment bodies)."""

    return "\n".join(line for line in text.splitlines()
                     if not line.lstrip().startswith(("//", "*", "/*")))


class Policy(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.client = panel.app.test_client()

    def assert_policy(self, response):
        headers = response.headers
        self.assertEqual(headers.get("Content-Security-Policy"),
                         panel.CONTENT_SECURITY_POLICY)
        self.assertEqual(headers.get("X-Frame-Options"), "DENY")
        self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(headers.get("Referrer-Policy"), "same-origin")


class SecurityHeaderTests(Policy):

    def test_the_policy_allows_the_page_itself_and_nothing_else(self):
        directives = dict(part.strip().split(" ", 1) for part in
                          panel.CONTENT_SECURITY_POLICY.split(";"))

        self.assertEqual(directives["default-src"], "'self'")
        self.assertEqual(directives["script-src"], "'self'")
        self.assertEqual(directives["connect-src"], "'self'")
        self.assertEqual(directives["font-src"], "'self'")
        self.assertEqual(directives["img-src"], "'self' data:")
        self.assertEqual(directives["frame-ancestors"], "'none'")
        self.assertEqual(directives["base-uri"], "'self'")
        self.assertEqual(directives["form-action"], "'self'")
        # Inline STYLE is allowed -- the page is full of style="" -- and
        # nothing else inline is.
        self.assertEqual(directives["style-src"], "'self' 'unsafe-inline'")
        self.assertNotIn("unsafe-eval", panel.CONTENT_SECURITY_POLICY)
        self.assertNotIn("*", panel.CONTENT_SECURITY_POLICY)

    def test_the_dashboard_carries_it(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assert_policy(response)

    def test_an_api_answer_carries_it(self):
        import codal_announcements

        with mock.patch.object(codal_announcements, "recent", return_value=[]):
            response = self.client.get("/api/announcements")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["count"], 0)
        self.assert_policy(response)

    def test_a_refusal_and_an_unknown_route_carry_it(self):
        refused = self.client.get("/api/announcements",
                                  headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(refused.status_code, 403)
        self.assert_policy(refused)

        rebinding = self.client.get("/", headers={"Host": "evil.example"})
        self.assertEqual(rebinding.status_code, 403)
        self.assert_policy(rebinding)

        missing = self.client.get("/api/no-such-route")
        self.assertEqual(missing.status_code, 404)
        self.assert_policy(missing)

    def test_the_static_files_carry_it_and_say_what_they_are(self):
        """nosniff makes the declared type binding: a script that is not
        served as JavaScript, or a stylesheet that is not text/css, is
        dropped by the browser."""

        for path, kind in (("/static/js/panel.js", "javascript"),
                           ("/static/js/charts.js", "javascript"),
                           ("/static/css/panel.css", "text/css")):
            response = self.client.get(path)
            try:
                self.assertEqual(response.status_code, 200, path)
                self.assertIn(kind, response.headers["Content-Type"], path)
                self.assert_policy(response)
            finally:
                response.close()


class ThePageNeedsNothingThePolicyForbids(unittest.TestCase):
    """Read from the sources: there is no browser here to load the page in."""

    def test_the_only_script_is_the_module_from_static(self):
        html = source("templates", "dashboard.html")
        scripts = re.findall(r"<script\b([^>]*)>(.*?)</script>", html,
                             flags=re.I | re.S)

        self.assertTrue(scripts)
        for attributes, body in scripts:
            self.assertIn("src=", attributes)
            self.assertIn("url_for('static'", attributes)
            self.assertEqual(body.strip(), "", "an inline script")

    def test_the_page_has_no_inline_handler_or_script_url(self):
        html = source("templates", "dashboard.html")

        self.assertIsNone(re.search(r"\son[a-z]+\s*=\s*[\"']", html, re.I))
        self.assertNotIn("javascript:", html.lower())

    def test_the_page_loads_nothing_from_another_host(self):
        html = source("templates", "dashboard.html")
        loaded = re.findall(
            r"<(?:script|link|img|iframe|frame|object|embed|source|video|"
            r"audio|base)\b[^>]*?\b(?:src|href)\s*=\s*[\"']([^\"']*)", html,
            flags=re.I)

        for address in loaded:
            self.assertFalse(re.match(r"(?:[a-z][a-z0-9+.-]*:)?//", address,
                                      re.I), address)
            if ":" in address.split("/")[0] and "{{" not in address:
                self.assertTrue(address.startswith("data:image/"), address)
        self.assertIsNone(re.search(r"<(?:iframe|frame|object|embed|base)\b",
                                    html, re.I))

    def test_the_scripts_build_no_handler_and_evaluate_no_text(self):
        for name in ("panel.js", "charts.js"):
            code = code_lines(source("static", "js", name))

            # an on* attribute inside markup the script writes
            self.assertIsNone(
                re.search(r"<[a-z][^>`\n]*\son[a-z]+\s*=\s*\\?[\"'$]", code,
                          re.I), name)
            self.assertIsNone(re.search(r"setAttribute\(\s*[\"']on", code),
                              name)
            for forbidden in ("eval(", "new Function", "document.write",
                              "javascript:", "createObjectURL", "new Worker",
                              "importScripts"):
                self.assertNotIn(forbidden, code, "%s in %s" % (forbidden, name))
            # a timer given text to run
            self.assertIsNone(
                re.search(r"set(?:Timeout|Interval)\(\s*[\"'`]", code), name)

    def test_the_scripts_fetch_only_the_panel(self):
        code = code_lines(source("static", "js", "panel.js"))

        self.assertIsNone(re.search(r"fetch\(\s*[\"'`](?!/)", code))
        self.assertIsNone(re.search(r"getJSON\(\s*[\"'`](?!/api/)", code))

    def test_the_stylesheet_loads_its_font_and_its_own_icons_only(self):
        css = source("static", "css", "panel.css")

        self.assertNotIn("@import", css)
        for address in re.findall(r"url\(\s*[\"']?([^\"')]+)", css):
            self.assertTrue(address.startswith(("data:image/", "../fonts/")),
                            address[:60])


class ReportRouteTests(Policy):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="sirat-report-test-")
        self.pdf = os.path.join(self.dir, "x.pdf")
        with open(self.pdf, "wb") as handle:
            handle.write(b"%PDF-1.4\n%%EOF\n")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_a_pdf_is_left_to_the_browsers_own_viewer(self):
        with mock.patch.object(stock_report, "build", return_value=self.pdf):
            response = self.client.get("/api/report?symbol=x")
        try:
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.mimetype, "application/pdf")
            self.assertNotIn("Content-Security-Policy", response.headers)
            self.assertNotIn("X-Frame-Options", response.headers)
            self.assertEqual(response.headers["X-Content-Type-Options"],
                             "nosniff")
            self.assertEqual(response.headers["Referrer-Policy"],
                             "same-origin")
        finally:
            response.close()

    def test_the_reports_error_page_carries_the_policy(self):
        with mock.patch.object(stock_report, "build",
                               side_effect=stock_report.ReportError("نیست")):
            response = self.client.get("/api/report?symbol=x")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.mimetype, "text/html")
        self.assert_policy(response)
        self.assertFalse(panel._REPORT_LOCK.locked())

    def test_a_second_request_during_a_build_starts_no_second_build(self):
        started, finish = threading.Event(), threading.Event()
        built, first = [], {}

        def slow_build(symbol):
            built.append(symbol)
            started.set()
            finish.wait(20)
            return self.pdf

        def first_request():
            first["response"] = panel.app.test_client().get(
                "/api/report?symbol=a")

        with mock.patch.object(stock_report, "build", side_effect=slow_build), \
                mock.patch.object(panel, "REPORT_WAIT_SECONDS", 0.05):
            thread = threading.Thread(target=first_request)
            thread.start()
            try:
                self.assertTrue(started.wait(10), "the first build never began")

                second = self.client.get("/api/report?symbol=b")

                self.assertEqual(second.status_code, 429)
                self.assertEqual(second.headers["Retry-After"], "5")
                self.assertIn("در حال ساخت", second.get_data(as_text=True))
                self.assert_policy(second)
                self.assertEqual(built, ["a"])
            finally:
                finish.set()
                thread.join(20)

            self.assertEqual(first["response"].status_code, 200)
            first["response"].close()

            # The build over, the next request builds.
            third = self.client.get("/api/report?symbol=c")
            self.assertEqual(third.status_code, 200)
            third.close()
            self.assertEqual(built, ["a", "c"])

    def test_a_build_that_fails_does_not_keep_the_lock(self):
        with mock.patch.object(stock_report, "build",
                               side_effect=RuntimeError("edge fell over")), \
                self.assertLogs(panel.app.logger, level="ERROR"):
            response = self.client.get("/api/report?symbol=x")

        self.assertEqual(response.status_code, 500)
        self.assertFalse(panel._REPORT_LOCK.locked())

    def test_a_request_without_a_symbol_takes_no_lock(self):
        with mock.patch.object(stock_report, "build") as build:
            response = self.client.get("/api/report")

        self.assertEqual(response.status_code, 400)
        build.assert_not_called()
        self.assertFalse(panel._REPORT_LOCK.locked())


if __name__ == "__main__":
    unittest.main()
