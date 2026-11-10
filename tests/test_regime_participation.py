"""regime.participation: the share of LIVE EQUITIES above their slow MA.

Three things used to sit in the count that are not the stock market's breadth:
the gold funds, names halted for over a month (a frozen close equals its own MA
once the freeze outlasts it, so it can never count as above), and unadjusted
capital-increase gaps. And because detect(as_of=...) must be reproducible for a
past date, the halt rule is applied as of that date rather than from today's
halt list.
"""

import os
import sys
import tempfile
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database  # noqa: E402
import regime  # noqa: E402


SESSIONS = 130


class ParticipationTests(unittest.TestCase):

    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.original = database.DATABASE_NAME
        database.DATABASE_NAME = self.path
        database.create_database()

        self.dates = pd.bdate_range("2026-05-01", periods=SESSIONS)
        rising = [1000.0 + 5 * i for i in range(SESSIONS)]

        database.save_instruments(
            [{"ins_code": code, "symbol_fa": code, "kind": "equity",
              "is_equity": True}
             for code in ("up", "down", "frozen", "split")]
            + [{"ins_code": "gold", "symbol_fa": "gold", "kind": "fund",
                "is_equity": False}])

        self._save("up", rising)
        self._save("down", [2000.0 - 5 * i for i in range(SESSIONS)])

        # A rising fund: above its MA, and not a share.
        self._save("gold", rising)

        # Traded and rose for 60 sessions, then halted: 70 more bars at the
        # frozen close and zero volume, so close == MA50 exactly at the end.
        self.halt_at = 60
        frozen = rising[:self.halt_at] + [rising[self.halt_at - 1]] * (
            SESSIONS - self.halt_at)
        volume = [1000.0] * self.halt_at + [0.0] * (SESSIONS - self.halt_at)
        self._save("frozen", frozen, volume)

        # Rising throughout, with a 100% capital increase at session 110 that
        # halves the quoted price overnight. Raw, it reads as below its MA.
        self._save("split", [c / 2.0 if i >= 110 else c
                             for i, c in enumerate(rising)])

    def tearDown(self):
        database.DATABASE_NAME = self.original
        os.unlink(self.path)

    def _save(self, code, closes, volume=None):
        database.save_history(code, pd.DataFrame({
            "date": self.dates, "open": closes, "high": closes, "low": closes,
            "close": closes,
            "volume": volume if volume is not None else [1000.0] * len(closes),
            "value": 1e9,
        }))

    def test_only_live_equities_on_adjusted_closes_are_counted(self):
        reading = regime.participation()

        # up, down and split. Not gold (a fund), not frozen (halted 70
        # sessions). split counts as ABOVE: adjusted, it rose throughout.
        self.assertEqual(reading["instruments"], 3)
        self.assertAlmostEqual(reading["above_slow_ma_pct"], 66.67, places=2)

    def test_the_halt_rule_is_point_in_time(self):
        # Six sessions into the halt the name had traded within the month, so
        # a read for that date counts it -- even though it is halted today.
        # Today's halt list would have dropped it and changed a past reading.
        reading = regime.participation(as_of=self.dates[self.halt_at + 5])

        self.assertEqual(reading["instruments"], 4)
        self.assertAlmostEqual(reading["above_slow_ma_pct"], 75.0, places=2)

    def test_a_cutoff_past_the_newest_session_halts_nothing(self):
        # The clock is the newest session, not the cutoff: asking for a date
        # two months after the last fetch must not age every name past the
        # halt limit.
        later = self.dates[-1] + pd.Timedelta(days=60)

        self.assertEqual(regime.participation(as_of=later),
                         regime.participation())

    def test_a_past_date_counts_a_listing_a_board_move_retired_since(self):
        """seed_import.reconcile_universe retires the old code of a board
        move (kind superseded, is_equity 0). It was the market on the dates
        it traded; today's reading is of the live codes only."""

        database.save_instruments([{
            "ins_code": "moved", "symbol_fa": "moved", "kind": "superseded",
            "is_equity": False}])
        self._save("moved", [1000.0 + 5 * i for i in range(SESSIONS)])

        past = regime.participation(as_of=self.dates[-1])

        self.assertEqual(past["instruments"], 4)   # up, down, split, moved
        self.assertEqual(regime.participation()["instruments"], 3)

    def test_detect_votes_on_the_corrected_reading(self):
        breadth = regime.participation()

        report = regime.assemble(None, None, breadth)

        # One input only, and it is participation's: 66.67% is a BULL vote,
        # where the raw count of all five series (40%) was a SIDEWAYS one.
        self.assertEqual(report["votes"]["bull"], 1)
        self.assertEqual(report["participation"]["instruments"], 3)


if __name__ == "__main__":
    unittest.main()
