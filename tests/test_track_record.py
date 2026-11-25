"""The track-record registry: every card's badge, read from the evidence.

Offline. The live backtest_output files are only read; the tests that need
another verdict replace track_record.results() for one card at a time.
"""

import copy
import unittest
from unittest import mock

import tests  # noqa: F401  -- opens the live stores read-only first

import track_record as tr

REJECTED_CARDS = ("basket", "technical", "technical_top", "technical_inflow",
                  "screen", "index_technical", "smart_money")


def registry():
    return {c["id"]: c for c in tr.cards(use_cache=False)}


class RegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cards = registry()

    def test_every_card_is_present_and_read(self):
        self.assertEqual(list(self.cards), [cid for cid, _b in tr.REGISTRY])
        for cid, card in self.cards.items():
            self.assertIsNone(card["error"], cid)

    def test_the_shape_the_panel_renders(self):
        keys = {"id", "title_fa", "tab", "panel_titles", "badge", "badge_key",
                "badge_reason_fa", "experiments", "experiments_detail",
                "test_period", "n", "hit_rate", "vs_tedpix", "sample", "sample_fa",
                "warning_fa", "hide_levels", "hidden_levels_label_fa", "doubt",
                "track_line_fa", "sources", "extra", "error", "gated_titles",
                "gate_text_fa"}
        for cid, card in self.cards.items():
            self.assertEqual(set(card), keys, cid)
            self.assertIn(card["badge_key"], tr.BADGES, cid)
            self.assertEqual(card["badge"], tr.BADGES[card["badge_key"]]["fa"], cid)
            self.assertTrue(card["track_line_fa"].startswith(card["badge"]), cid)
            self.assertTrue(card["warning_fa"], cid)
            self.assertIn("after_live_cost_fa", card["vs_tedpix"], cid)
            self.assertTrue(card["panel_titles"], cid)

    def test_rejected_strategies_hide_their_levels(self):
        for cid in REJECTED_CARDS:
            self.assertEqual(self.cards[cid]["badge_key"], tr.REJECTED, cid)
            self.assertTrue(self.cards[cid]["hide_levels"], cid)
            self.assertEqual(self.cards[cid]["hidden_levels_label_fa"],
                             "ردشده در آزمون، فقط برای پژوهش")
        for cid, card in self.cards.items():
            if card["badge_key"] != tr.REJECTED:
                self.assertFalse(card["hide_levels"], cid)
                self.assertEqual(card["gated_titles"], [], cid)
            else:
                # what is gated is a part of the card, named, with a text
                self.assertTrue(card["gated_titles"], cid)
                self.assertTrue(set(card["gated_titles"]) <= set(card["panel_titles"]), cid)
                self.assertTrue(card["gate_text_fa"], cid)

    def test_the_technical_reading_hides_its_vote_and_targets_only(self):
        """2026-11-26: the owner read the gate as "the technical analysis
        reports nothing" -- it hid the indicators, the charts and support and
        resistance, which describe the price and were never what was tested."""

        card = self.cards["technical"]
        self.assertEqual(card["gated_titles"], ["سیگنال تکنیکال", "اهداف قیمتی"])
        for shown in ("تحلیل تکنیکال نماد", "قیمت، سطوح و حجم", "مقاومت‌ها و حمایت‌ها"):
            self.assertNotIn(shown, card["gated_titles"])
        self.assertIn("رأی خرید/فروش", card["gate_text_fa"])
        # the lists stay behind the gate whole
        for cid in ("technical_top", "technical_inflow", "screen"):
            self.assertEqual(self.cards[cid]["gated_titles"],
                             self.cards[cid]["panel_titles"], cid)

    def test_descriptive_cards(self):
        for cid in ("fund_discount", "bills", "news", "holdings", "announcements",
                    "market_flow", "moneyflow", "live", "regime", "valuation", "gold"):
            self.assertEqual(self.cards[cid]["badge_key"], tr.DESCRIPTIVE, cid)

    def test_warnings_are_specific(self):
        for cid, card in self.cards.items():
            text = card["warning_fa"]
            self.assertNotIn("عملکرد گذشته", text, cid)
            self.assertNotIn("past performance", text.lower(), cid)
        # A rejected card's warning names the test that rejected it.
        for cid in REJECTED_CARDS:
            self.assertRegex(self.cards[cid]["warning_fa"], r"E\d+|v1", cid)

    def test_untested_costs_say_not_measured(self):
        basket = self.cards["basket"]["vs_tedpix"]
        self.assertEqual(basket["cost_used_pct"], 1.039)
        self.assertTrue(basket["after_live_cost_fa"].startswith(tr.NOT_MEASURED))
        self.assertEqual(self.cards["news"]["vs_tedpix"]["after_live_cost_fa"],
                         tr.NOT_MEASURED)

    def test_the_moneyflow_card_carries_e21_integrity_only(self):
        forward = self.cards["moneyflow"]["extra"]["forward_e21"]
        self.assertIn("locked until 2027-08-19", forward["outcome_scoring"] or "")
        self.assertNotIn("ic", forward)


