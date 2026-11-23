"""codal_monthly's history phase: known letters of missing months, no searches."""

import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import codal_monthly  # noqa: E402
import database  # noqa: E402
import fundamental_db  # noqa: E402

TITLE = "گزارش فعالیت ماهانه دوره 1 ماهه منتهی به %s"


class HistoryTests(unittest.TestCase):

    def setUp(self):
        handle, self.store = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        os.unlink(self.store)
        handle, self.market = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.previous = fundamental_db.set_path(self.store)
        conn = sqlite3.connect(self.market)
        conn.execute("""CREATE TABLE codal_letters (tracing_no TEXT PRIMARY KEY,
            ins_code TEXT, symbol TEXT, letter_code TEXT, title TEXT,
            published_at TEXT, url TEXT, excel_url TEXT, has_excel INTEGER,
            parsed INTEGER, seen_at TEXT)""")
        letters = [
            # company 1: three months known, one already stored
            ("t1", "1", "ن-30", TITLE % "1405/07/30", "1405/08/05"),
            ("t2", "1", "ن-30", TITLE % "1405/06/31", "1405/07/05"),
            ("t3", "1", "ن-30", TITLE % "1405/05/31", "1405/06/05"),
            ("t3b", "1", "ن-30", TITLE % "1405/05/31", "1405/06/20"),   # its correction
            ("old", "1", "ن-30", TITLE % "1403/01/31", "1403/02/05"),   # outside the window
            ("st", "1", "ن-10", "صورت مالی", "1405/08/01"),            # not a monthly report
            # company 2: one month known
            ("u1", "2", "ن-30", TITLE % "1405/07/30", "1405/08/05"),
            # a fund: not an equity
            ("f1", "9", "ن-30", TITLE % "1405/07/30", "1405/08/05"),
        ]
        conn.executemany("INSERT INTO codal_letters VALUES (?,?,?,?,?,?,?,?,1,0,'')",
                         [(t, c, "s" + c, lc, title, pub, "u", "x/" + t)
                          for t, c, lc, title, pub in letters])
        conn.commit()
        conn.close()
        self.patches = [
            mock.patch.object(database, "DATABASE_NAME", self.market),
            mock.patch.object(database, "load_instruments",
                              return_value=[{"ins_code": "1"}, {"ins_code": "2"}]),
        ]
        for p in self.patches:
            p.start()
        codal_monthly._ensure_tables()
        codal_monthly.store("1", {"tracing_no": "t1", "published_at": "1405/08/05",
                                  "title": TITLE % "1405/07/30"},
                            {"period_end": "1405/07/30", "template": "manufacturer"})

    def tearDown(self):
        for p in self.patches:
            p.stop()
        fundamental_db.set_path(self.previous)
        for path in (self.store, self.market):
            for suffix in ("", "-wal", "-shm"):
                if os.path.exists(path + suffix):
                    try:
                        os.unlink(path + suffix)
                    except OSError:
                        pass

    def test_missing_months_with_a_known_letter_emptiest_first(self):
        due = codal_monthly.history_due(15, today=(1405, 9, 2))
        self.assertEqual([c for c, _l in due], ["1", "2"])
        months = [l["title"][-10:] for l in due[0][1]]
        self.assertEqual(months, ["1405/06/31", "1405/05/31"])     # newest first
        # the correction, published later, is the one fetched
        self.assertEqual(due[0][1][1]["tracing_no"], "t3b")

    def test_the_run_downloads_no_more_than_its_limit_and_searches_nothing(self):
        client = mock.Mock()
        client.fetch_sheets.return_value = mock.Mock(ok=True, data={"s": []},
                                                     transient=False, error=None)
        with mock.patch.object(codal_monthly, "due_companies", return_value=[]), \
                mock.patch.object(codal_monthly, "last_closed_month",
                                  return_value=(1405, 8)), \
                mock.patch.object(codal_monthly, "search",
                                  side_effect=AssertionError("searched")), \
                mock.patch.object(codal_monthly.monthly_reports, "parse",
                                  side_effect=lambda _d, title: {
                                      "period_end": title[-10:], "template": "m"}), \
                mock.patch.object(codal_monthly, "Pacer",
                                  return_value=mock.Mock(stop=False)):
            totals = codal_monthly.run(verbose=False, client=client,
                                       history_months=15, history_limit=2)
        self.assertEqual(client.fetch_sheets.call_count, 2)
        self.assertEqual(totals["history_stored"], 2)

    def test_zero_months_turns_the_phase_off(self):
        self.assertEqual(codal_monthly.history_due(0), [])


if __name__ == "__main__":
    unittest.main()
