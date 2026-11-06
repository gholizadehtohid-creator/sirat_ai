"""codal_announcements: what the watch keeps, and the dividend per share."""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import codal_announcements as a  # noqa: E402
import fundamental_db  # noqa: E402

# The shape of codal.ir/Reports/Decision.aspx, trimmed: a hidden
# PropertyName cell, the Persian label, and the value in an input.
PAGE = """
<tr><td class="Hidden PropertyName">AllocatedProfit</td>
<td><span>سود قابل تخصیص</span></td><td><input name="x" type="text" value="900" class="Hidden"></td></tr>
<tr><td class="Hidden PropertyName">DividendPerShare</td>
<td class="DescriptionColumn"><span>سود نقدی هر سهم (ریال)</span></td>
<td class="GridItem"><div></div><input name="y" type="text" value="55" class="Hidden">
<span class="spanFormattedValue">۵۵</span></td></tr>
<tr class="ComputationalRow"><td class="Hidden PropertyName">ListedCapital</td>
<td><span>سرمایه</span></td><td><div></div><input name="z" type="text" value="1935000000"></td></tr>
"""


def letter(tracing, published, code="ن-52",
           title="تصمیمات مجمع عمومی عادی سالیانه دوره ۱۲ ماهه منتهی به ۱۴۰۴/۱۲/۲۹"):
    return {"tracing_no": tracing, "letter_code": code, "title": title,
            "published_at": published, "symbol": "فولاد",
            "url": "/Reports/Decision.aspx?LetterSerial=%s" % tracing}


class ParseTests(unittest.TestCase):

    def test_dps_and_capital_come_from_the_machine_names(self):
        self.assertEqual(a.parse_dividend(PAGE),
                         {"dps": 55.0, "capital": 1935000000.0})

    def test_a_page_without_the_row_has_no_dividend(self):
        self.assertIsNone(a.parse_dividend("<table><tr><td>x</td></tr></table>"))

    def test_fiscal_year_from_a_persian_title(self):
        self.assertEqual(a.fiscal_year_end(
            "تصمیمات مجمع ... منتهی به  ۱۴۰۴/۱۲/۲۹(اصلاحیه)"), "1404/12/29")

    def test_kinds_match_either_digit_form(self):
        self.assertEqual(a.kind_of("ن-۵۲"), "agm")
        self.assertEqual(a.kind_of("ن-60"), "capital")
        self.assertEqual(a.kind_of("ف ب-13"), "halt")
        self.assertIsNone(a.kind_of("ن-10"))


class StoreTests(unittest.TestCase):

    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        os.unlink(self.path)
        self.previous = fundamental_db.set_path(self.path)
        a.ensure_tables()

    def tearDown(self):
        fundamental_db.set_path(self.previous)
        if os.path.exists(self.path):
            os.unlink(self.path)

    def handle(self, item, page=PAGE):
        with mock.patch.object(a.codal, "archive_read", return_value=None), \
                mock.patch.object(a.codal, "archive_write"):
            client = mock.Mock()
            client._get.return_value.content = page.encode("utf-8")
            return a.handle(client, item, {"ins_code": "123"})

    def test_an_annual_agm_stores_the_announcement_and_the_dps(self):
        self.assertEqual(self.handle(letter("1", "1405/04/29 12:00:00")),
                         "stored (DPS 55)")
        self.assertEqual(a.latest_dividends()["123"]["dps"], 55.0)
        self.assertEqual(a.recent(days=None)[0]["kind"], "agm")

    def test_an_amendment_replaces_the_original(self):
        self.handle(letter("1", "1405/04/29 12:00:00"))
        self.handle(letter("2", "1405/05/25 14:00:00"),
                    page=PAGE.replace('value="55"', 'value="60"'))
        self.assertEqual(a.latest_dividends()["123"]["dps"], 60.0)

    def test_an_older_letter_does_not_overwrite_a_newer_one(self):
        self.handle(letter("2", "1405/05/25 14:00:00"),
                    page=PAGE.replace('value="55"', 'value="60"'))
        self.handle(letter("1", "1405/04/29 12:00:00"))
        self.assertEqual(a.latest_dividends()["123"]["dps"], 60.0)

    def test_other_kinds_are_stored_without_a_request(self):
        client = mock.Mock()
        outcome = a.handle(client, letter("3", "1405/08/14 10:00:00", code="ن-20",
                                          title="افشای اطلاعات بااهمیت"),
                           {"ins_code": "123"})
        self.assertEqual(outcome, "stored")
        client._get.assert_not_called()
        self.assertEqual(a.recent(days=None, kind="material")[0]["tracing_no"], "3")

    def test_a_transient_failure_is_left_for_the_next_poll(self):
        client = mock.Mock()
        client._get.side_effect = TimeoutError("read timed out")
        with mock.patch.object(a.codal, "archive_read", return_value=None):
            self.assertIsNone(a.handle(client, letter("4", "1405/05/01 10:00:00"),
                                       {"ins_code": "123"}))


if __name__ == "__main__":
    unittest.main()
