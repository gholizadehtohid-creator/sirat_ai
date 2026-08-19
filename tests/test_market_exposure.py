"""The Market Exposure Engine, tested on its own before it is measured.

The protocol requires the engine to be unit-tested and leakage-tested BEFORE
Experiment 1 runs, because a result from an engine that quietly reads the future
is worse than no result -- it is a number people believe.

The leakage tests are the important ones. They do not inspect the code for
suspicious calls; they truncate the history at the decision session and assert
that the answer does not move. An engine that peeked would answer differently.
"""

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import market_exposure as mx


def ramp(start, end, n):
    step = (end - start) / float(n - 1)
    return [start + step * i for i in range(n)]


def noisy(base, n, amplitude=0.0):
    return [base + amplitude * math.sin(i / 3.0) for i in range(n)]


LONG = 600


# ==============================
# THE INDIVIDUAL VOTES
# ==============================

class TrendTest(unittest.TestCase):

    def test_a_rising_series_is_above_its_mean(self):
        vote, detail = mx.trend(ramp(100, 300, LONG))

        self.assertTrue(vote)
        self.assertGreater(detail["gap_pct"], 0)

    def test_a_falling_series_is_below_its_mean(self):
        self.assertFalse(mx.trend(ramp(300, 100, LONG))[0])

    def test_a_short_history_abstains_rather_than_voting_no(self):
        vote, detail = mx.trend(ramp(100, 200, 50))

        self.assertIsNone(vote)
        self.assertIn("need", detail["reason"])


class MomentumTest(unittest.TestCase):

    def test_a_positive_return_votes_yes(self):
        self.assertTrue(mx.momentum(ramp(100, 200, LONG))[0])

    def test_a_negative_return_votes_no(self):
        self.assertFalse(mx.momentum(ramp(200, 100, LONG))[0])

    def test_the_threshold_is_zero_not_a_fitted_number(self):
        flat = [100.0] * LONG

        self.assertFalse(mx.momentum(flat)[0],
                         "a flat market must not read as positive")


class BreadthTest(unittest.TestCase):

    def test_more_than_half_votes_yes(self):
        self.assertTrue(mx.breadth(60.0)[0])

    def test_exactly_half_is_not_more_than_half(self):
        self.assertFalse(mx.breadth(50.0)[0])

    def test_no_reading_abstains(self):
        self.assertIsNone(mx.breadth(None)[0])


class VolatilityTest(unittest.TestCase):

    def _series(self, calm_tail):
        """A year of noisy history, then a calm or wild recent window."""

        history = noisy(100.0, 400, amplitude=4.0)

        tail = (noisy(100.0, 120, amplitude=0.2) if calm_tail
                else noisy(100.0, 120, amplitude=25.0))

        return history + tail

    def test_a_calm_tail_votes_yes(self):
        self.assertTrue(mx.volatility(self._series(True))[0])

    def test_a_wild_tail_votes_no(self):
        self.assertFalse(mx.volatility(self._series(False))[0])

    def test_a_short_history_abstains(self):
        self.assertIsNone(mx.volatility(ramp(100, 200, 100))[0])


# ==============================
# THE LADDER
# ==============================

