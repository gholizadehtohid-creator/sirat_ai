"""A filing is downloaded from Codal, and from nowhere else.

The security review, 2026-11-23: a filing's address is whatever the search
reply's ExcelUrl says, and CodalClient.fetch_sheets downloaded it as given,
over a session that may be bound to the physical NIC -- which reaches the
home network and this machine's own panel as well as Codal. A reply naming
http://192.168.1.1/ or this panel's /api/ would have been fetched. These
tests pin the rule:

    ONLY HTTPS, ONLY CODAL. codal.ir or a name under it. Every one of the
    37,657 stored excel_url values is https://excel.codal.ir.

    REFUSED IS SETTLED, AND NO REQUEST IS MADE. The address will not change,
    so the filing is "bad", not "try again later".

    THE ARCHIVE NEEDS NO ADDRESS. A document already on disk is read from
    there whatever its letter says.
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import codal  # noqa: E402


def client(get):
    """A client that talks to nothing: its own session, no pacing."""

    made = codal.CodalClient(session=object(), delay=0)
    made._get = get
    return made


class FilingUrlRefusalTests(unittest.TestCase):

    def test_codal_over_https_passes(self):
        for url in ("https://excel.codal.ir/service/Excel/GetAll/abc/0",
                    "https://codal.ir/Reports/Excel.aspx?LetterSerial=x",
                    "https://www.codal.ir/x",
                    "https://search.codal.ir:443/x",
                    "HTTPS://EXCEL.CODAL.IR/x",
                    "https://excel.codal.ir./x",
                    "  https://excel.codal.ir/x  "):
            self.assertIsNone(codal.filing_url_refusal(url), url)

    def test_another_scheme_is_refused(self):
        for url in ("http://excel.codal.ir/x",
                    "ftp://excel.codal.ir/x",
                    "file:///C:/Windows/win.ini",
                    "//excel.codal.ir/x",
                    "/Reports/Excel.aspx?LetterSerial=x",
                    "excel.codal.ir/x",
                    "", None):
            self.assertIn("https", codal.filing_url_refusal(url) or "", url)

    def test_another_host_is_refused(self):
        for url in ("https://127.0.0.1:5000/api/bootstrap?force=1",
                    "https://localhost:5000/api/report?symbol=x",
                    "https://192.168.1.1/",
                    "https://[::1]/x",
                    "https://evil.example/x",
                    "https://evilcodal.ir/x",                 # no dot before
                    "https://codal.ir.evil.example/x",        # a prefix only
                    "https://excel.codal.ir@evil.example/x",  # a user name
                    "https://evil.example/excel.codal.ir",    # a path
                    "https://evil.example/?u=https://codal.ir/"):
            why = codal.filing_url_refusal(url)
            self.assertIsNotNone(why, url)
            self.assertIn("host", why, url)

    def test_what_two_parsers_read_differently_is_refused(self):
        for url in ("https://evil.example\\@excel.codal.ir/x",
                    "https://excel.codal.ir/x y",
                    "https://excel.codal.ir/x\r\nHost: evil.example",
                    "https://excel.codal.ir/\x00x",
                    "https://[excel.codal.ir/x"):
            self.assertIsNotNone(codal.filing_url_refusal(url), repr(url))

    def test_the_reason_is_short_whatever_the_address(self):
        why = codal.filing_url_refusal("https://" + "a" * 5000 + ".example/")
        self.assertLess(len(why), 120)
        why = codal.filing_url_refusal("x" * 5000 + "://codal.ir/")
        self.assertLess(len(why), 120)


class FetchSheetsTests(unittest.TestCase):

    def test_a_foreign_address_is_settled_without_a_request(self):
        for url in ("http://excel.codal.ir/x",
                    "https://192.168.1.1/",
                    "https://evil.example/f.xls"):
            get = mock.Mock(side_effect=AssertionError("a request was made"))

            result = client(get).fetch_sheets(url)

            get.assert_not_called()
            self.assertFalse(result.ok)
            self.assertEqual(result.kind, codal.KIND_BAD, url)
            self.assertFalse(result.transient)
            self.assertIn("refused", result.error)

    def test_a_codal_address_is_still_downloaded(self):
        get = mock.Mock(return_value=mock.Mock(content=b""))
        url = "https://excel.codal.ir/service/Excel/GetAll/abc/0"

        result = client(get).fetch_sheets(url)

        self.assertEqual(get.call_args.args[0], url)
        self.assertNotIn("refused", result.error)     # an empty body, as before
        self.assertEqual(result.kind, codal.KIND_TRANSIENT)

    def test_an_archived_document_needs_no_address(self):
        get = mock.Mock(side_effect=AssertionError("a request was made"))

        with mock.patch.object(codal, "archive_read", return_value=b"body"), \
                mock.patch.object(codal.CodalClient, "parse_body",
                                  return_value="parsed") as parse:
            result = client(get).fetch_sheets("u", archive_key="123")

        self.assertEqual(result, "parsed")
        parse.assert_called_once_with(b"body")
        get.assert_not_called()

    def test_a_letter_without_an_address_reads_as_before(self):
        result = client(mock.Mock()).fetch_sheets("")

        self.assertEqual(result.error, "filing has no Excel URL")


if __name__ == "__main__":
    unittest.main()
