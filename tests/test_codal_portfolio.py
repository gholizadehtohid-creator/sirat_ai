"""codal_portfolio: which investment companies are behind, and what is fetched.

The user, 2026-11-11: why does every NAV date from the end of Tir? 130 of
141 portfolios were 1405/04/31; ن-۳۱ was not watched and the per-company
pass had not reached them since August. The adversarial review before the
first run (16 confirmed findings) is pinned here. No request leaves these
tests.
"""

import os
import sys
import unittest
from datetime import datetime, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import codal  # noqa: E402
import codal_monthly  # noqa: E402
import codal_portfolio as cp  # noqa: E402
import config  # noqa: E402

INVEST = sorted(config.EARNINGS_MULTIPLE_NOT_MEANINGFUL)[0]
OK = codal.KIND_OK


def inst(code, sector):
    return {"ins_code": code, "symbol_fa": "s" + code, "sector_name": sector}


def letter(tracing, period, published, correction=False):
    title = "گزارش فعالیت ماهانه دوره ۱ ماهه منتهی به %s%s" % (
        period, "(اصلاحیه)" if correction else "")
    return {"tracing_no": tracing, "title": title, "excel_url": "x" + tracing,
            "published_at": published, "symbol": "s1", "letter_code": "ن-31"}


class DueTests(unittest.TestCase):

    def due(self, held, status=None):
        with mock.patch.object(cp, "_ensure_tables"), \
                mock.patch.object(cp, "want_period", return_value="1405/07"), \
                mock.patch.object(cp.database, "load_instruments", return_value=[
                    inst("1", INVEST), inst("2", INVEST), inst("3", "فلزات اساسي"),
                    inst("4", "فلزات اساسي"), inst("5", INVEST)]), \
                mock.patch.object(cp.database, "load_codal_portfolio_latest",
                                  return_value=held), \
                mock.patch.object(cp, "_status_rows", return_value=status or {}):
            return [i["ins_code"] for i in cp.due_companies(
                now=datetime(2026, 11, 11, 20, tzinfo=timezone.utc))]

    def test_behind_filers_first_then_asset_valued_names_never_seen(self):
        held = {"1": {"period_end": "1405/04/31"}, "4": {"period_end": "1405/05/31"},
                "5": {"period_end": "1405/07/30"}}
        self.assertEqual(self.due(held), ["1", "4", "2"])

    def test_recent_checks_wait_and_non_filers_wait_a_week(self):
        held = {"1": {"period_end": "1405/04/31"}, "4": {"period_end": "1405/04/31"}}
        status = {"1": ("2026-11-11T10:00:00+00:00", "1405/04/31", 20, "nothing newer"),
                  "2": ("2026-11-08T10:00:00+00:00", None, 0, None),
                  "4": ("2026-11-09T10:00:00+00:00", None, 12, "no portfolio")}
        self.assertEqual(self.due(held, status), ["5"])


class FetchNewestTests(unittest.TestCase):

    def fetch(self, letters, answers, held=None, known=(), want="1405/07"):
        asked, saved = [], []

        def parse(_client, item):
            asked.append(item["tracing_no"])
            return answers[item["tracing_no"]]

        with mock.patch("codal_sync.parse_portfolio_letter", side_effect=parse), \
                mock.patch.object(cp.database, "known_tracing_numbers",
                                  return_value=set(known)), \
                mock.patch.object(cp.database, "mark_letter_parsed"), \
                mock.patch.object(cp.database, "save_codal_portfolio",
                                  side_effect=lambda code, rec: saved.append(rec) or 1):
            out = cp.fetch_newest(None, "c", letters, newest_held=held, want=want)
        return out, asked, saved

    def test_the_newest_is_stored_and_nothing_older_asked(self):
        letters = [letter("30", "1405/07/30", "1405/08/10"),
                   letter("29", "1405/06/31", "1405/07/10")]
        out, asked, saved = self.fetch(
            letters, {"30": ({"period_end": "1405/07/30"}, None, OK)}, held="1405/04/31")
        self.assertEqual((out["stored"], asked), (1, ["30"]))
        self.assertEqual(saved[0]["published_at"], "1405/08/10")

    def test_a_correction_of_the_month_held_is_downloaded(self):
        """The review: `<=` skipped every same-month اصلاحیه."""
        letters = [letter("31", "1405/07/30", "1405/08/14", correction=True),
                   letter("30", "1405/07/30", "1405/08/10")]
        out, asked, _ = self.fetch(
            letters, {"31": ({"period_end": "1405/07/30"}, None, OK)}, held="1405/07/30")
        self.assertEqual((out["stored"], asked), (1, ["31"]))

    def test_the_original_of_the_month_held_is_not_downloaded_again(self):
        out, asked, _ = self.fetch([letter("30", "1405/07/30", "1405/08/10")], {},
                                   held="1405/07/30")
        self.assertEqual((out["requested"], asked), (0, []))

    def test_a_late_correction_of_an_old_month_does_not_hide_a_newer_report(self):
        """The review: a correction of 1405/02 published after 1405/03 broke the walk."""
        letters = [letter("22", "1405/02/31", "1405/04/20", correction=True),
                   letter("33", "1405/03/31", "1405/04/10")]
        out, asked, _ = self.fetch(
            letters, {"33": ({"period_end": "1405/03/31"}, None, OK)}, held="1405/02/31")
        self.assertEqual((out["stored"], asked), (1, ["33"]))

    def test_only_the_newest_few_letters_are_looked_at(self):
        """The review: with nothing held, the walk went back three downloads a run."""
        letters = [letter(str(40 - i), "1405/07/30", "1405/08/%02d" % (10 - i))
                   for i in range(6)]
        none = (None, None, OK)
        out, asked, _ = self.fetch(letters, {t["tracing_no"]: none for t in letters},
                                   known={"40"})
        self.assertEqual(asked, ["39", "38"])            # 40 is read; window of 3
        self.assertEqual(out["outcome"], "no portfolio")

    def test_with_nothing_held_an_old_report_is_never_stored_as_today(self):
        out, asked, _ = self.fetch([letter("10", "1403/12/29", "1404/01/20")], {})
        self.assertEqual((out["requested"], asked), (0, []))

    def test_a_refusal_stops_and_says_so(self):
        out, _asked, saved = self.fetch([letter("30", "1405/07/30", "1405/08/10")],
                                        {"30": (None, "429", codal.KIND_TRANSIENT)},
                                        held="1405/04/31")
        self.assertTrue(out["transient"])
        self.assertEqual(saved, [])

    def test_an_unreadable_report_is_named_not_hidden(self):
        """The review: a KIND_BAD failure read as 'not newer, or no portfolio'."""
        out, _asked, _ = self.fetch([letter("30", "1405/07/30", "1405/08/10")],
                                    {"30": (None, "30: no period end", codal.KIND_BAD)},
                                    held="1405/04/31")
        self.assertEqual((out["outcome"], out["error"]), ("unreadable", "30: no period end"))