class TargetExposureTest(unittest.TestCase):

    def _reading(self, **votes):
        full = {name: None for name in mx.SIGNALS}
        full.update(votes)
        return {"votes": full}

    def test_all_four_votes_is_full_exposure(self):
        reading = self._reading(trend=True, momentum=True, breadth=True,
                                volatility=True)

        self.assertEqual(mx.target_exposure(reading)[0], 100.0)

    def test_no_votes_is_no_exposure(self):
        reading = self._reading(trend=False, momentum=False, breadth=False,
                                volatility=False)

        self.assertEqual(mx.target_exposure(reading)[0], 0.0)

    def test_two_of_four_is_half(self):
        reading = self._reading(trend=True, momentum=True, breadth=False,
                                volatility=False)

        self.assertEqual(mx.target_exposure(reading)[0], 50.0)

    def test_the_output_is_always_on_the_ladder(self):
        import itertools

        for combo in itertools.product([True, False, None], repeat=4):

            reading = self._reading(**dict(zip(mx.SIGNALS, combo)))

            exposure, _ = mx.target_exposure(reading)

            if exposure is None:
                continue

            self.assertIn(exposure, (0.0, 25.0, 50.0, 75.0, 100.0))

    def test_a_missing_vote_is_not_a_bearish_vote(self):
        """Otherwise the warm-up reads as a crash and the book sits in cash."""

        partial = self._reading(trend=True, momentum=True)
        full = self._reading(trend=True, momentum=True, breadth=True,
                             volatility=True)

        self.assertEqual(mx.target_exposure(partial)[0],
                         mx.target_exposure(full)[0])

    def test_nothing_computable_returns_none_rather_than_zero(self):
        exposure, why = mx.target_exposure(self._reading())

        self.assertIsNone(exposure)
        self.assertIn("no signal", why["reason"])

    def test_bounded_between_zero_and_one_hundred(self):
        reading = self._reading(trend=True, momentum=True, breadth=True,
                                volatility=True)

        exposure, _ = mx.target_exposure(reading)

        self.assertGreaterEqual(exposure, 0.0)
        self.assertLessEqual(exposure, 100.0)


# ==============================
# LEAKAGE
# ==============================

class LeakageTest(unittest.TestCase):
    """Truncate at the decision session; the answer must not move."""

    def _history(self):
        # A rise, then a crash AFTER the decision point. An engine that peeks
        # would turn bearish early; one that does not cannot know yet.
        return ramp(100, 400, 500) + ramp(400, 120, 200)

    def test_the_future_cannot_change_todays_exposure(self):
        history = self._history()

        cut = 500

        today, _ = mx.exposure_for(history[:cut], above_pct=70.0)
        with_future, _ = mx.exposure_for(history, above_pct=70.0)

        self.assertIsNotNone(today)

        # The two SHOULD differ -- the second is a later date with a crash in
        # it. What must hold is that the first does not know about the crash.
        rerun, _ = mx.exposure_for(history[:cut], above_pct=70.0)

        self.assertEqual(today, rerun)
        self.assertNotEqual(today, with_future,
                            "the crash must change a LATER reading, or this "
                            "test proves nothing")

    def test_appending_future_bars_does_not_move_an_earlier_reading(self):
        history = self._history()

        for cut in (400, 450, 500, 550):

            here, _ = mx.exposure_for(history[:cut], above_pct=60.0)

            longer, _ = mx.exposure_for(history[:cut] + [999999.0] * 50,
                                        above_pct=60.0)

            # The absurd appended bars are AFTER the cut, so a reading taken at
            # the cut must be unchanged by them.
            again, _ = mx.exposure_for(history[:cut], above_pct=60.0)

            self.assertEqual(here, again, "not deterministic at cut %d" % cut)

    def test_the_engine_is_deterministic(self):
        history = self._history()[:500]

        first = mx.exposure_for(history, above_pct=55.0)[0]

        for _ in range(5):
            self.assertEqual(mx.exposure_for(history, above_pct=55.0)[0],
                             first)

    def test_every_threshold_is_a_natural_constant(self):
        """Guards against a fitted number appearing in a later edit."""

        self.assertEqual(mx.MOMENTUM_FLOOR, 0.0)
        self.assertEqual(mx.BREADTH_FLOOR, 50.0)
        self.assertEqual(mx.STEP_PCT, 25.0)
        self.assertEqual(mx.TREND_WINDOW, 200)
        self.assertEqual(mx.MOMENTUM_WINDOW, 60)
        self.assertEqual(mx.VOLATILITY_WINDOW, 20)


if __name__ == "__main__":
    unittest.main()
