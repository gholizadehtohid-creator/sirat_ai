"""Portfolio construction: sizing, sector diversity and the budget it must fit.

The regression this file exists for: per-position caps cannot keep a portfolio
solvent on their own. Ten names each allowed 15% of equity sum to 150%, and the
first working version of portfolio.py produced exactly that -- a basket worth
1.43x the account, every individual position within its limit.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import portfolio


EQUITY = 1_000_000_000


def candidate(symbol, sector, score, close=1000.0, atr_pct=2.0,
              adv=50_000_000_000, verdict="APPROVED", scored=True):
    """A screen row shaped the way pipeline.run_scan emits them."""

    return {
        "ins_code": f"code-{symbol}",
        "symbol": symbol,
        "name": f"{symbol} Co",
        "sector_name": sector,
        "verdict": verdict,
        "fundamental_score": score,
        "combined_score": score,
        "technical": {"score": score, "scored": scored,
                      "status": "STRONG BUY CANDIDATE", "trend": "Uptrend"},
        "metrics": {
            "close": close,
            "atr_pct": atr_pct,
            "volatility_60": atr_pct * 15,
            "avg_value_20": adv,
            "pe": 5.0,
        },
    }


def spread(n, sectors=None, base=95):
    """n candidates, one per sector unless `sectors` forces collisions."""

    rows = []
    for i in range(n):
        sector = sectors[i] if sectors else f"صنعت {i}"
        rows.append(candidate(f"SYM{i}", sector, base - i))
    return rows


class TestBudget(unittest.TestCase):

    def test_positions_never_exceed_equity(self):
        result = portfolio.build(spread(10), equity=EQUITY)

        invested = sum(p["position_value"] for p in result["positions"])

        self.assertLessEqual(invested, EQUITY,
                             "a basket may never cost more than the account")

    def test_cash_floor_is_respected(self):
        result = portfolio.build(spread(10), equity=EQUITY)

        invested_pct = result["totals"]["invested_pct"]

        self.assertLessEqual(invested_pct, 100 - config.MIN_CASH_PCT + 0.5)

    def test_regime_cash_target_overrides_the_default_floor(self):
        bear = {"available": True, "regime": "BEAR", "label": "بازار نزولی",
                "cash_target_pct": 60.0, "confidence": 1.0}

        result = portfolio.build(spread(10), equity=EQUITY, market_regime=bear)

        self.assertLessEqual(result["totals"]["invested_pct"], 40.5)
        self.assertIsNotNone(result["market_regime"])
        self.assertEqual(result["market_regime"]["regime"], "BEAR")

    def test_no_position_exceeds_the_weight_cap(self):
        result = portfolio.build(spread(10), equity=EQUITY)

        for position in result["positions"]:
            self.assertLessEqual(position["weight_pct"],
                                 config.MAX_POSITION_PCT + 0.01)

    def test_no_position_below_the_floor(self):
        result = portfolio.build(spread(10), equity=EQUITY)

        for position in result["positions"]:
            self.assertGreaterEqual(position["weight_pct"],
                                    config.MIN_POSITION_PCT)


class TestSectorDiversity(unittest.TestCase):

    def test_sector_cap_is_enforced(self):
        # Twelve names, all in three sectors: the cap must bind.
        sectors = ["بانک"] * 4 + ["فلزات"] * 4 + ["پتروشیمی"] * 4

        result = portfolio.build(
            spread(12, sectors=sectors), equity=EQUITY, max_per_sector=2
        )

        counts = {}
        for position in result["positions"]:
            counts[position["sector_name"]] = counts.get(position["sector_name"], 0) + 1

        for sector, n in counts.items():
            self.assertLessEqual(n, 2, f"{sector} exceeded the per-sector cap")

    def test_widest_spread_is_preferred_over_pure_score(self):
        """One name per sector before any sector gets a second seat.

        Sorted purely by score the basket would be the top two of سرمایه‌گذاری
        before it ever reached فلزات, which is one position wearing two names.
        """

        # D scores well below the others but still clears the technical gate:
        # the point is that a *weaker* name from a second sector beats a third
        # name from the first, not that an unqualified name gets a free pass.
        rows = [
            candidate("A", "بانک", 95),
            candidate("B", "بانک", 94),
            candidate("C", "بانک", 93),
            candidate("D", "فلزات", 75),
        ]

        result = portfolio.build(rows, equity=EQUITY, size=3,
                                 max_per_sector=2, min_score=50)

        picked = {p["symbol"] for p in result["positions"]}

        self.assertIn("D", picked,
                      "the only name from a second sector must be taken")
        self.assertNotIn("C", picked,
                         "a third name from one sector must not displace it")

    def test_sector_total_respects_max_sector_pct(self):
        sectors = ["بانک"] * 6 + [f"صنعت {i}" for i in range(4)]

        result = portfolio.build(
            spread(10, sectors=sectors), equity=EQUITY, max_per_sector=6
        )

        by_sector = {}
        for p in result["positions"]:
            by_sector[p["sector_name"]] = by_sector.get(p["sector_name"], 0) + p["position_value"]

        for sector, value in by_sector.items():
            self.assertLessEqual(
                value / EQUITY * 100, config.MAX_SECTOR_PCT + 0.5,
                f"{sector} holds more than the sector cap allows"
            )


class TestRiskSizing(unittest.TestCase):

    def test_volatile_names_get_smaller_positions(self):
        """Equal score, quadruple volatility -> a materially smaller position."""

        rows = [
            candidate("CALM", "صنعت الف", 90, atr_pct=1.0),
            candidate("WILD", "صنعت ب", 90, atr_pct=8.0),
        ]

        result = portfolio.build(rows, equity=EQUITY, size=2)

        sized = {p["symbol"]: p for p in result["positions"]}

        self.assertLess(sized["WILD"]["position_value"],
                        sized["CALM"]["position_value"])

    def test_each_position_risks_about_the_same(self):
        rows = [
            candidate("A", "صنعت الف", 90, atr_pct=2.0),
            candidate("B", "صنعت ب", 90, atr_pct=5.0),
            candidate("C", "صنعت ج", 90, atr_pct=9.0),
        ]

        result = portfolio.build(rows, equity=EQUITY, size=3, risk_pct=1.0)

        risks = [p["risk_pct"] for p in result["positions"]]

        # Scaling to the cash floor moves them together, so compare spread not
        # absolute level: no position should risk more than 2x another.
        self.assertLess(max(risks) / min(risks), 2.0)

    def test_stop_and_target_bracket_the_entry(self):
        result = portfolio.build(spread(5), equity=EQUITY, size=5)

        for p in result["positions"]:
            self.assertLess(p["stop_price"], p["close"])
            self.assertGreater(p["target_price"], p["close"])

    def test_reward_to_risk_matches_config(self):
        result = portfolio.build(spread(5), equity=EQUITY, size=5)

        for p in result["positions"]:
            reward = p["target_price"] - p["close"]
            risk = p["close"] - p["stop_price"]
            self.assertAlmostEqual(reward / risk, config.MIN_RISK_REWARD,
                                   places=4)

    def test_liquidity_cap_binds_on_thin_names(self):
        """A position may never exceed its share of daily traded value."""

        rows = [candidate("THIN", "صنعت الف", 95, adv=100_000_000)]

        result = portfolio.build(rows, equity=EQUITY, size=1, min_score=50)

        if result["positions"]:
            position = result["positions"][0]
            self.assertLessEqual(
                position["adv_participation_pct"],
                config.MAX_ADV_PARTICIPATION_PCT + 0.01
            )

    def test_falls_back_to_volatility_when_atr_is_missing(self):
        row = candidate("NOATR", "صنعت الف", 90)
        row["metrics"]["atr_pct"] = None

        result = portfolio.build([row], equity=EQUITY, size=1, min_score=50)

        self.assertEqual(len(result["positions"]), 1)
        self.assertEqual(result["positions"][0]["stop_source"],
                         "realised volatility")


class TestSelectionMode(unittest.TestCase):
    """Ranking by flow once the technical gate is cleared.

    The measurement behind this: the technical score does not sort by outcome
    above its own threshold, while the flow score does. So a name with a lower
    technical score but stronger flow must outrank a technically prettier one,
    which is precisely the behaviour the old blend could not express.
    """

    def _rows(self):
        strong_tech = candidate("TECH", "صنعت الف", 95)
        strong_tech["flow_score"] = 20
        strong_tech["rank_score"] = 20
        strong_tech["rank_eligible"] = True
        strong_tech["selection"] = {"basis": "flow"}

        strong_flow = candidate("FLOW", "صنعت ب", 75)
        strong_flow["flow_score"] = 95
        strong_flow["rank_score"] = 95
        strong_flow["rank_eligible"] = True
        strong_flow["selection"] = {"basis": "flow"}

        return [strong_tech, strong_flow]

    def test_flow_outranks_a_higher_technical_score(self):
        if getattr(config, "SELECTION_MODE", "blend") != "gate_then_flow":
            self.skipTest("blend mode active")

        result = portfolio.build(self._rows(), equity=EQUITY, size=2,
                                 max_per_sector=1, min_score=50)

        ranked = [p["symbol"] for p in result["positions"]]

        self.assertEqual(ranked[0], "FLOW",
                         "stronger flow must rank first once both clear the gate")

    def test_a_name_below_the_gate_never_enters(self):
        if getattr(config, "SELECTION_MODE", "blend") != "gate_then_flow":
            self.skipTest("blend mode active")

        row = candidate("LOW", "صنعت الف", config.TECHNICAL_GATE - 5)
        row["flow_score"] = 100
        row["rank_score"] = 100
        row["rank_eligible"] = False
        row["selection"] = {"basis": None}

        result = portfolio.build([row], equity=EQUITY, size=1, min_score=0)

        self.assertEqual(result["positions"], [],
                         "perfect flow must not buy a name that failed the gate")

    def test_missing_flow_falls_back_to_the_blend(self):
        """No client-type data must not exclude a name outright."""

        row = candidate("NOFLOW", "صنعت الف", 90)
        row["flow_score"] = None
        row["rank_score"] = row["combined_score"]
        row["rank_eligible"] = True
        row["selection"] = {"basis": "combined (no flow data)"}

        result = portfolio.build([row], equity=EQUITY, size=1, min_score=50)

        self.assertEqual(len(result["positions"]), 1)

    def test_the_tiebreak_decides_when_flow_saturates(self):
        """Tied ranking scores must be separated by the configured tiebreak.

        Flow saturates at 100 for tens of names, so for a ten-name basket this
        is the field that decides membership. It must be the one config names,
        not whichever component happens to be left in the sort key.
        """

        if getattr(config, "TIEBREAK", None) != "liquidity":
            self.skipTest("liquidity tiebreak not active")

        thin = candidate("THIN", "صنعت الف", 90, adv=30_000_000_000)
        deep = candidate("DEEP", "صنعت ب", 90, adv=90_000_000_000)

        for row, flow in ((thin, 100), (deep, 100)):
            row["flow_score"] = flow
            row["rank_score"] = flow
            row["rank_eligible"] = True
            row["selection"] = {"basis": "flow"}

        # The thinner name carries the better fundamental score, so if the old
        # tiebreak were still in force it would win.
        thin["fundamental_score"] = 99
        deep["fundamental_score"] = 10

        result = portfolio.build([thin, deep], equity=EQUITY, size=1,
                                 max_per_sector=1, min_score=50)

        self.assertEqual(len(result["positions"]), 1)
        self.assertEqual(result["positions"][0]["symbol"], "DEEP",
                         "the more liquid name must win a tied ranking")

    def test_rows_without_a_rank_score_still_order(self):
        """Hand-built and cached rows carry combined_score only."""

        rows = [candidate("A", "صنعت الف", 95),
                candidate("B", "صنعت ب", 80)]

        result = portfolio.build(rows, equity=EQUITY, size=2,
                                 max_per_sector=1, min_score=50)

        self.assertEqual(len(result["positions"]), 2)


class TestTrailingStop(unittest.TestCase):
    """The trail must never be able to tighten a position at entry.

    The effective stop is max(initial stop, peak close - trail distance). On
    the day a position opens the peak IS the entry, so a trail narrower than
    the initial stop would sit above it and stop the trade out on noise before
    it had moved at all -- the failure this class exists to prevent.
    """

    def _position(self, atr_pct=2.0, close=1000.0):
        row = candidate("TRAIL", "صنعت الف", 90, close=close, atr_pct=atr_pct)
        result = portfolio.build([row], equity=EQUITY, size=1, min_score=50)
        self.assertEqual(len(result["positions"]), 1)
        return result["positions"][0]

    def test_trail_is_wider_than_the_initial_stop(self):
        if not getattr(config, "TRAIL_ATR_MULTIPLE", 0):
            self.skipTest("trailing stop disabled")

        self.assertGreater(config.TRAIL_ATR_MULTIPLE, config.STOP_ATR_MULTIPLE,
                           "a trail tighter than the initial stop bites at entry")

    def test_entry_stop_is_the_initial_stop_not_the_trail(self):
        if not getattr(config, "TRAIL_ATR_MULTIPLE", 0):
            self.skipTest("trailing stop disabled")

        p = self._position()

        self.assertAlmostEqual(p["stop_price"], p["initial_stop_price"], places=6)
        self.assertLess(p["trail_price"], p["initial_stop_price"],
                        "at entry the trail must sit below the fixed stop")

    def test_trail_distance_follows_the_configured_multiple(self):
        if not getattr(config, "TRAIL_ATR_MULTIPLE", 0):
            self.skipTest("trailing stop disabled")

        atr_pct = 2.5
        p = self._position(atr_pct=atr_pct)

        self.assertAlmostEqual(
            p["trail_distance_pct"],
            atr_pct * config.TRAIL_ATR_MULTIPLE,
            places=2
        )

    def test_activation_price_is_where_the_trail_overtakes_the_stop(self):
        """Above this price the trail governs; below it the fixed stop does."""

        if not getattr(config, "TRAIL_ATR_MULTIPLE", 0):
            self.skipTest("trailing stop disabled")

        p = self._position()

        peak = p["trail_activates_above"]

        self.assertGreater(peak, p["close"])

        trail_at_peak = peak * (1 - p["trail_distance_pct"] / 100.0)

        # Rebuilt from the reported numbers rather than from internals, so the
        # test fails if the published fields stop agreeing with each other.
        self.assertAlmostEqual(
            trail_at_peak,
            p["initial_stop_price"],
            delta=max(1.0, p["close"] * 0.005)
        )

    def test_trail_never_moves_the_stop_below_the_initial(self):
        for atr_pct in (0.5, 1.0, 2.0, 4.0, 8.0):
            with self.subTest(atr_pct=atr_pct):
                p = self._position(atr_pct=atr_pct)
                self.assertGreaterEqual(p["stop_price"],
                                        p["initial_stop_price"] - 1e-6)

    def test_disabled_trail_reports_nothing(self):
        original = config.TRAIL_ATR_MULTIPLE
        config.TRAIL_ATR_MULTIPLE = 0.0

        try:
            p = self._position()
            self.assertIsNone(p["trail_distance_pct"])
            self.assertIsNone(p["trail_price"])
            self.assertAlmostEqual(p["stop_price"], p["initial_stop_price"],
                                   places=6)
        finally:
            config.TRAIL_ATR_MULTIPLE = original


class TestEligibility(unittest.TestCase):

    def test_rejected_names_never_enter(self):
        rows = [candidate("BAD", "صنعت الف", 95, verdict="REJECTED")]

        result = portfolio.build(rows, equity=EQUITY, min_score=50)

        self.assertEqual(result["positions"], [])

    def test_insufficient_data_never_enters(self):
        rows = [candidate("UNK", "صنعت الف", 95, verdict="INSUFFICIENT_DATA")]

        result = portfolio.build(rows, equity=EQUITY, min_score=50)

        self.assertEqual(result["positions"], [])

    def test_untraded_names_never_enter(self):
        """`scored: False` is a placeholder zero, not a verdict of zero."""

        rows = [candidate("HALT", "صنعت الف", 95, scored=False)]

        result = portfolio.build(rows, equity=EQUITY, min_score=50)

        self.assertEqual(result["positions"], [])

    def test_below_min_score_is_reported_not_silently_empty(self):
        """An empty basket must name the bar that was missed and the best seen.

        Which bar that is depends on the mode, and the note has to follow it:
        under gate_then_flow the blocking number is the technical gate, so a
        message about the combined minimum would send the user to change a
        setting that had no effect on the outcome.
        """

        rows = [candidate("MEH", "صنعت الف", 40)]

        result = portfolio.build(rows, equity=EQUITY, min_score=60)

        self.assertEqual(result["positions"], [])
        self.assertTrue(result["notes"], "an empty basket must explain itself")

        note = " ".join(result["notes"])

        self.assertIn("40", note, "the best available score must be reported")

        if getattr(config, "SELECTION_MODE", "blend") == "gate_then_flow":
            self.assertIn(str(config.TECHNICAL_GATE), note,
                          "the gate that blocked the basket must be named")

    def test_empty_input_does_not_raise(self):
        result = portfolio.build([], equity=EQUITY)

        self.assertEqual(result["positions"], [])
        self.assertEqual(result["totals"]["positions"], 0)
        self.assertTrue(result["notes"])

    def test_none_input_does_not_raise(self):
        result = portfolio.build(None, equity=EQUITY)

        self.assertEqual(result["positions"], [])


class TestTotals(unittest.TestCase):

    def test_totals_agree_with_the_positions(self):
        result = portfolio.build(spread(10), equity=EQUITY)

        invested = sum(p["position_value"] for p in result["positions"])
        risk = sum(p["risk_rials"] for p in result["positions"])

        self.assertAlmostEqual(result["totals"]["invested"], invested, delta=1)
        self.assertAlmostEqual(result["totals"]["total_risk"], risk, delta=1)
        self.assertAlmostEqual(
            result["totals"]["cash"], EQUITY - invested, delta=1
        )

    def test_ranks_have_no_holes(self):
        result = portfolio.build(spread(10), equity=EQUITY)

        ranks = [p["rank"] for p in result["positions"]]

        self.assertEqual(ranks, list(range(1, len(ranks) + 1)))

    def test_shares_are_whole_numbers(self):
        result = portfolio.build(spread(10), equity=EQUITY)

        for p in result["positions"]:
            self.assertIsInstance(p["shares"], int)
            self.assertGreater(p["shares"], 0)


if __name__ == "__main__":
    unittest.main()