class RunTests(unittest.TestCase):

    def test_three_refused_downloads_end_the_pass_whatever_the_searches_say(self):
        """The review: a good search reset the count, and nothing cooled off."""
        companies = [inst(str(i), INVEST) for i in range(10)]
        searched = []

        def search(_client, symbol):
            searched.append(symbol)
            return [letter("30", "1405/07/30", "1405/08/10")], None, None

        with mock.patch.object(cp, "_ensure_tables"), \
                mock.patch.object(cp, "due_companies", return_value=companies), \
                mock.patch.object(cp, "held_period", return_value="1405/04/31"), \
                mock.patch.object(cp, "search", side_effect=search), \
                mock.patch.object(cp, "_set_status") as status, \
                mock.patch.object(cp.database, "save_codal_letters"), \
                mock.patch.object(cp, "fetch_newest", return_value={
                    "stored": 0, "transient": True, "requested": 1, "error": None,
                    "outcome": "refused"}), \
                mock.patch("codal_sync.codal_symbol", side_effect=lambda i: i["symbol_fa"]):
            totals = cp.run(hours=None, limit=None, verbose=False, client=object())
        self.assertEqual(len(searched), cp.DOWNLOAD_REFUSALS_STOP)
        self.assertEqual(totals["failed"], cp.DOWNLOAD_REFUSALS_STOP)
        status.assert_not_called()                  # due again next pass

    def test_a_cool_off_that_would_pass_the_deadline_stops_instead(self):
        import time
        pace = codal_monthly.Pacer("t", verbose=False, deadline=time.time() + 60)
        with mock.patch.object(codal_monthly.time, "sleep") as sleep:
            for _ in range(3):
                pace(True)
        sleep.assert_not_called()
        self.assertTrue(pace.stop)


class WatchTests(unittest.TestCase):

    def test_the_watch_follows_portfolio_reports(self):
        import codal_watch
        self.assertIn("ن-31", codal_watch.FEEDS)
        self.assertIn("ن-31", codal_watch.NETWORK_CODES)
        self.assertIs(codal_watch.HANDLERS["ن-31"], codal_watch.handle_portfolio)

    def test_a_request_free_outcome_is_not_paced_and_says_nothing_of_refusals(self):
        import codal_watch
        paced = []
        totals = {"stored": 0, "unreadable": 0, "retry_later": 0, "given_up": 0,
                  "outside_universe": 0}
        item = dict(letter("30", "1405/07/30", "1405/08/10"))
        with mock.patch.dict(codal_watch.HANDLERS, {
                "ن-31": lambda c, l, i: codal_watch.NoRequest("already stored")}), \
                mock.patch.object(codal_watch.database, "save_codal_letters"), \
                mock.patch.object(codal_watch, "_mark"), \
                mock.patch.object(codal_watch, "_unqueue"):
            outcome, refused = codal_watch._process(
                None, item, {"s1": {"ins_code": "1"}}, paced.append, totals, False)
        self.assertEqual((outcome, refused, paced), ("already stored", None, []))

    def test_only_each_companys_newest_report_is_downloaded_in_a_poll(self):
        """The review: a --since catch-up fetched every intermediate month."""
        import codal_watch
        fresh = [letter("31", "1405/07/30", "1405/08/14", correction=True),
                 letter("30", "1405/07/30", "1405/08/10"),
                 letter("29", "1405/06/31", "1405/07/10"),
                 dict(letter("x1", "1405/06/31", "1405/07/10"), letter_code="ن-10")]
        totals = {"new": 0}
        with mock.patch.object(codal_watch.database, "save_codal_letters"), \
                mock.patch.object(codal_watch, "_mark") as mark, \
                mock.patch.object(codal_watch, "_unqueue"):
            kept = codal_watch._newest_portfolio_per_company(
                fresh, {"s1": {"ins_code": "1"}}, totals)
        self.assertEqual([l["tracing_no"] for l in kept], ["31", "x1"])
        self.assertEqual(mark.call_count, 2)


if __name__ == "__main__":
    unittest.main()
