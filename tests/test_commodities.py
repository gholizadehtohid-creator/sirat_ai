"""commodities.py: world prices as context, never a signal (2026-11-26)."""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bot_content  # noqa: E402
import commodities as cm  # noqa: E402


def rows(start_price, days, step=0.0):
    """Daily rows (gregorian, jalali, close) from 2025-01-01."""

    from datetime import date, timedelta
    out = []
    for i in range(days):
        d = date(2025, 1, 1) + timedelta(days=i)
        out.append((d.isoformat(), "14%02d/%02d/%02d" % (3, 1 + i // 30 % 12, 1 + i % 29),
                    start_price * (1 + step) ** i))
    return out


class SummaryTests(unittest.TestCase):

    def test_the_week_month_and_year_are_read_back_from_the_latest_day(self):
        record = {"fetched_at": "2026-01-01T00:00:00+00:00", "source": "tgju.org",
                  "series": {"copper": rows(4.0, 400, 0.001)}}
        s = cm.summary(record, today=record["series"]["copper"][-1][0])
        item = s["items"][0]
        self.assertEqual((item["name"], item["label"], item["unit"]),
                         ("copper", "مس", "دلار/پوند"))
        self.assertAlmostEqual(item["change_1w"], 1.001 ** 7 - 1)
        self.assertAlmostEqual(item["change_1m"], 1.001 ** 30 - 1)
        self.assertAlmostEqual(item["change_1y"], 1.001 ** 365 - 1)
        self.assertFalse(item["stale"])

    def test_a_series_too_short_for_a_year_says_nothing_of_the_year(self):
        record = {"series": {"brent": rows(80.0, 40)}}
        item = cm.summary(record, today="2025-02-09")["items"][0]
        self.assertIsNone(item["change_1y"])
        self.assertIsNotNone(item["change_1m"])

    def test_an_old_last_close_is_marked_stale(self):
        record = {"series": {"lead": rows(1800.0, 200)}}
        item = cm.summary(record, today="2026-01-01")["items"][0]
        self.assertTrue(item["stale"])

    def test_nothing_stored_is_none(self):
        self.assertIsNone(cm.summary({"series": {}}))
        self.assertIsNone(cm.summary(None or {}))


class CompanyTests(unittest.TestCase):

    def test_metal_producers_are_matched_by_name_inside_their_sectors(self):
        self.assertEqual(cm.for_company("ملی صنایع مس ایران", "فلزات اساسي"), ("copper",))
        self.assertEqual(cm.for_company("الومینیومایران", "فلزات اساسي"), ("aluminum",))
        self.assertEqual(cm.for_company("ملی سربوروی ایران", "فلزات اساسي"), ("lead", "zinc"))
        self.assertEqual(cm.for_company("توسعه معادن روی ایران", "استخراج کانه هاي فلزي"),
                         ("zinc", "lead"))

    def test_a_name_outside_the_metal_sectors_is_not_read(self):
        # «مس» opens «مسکن»: a housing developer is not a copper story
        self.assertEqual(cm.for_company("سرمایه گذاری مسکن", "انبوه سازي، املاك و مستغلات"), ())
        self.assertEqual(cm.for_company("فولاد مبارکه اصفهان", "فلزات اساسي"), ())

    def test_refiners_and_petrochemicals_follow_oil(self):
        self.assertEqual(cm.for_company("پالایش نفت اصفهان",
                                        "فراورده هاي نفتي، كك و سوخت هسته اي"), ("brent",))
        self.assertEqual(cm.for_company("پتروشیمی جم", "محصولات شيميايي"), ("brent",))

    def test_the_line_is_a_description_with_its_source(self):
        record = {"series": {"copper": rows(4.0, 400, 0.001)}}
        with mock.patch.object(cm, "load", return_value=record):
            lines = cm.company_lines("ملی صنایع مس ایران", "فلزات اساسي")
        self.assertEqual(len(lines), 1)
        self.assertIn("قیمت جهانی مس", lines[0])
        self.assertIn("یک سال", lines[0])
        self.assertIn("نه پیش‌بینی", lines[0])


class FetchTests(unittest.TestCase):

    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".json")
        os.close(handle)
        os.unlink(self.path)
        self.patch = mock.patch.object(cm, "PATH", self.path)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        if os.path.exists(self.path):
            os.unlink(self.path)

    def payload(self, n=150):
        return {"data": [["1", "1", "1", "%d" % (1000 + i), "0", "0",
                          "2025/%02d/%02d" % (1 + i // 28 % 12, 1 + i % 28),
                          "1404/%02d/%02d" % (1 + i // 28 % 12, 1 + i % 28)]
                         for i in range(n)]}

    def test_a_series_that_fails_keeps_the_rows_it_had(self):
        good = mock.Mock(status_code=200)
        good.json.return_value = self.payload()
        good.raise_for_status.return_value = None
        bad = mock.Mock()
        bad.raise_for_status.side_effect = RuntimeError("HTTP 500")
        session = mock.Mock()
        session.get.side_effect = lambda url, timeout: bad if "nickel" in url else good
        previous = {"series": {"nickel": [("2025-01-01", "1403/10/12", 15000.0)]}}
        record = cm.fetch(session=session, previous=previous, pause=0)
        self.assertEqual(record["series"]["nickel"], previous["series"]["nickel"])
        self.assertIn("nickel", record["failed"])
        self.assertEqual(len(record["series"]), len(cm.SERIES))
        self.assertEqual(session.get.call_count, len(cm.SERIES))

    def test_refresh_never_raises_and_keeps_what_is_stored(self):
        session = mock.Mock()
        session.get.side_effect = OSError("no route")
        message = cm.refresh(max_age_hours=0, session=session)
        self.assertIn("fetch failed", message)


class BotLineTests(unittest.TestCase):

    def test_the_market_summary_carries_one_world_line(self):
        rep = {"dollar": {"rates": {"free": {"close": 2_838_900.0}}},
               "commodities": {"items": [
                   {"name": "gold_ounce", "label": "انس طلا", "close": 3573.84, "change_1m": -0.109},
                   {"name": "copper", "label": "مس", "close": 5.39, "change_1m": -0.172}]}}
        line = bot_content.commodities_line(rep)
        self.assertTrue(line.startswith("🌍"))
        self.assertIn("دلار آزاد", line)
        self.assertIn("۵٫۳۹", line)                 # a small price keeps its decimals
        self.assertIn("انس طلا ۳٬۵۷۴", line)
        self.assertEqual(bot_content.commodities_line(None), "")
        text = bot_content.market_text(commodities=rep)
        self.assertIn("🌍", text)


if __name__ == "__main__":
    unittest.main()