class FromTheFilesTests(unittest.TestCase):
    """No number is typed by hand: change the file, the card changes."""

    def with_results(self, name, mutate):
        real = tr.results

        def fake(asked):
            data = real(asked)
            if asked == name:
                data = copy.deepcopy(data) if data is not None else None
                data = mutate(data)
            return data

        return mock.patch.object(tr, "results", side_effect=fake)

    def test_the_basket_warning_follows_e26(self):
        def mutate(d):
            last = sorted(d["windows"])[-1]
            d["windows"][last]["total_return_pct"]["baseline"] = 12.34
            d["windows"][last]["benchmarks_pct"]["tedpix"] = 56.78
            return d

        with self.with_results("E26_results.json", mutate):
            card = tr.card_basket()
        self.assertIn("12.3٪", card["warning_fa"])
        self.assertIn("56.8٪", card["warning_fa"])

    def test_a_missing_file_fails_one_card_only(self):
        with self.with_results("E26_results.json", lambda d: None):
            cards = {c["id"]: c for c in tr.cards(use_cache=False)}
        self.assertTrue(cards["basket"]["error"])
        self.assertIsNone(cards["basket"]["badge"])
        self.assertIsNone(cards["allocation"]["error"])

    def with_retest(self, mutate, name="E35_results.json"):
        """E35's file through `mutate`, E36's absent: the fallback path.
        (E36, fifteen years, answers first when it has a result.)"""
        real = tr.results

        def fake(asked):
            if asked == "E36_results.json" and name != asked:
                return None
            data = real(asked)
            if asked == name:
                data = copy.deepcopy(data) if data is not None else None
                data = mutate(data)
            return data

        return mock.patch.object(tr, "results", side_effect=fake)

    def test_e36_answers_first_and_is_named(self):
        verdict = {"H1_gold_vs_fixed_income": "NOT ACCEPTED",
                   "H2_equity_vs_fixed_income": "NOT ACCEPTED"}
        doc = {"verdict": verdict, "months": 180, "fixed_income": {"cagr_pct": 23.9},
               "books": {"equity_100": {"cagr_pct": 40.4}},
               "tests": {"H1_gold_vs_fixed_income": {"p": 0.093},
                         "H2_equity_vs_fixed_income": {"p": 0.086}}}
        with self.with_retest(lambda d: doc, name="E36_results.json"):
            card = tr.card_allocation()
        self.assertEqual(card["badge_key"], tr.PROVISIONAL)
        self.assertIn("E36", card["warning_fa"])
        self.assertNotIn("E35", card["warning_fa"])
        self.assertIn("0.093", card["warning_fa"])

    def e34(self, verdict):
        return {"verdict": verdict, "months": 106,
                "books": {"equity_100": {"cagr_pct": 50.0}}, "bills": {"cagr_pct": 35.0}}

    def test_allocation_waits_for_e34(self):
        with self.with_retest(lambda d: None):
            card = tr.card_allocation()
        self.assertEqual(card["badge_key"], tr.PROVISIONAL)
        self.assertEqual(card["extra"]["retest"]["state_fa"], tr.IN_TEST)

    def test_allocation_accepted_when_e34_accepts_both(self):
        verdict = {"H1_gold_vs_real_bills": "ACCEPT", "H2_equity_vs_real_bills": "ACCEPT",
                   "H3_three_part_vs_gold": "NOT ACCEPTED"}
        with self.with_retest(lambda d: self.e34(verdict)):
            card = tr.card_allocation()
        self.assertEqual(card["badge_key"], tr.ACCEPTED)
        self.assertIn("50.0٪", card["vs_tedpix"]["after_live_cost_fa"])

    def test_allocation_provisional_when_e34_does_not_accept_one(self):
        verdict = {"H1_gold_vs_real_bills": "NOT ACCEPTED",
                   "H2_equity_vs_real_bills": "ACCEPT"}
        with self.with_retest(lambda d: self.e34(verdict)):
            card = tr.card_allocation()
            gold = tr.card_gold()
        self.assertEqual(card["badge_key"], tr.PROVISIONAL)
        self.assertIn("طلا", card["badge_reason_fa"])
        self.assertIn("نگذشت", gold["warning_fa"])

    def test_a_stage_one_failure_of_e34_says_nothing(self):
        with self.with_retest(lambda d: {"verdict": {"stage_1": "FAIL"}}):
            card = tr.card_allocation()
        self.assertEqual(card["badge_key"], tr.PROVISIONAL)
        self.assertEqual(card["extra"]["retest"]["state"], "no_result")

    def test_the_outlook_badge_follows_its_forward_test(self):
        import forward_scorecard

        for state, badge in (("collecting", tr.PROVISIONAL), ("active", tr.ACCEPTED),
                             ("retired", tr.REJECTED)):
            status = {"state": state, "report_only": state == "retired",
                      "resolved_in_window": 31, "min_n": 30,
                      "retire_reasons": ["skill -0.1 <= 0.0 vs the naive"]}
            with mock.patch.object(forward_scorecard, "status", return_value=status):
                card = tr.card_outlook()
            self.assertEqual(card["badge_key"], badge, state)
            self.assertEqual(card["extra"]["range_report_only"], state == "retired")
            # The outlook has no entry/exit levels to hide; its range is
            # demoted through range_report_only instead.
            self.assertFalse(card["hide_levels"])


if __name__ == "__main__":
    unittest.main()
