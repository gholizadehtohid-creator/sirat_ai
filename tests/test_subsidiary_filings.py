"""A parent's subsidiary statements are not the parent's statements.

Since 1405 a parent publishes its subsidiaries' statements under its own
symbol, the subsidiary named in brackets in the title. Stored as the parent's,
one replaced فولاد's latest period with a football club's (2026-11-10).
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import codal  # noqa: E402
import codal_backfill  # noqa: E402
import codal_sync  # noqa: E402
import database  # noqa: E402
import financials  # noqa: E402
import fundamental_db  # noqa: E402

OWN = ("اطلاعات و صورتهای مالی میاندورهای دوره 6 ماهه منتهی به 1405/06/31 "
       "(حسابرسی نشده)")
CLUB = ("اطلاعات و صورتهای مالی میاندورهای دوره 6 ماهه منتهی به 1405/06/31 "
        "(حسابرسی شده) (شرکت باشگاه فرهنگی ورزشی سپاهان نوین)")


class TitleTests(unittest.TestCase):

    def test_the_subsidiary_is_named_in_brackets(self):
        self.assertEqual(financials.subsidiary_of(CLUB),
                         "شرکت باشگاه فرهنگی ورزشی سپاهان نوین")

    def test_the_issuers_own_qualifiers_are_not_a_subsidiary(self):
        for title in (OWN, OWN + "(اصلاحیه)",
                      "صورتهای مالی تلفیقی سال مالی منتهی به 1404/12/29 "
                      "(حسابرسی شده) (شامل مقادیر براوردی)"):
            self.assertIsNone(financials.subsidiary_of(title), title)

    def test_a_private_joint_stock_tag_is_a_subsidiary(self):
        self.assertIsNotNone(financials.subsidiary_of(OWN + " (سهامی خاص)"))

    def test_it_is_refused_before_anything_is_downloaded(self):
        client = mock.Mock()
        record, error, kind = codal_sync.parse_letter(
            client, {"tracing_no": "9", "title": CLUB, "excel_url": "u"})
        self.assertIsNone(record)
        self.assertEqual(kind, codal.KIND_BAD)
        self.assertIn("subsidiary", error)
        client.fetch_sheets.assert_not_called()


class PurgeTests(unittest.TestCase):

    def setUp(self):
        handle, self.market = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        handle, self.store = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        os.unlink(self.store)
        self.original = database.DATABASE_NAME
        database.DATABASE_NAME = self.market
        self.previous = fundamental_db.set_path(self.store)
        database.create_database()

        def filing(tracing, title, revenue, published):
            record = {"tracing_no": tracing, "period_end": "1405/06/31",
                      "period_months": 6, "title": title,
                      "items": {"revenue": revenue}, "ratios": {}}
            database.save_codal_filing("P", record, published_at=published)
            database.save_codal_financials("P", record)

        filing("own", OWN, 1000.0, "1405/07/30 10:00:00")
        # Published later, so it took the period's place everywhere.
        filing("club", CLUB, 3.0, "1405/08/18 13:00:00")
        fundamental_db.sync_from_market_db()

    def tearDown(self):
        database.DATABASE_NAME = self.original
        fundamental_db.set_path(self.previous)
        for path in (self.market, self.store):
            if os.path.exists(path):
                os.unlink(path)

    def revenue_in_store(self):
        return fundamental_db.load_metrics("P", limit=5)[0]["items"]["revenue"]

    def test_the_club_held_the_period_before_the_purge(self):
        self.assertEqual(self.revenue_in_store(), 3.0)

    def test_the_purge_puts_the_issuers_own_figures_back(self):
        result = codal_backfill.purge_subsidiaries(verbose=False)

        self.assertEqual(result["removed"], 1)
        self.assertEqual(self.revenue_in_store(), 1000.0)
        self.assertEqual({r["tracing_no"] for r in database.load_codal_filings()},
                         {"own"})
        self.assertIn("club", codal_backfill.settled_failures())

        conn = database._connect()
        held = conn.execute("SELECT tracing_no FROM codal_financials").fetchall()
        conn.close()
        self.assertEqual(held, [("own",)])

    def test_the_seed_step_does_not_bring_it_back(self):
        codal_backfill.purge_subsidiaries(verbose=False)
        codal_backfill.seed_from_financials(verbose=False)
        self.assertNotIn("club", {r["tracing_no"]
                                  for r in database.load_codal_filings()})

    def test_running_it_twice_changes_nothing(self):
        codal_backfill.purge_subsidiaries(verbose=False)
        again = codal_backfill.purge_subsidiaries(verbose=False)
        self.assertEqual(again["removed"], 0)
        self.assertEqual(self.revenue_in_store(), 1000.0)


if __name__ == "__main__":
    unittest.main()
