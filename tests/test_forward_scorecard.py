"""The forward scorecard: E21 integrity without outcomes, the outlook by its
protocol.

Offline. Synthetic logs and databases live in a temporary directory; the
live E21 log is only READ (to hold the restated chain check equal to the
original), and no outcome of it is ever computed.
"""

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone

import tests  # noqa: F401  -- opens the live stores read-only first

import config
import forward_scorecard as fs
import outlook_forward_log as ofl

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEDPIX = str(config.INDICES["TEDPIX"]["insCode"])


def make_market_db(path, sessions=(), traded=None, filings=()):
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE index_history (ins_code TEXT, date TEXT, close REAL);
        CREATE TABLE price_history (ins_code TEXT, date TEXT, volume REAL);
        CREATE TABLE instruments (ins_code TEXT, is_equity INTEGER);
        CREATE TABLE codal_filings (tracing_no TEXT PRIMARY KEY, ins_code TEXT,
            period_end TEXT, period_months INTEGER, published_at TEXT,
            title TEXT, audited INTEGER, consolidated INTEGER, items TEXT,
            ratios TEXT, updated_at TEXT);
    """)
    for s in sessions:
        conn.execute("INSERT INTO index_history VALUES (?, ?, 1)", (TEDPIX, s))
    for s, n in (traded or {}).items():
        for i in range(n):
            conn.execute("INSERT INTO price_history VALUES (?, ?, 10)", ("c%d" % i, s))
    conn.executemany("INSERT INTO instruments VALUES (?, 1)",
                     [("c%d" % i,) for i in range(max((traded or {0: 0}).values() or [0]))])
    for n, (code, end, cons, published, profit) in enumerate(filings):
        conn.execute("INSERT INTO codal_filings (tracing_no, ins_code, period_end, "
                     "period_months, published_at, consolidated, items) VALUES "
                     "(?, ?, ?, 12, ?, ?, ?)",
                     ("t%d" % n, code, end, published, int(cons),
                      json.dumps({"net_profit": profit, "net_profit_owners": None})))
    conn.commit()
    conn.close()


class Temp(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="fsc-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.db = os.path.join(self.dir, "market.db")


# ==============================
# E21: the window is never spent
# ==============================

class ReservationGuardTests(unittest.TestCase):
    def test_outcome_scoring_refuses_before_maturity(self):
        with self.assertRaises(fs.ReservationNotMatured):
            fs.score_e21_outcomes()
        with self.assertRaises(fs.ReservationNotMatured):
            fs.score_e21_outcomes(today=date(2027, 8, 18))

    def test_after_maturity_it_still_belongs_to_a_registered_successor(self):
        with self.assertRaises(NotImplementedError):
            fs.score_e21_outcomes(today=date(2027, 8, 19))

    def test_the_guard_is_an_error_not_a_quiet_empty_answer(self):
        self.assertTrue(issubclass(fs.ReservationNotMatured, RuntimeError))


class E21ChainTests(unittest.TestCase):
    def test_the_restated_check_agrees_with_the_original(self):
        sys.path.insert(0, os.path.join(ROOT, "examples"))
        try:
            import forward_prediction_log as fpl
        finally:
            sys.path.remove(os.path.join(ROOT, "examples"))
        rows = fpl.read_log()
        if not rows:
            self.skipTest("no E21 log")
        mine, theirs = fs.verify_e21_chain(rows), fpl.verify_chain(rows)
        self.assertEqual(mine["intact"], theirs["intact"])
        if theirs["intact"]:
            self.assertEqual(mine["head"], theirs["head"])
        tampered = [dict(r) for r in rows]
        tampered[0]["n_names"] = (tampered[0].get("n_names") or 0) + 1
        self.assertEqual(fs.verify_e21_chain(tampered)["broken_at"],
                         fpl.verify_chain(tampered)["broken_at"])


class E21IntegrityTests(Temp):
    def write_log(self, rows):
        path = os.path.join(self.dir, "fpl.jsonl")
        prev = fs.E21_GENESIS
        with open(path, "w", encoding="utf-8") as handle:
            for row in rows:
                row = dict(row, spec=fs.E21_SPEC)
                import hashlib
                row["entry_hash"] = hashlib.sha256(
                    (prev + fs._canonical(row)).encode("utf-8")).hexdigest()
                prev = row["entry_hash"]
                handle.write(fs._canonical(row) + "\n")
        return path

    def test_counts_without_one_outcome(self):
        # Sat 2026-11-21 .. Wed 2026-11-25 (Thu/Fri closed).
        sessions = ["2026-11-21", "2026-11-22", "2026-11-23", "2026-11-24", "2026-11-25"]
        make_market_db(self.db, sessions, traded={"2026-11-21": 100, "2026-11-22": 100,
                                                  "2026-11-24": 100})
        log = self.write_log([
            # timely: before 11-22 08:45 Tehran (05:15 UTC)
            {"session": "2026-11-21", "computed_at": "2026-11-21T15:00:00+00:00",
             "n_names": 90},
            # late: after 11-23's pre-open
            {"session": "2026-11-22", "computed_at": "2026-11-23T06:00:00+00:00",
             "n_names": 95},
            {"session": "2026-11-23", "missed": "no run in time",
             "computed_at": "2026-11-24T06:00:00+00:00"},
            # timely but thin: 50 of 100 traded
            {"session": "2026-11-24", "computed_at": "2026-11-24T15:00:00+00:00",
             "n_names": 50},
        ])
        now = datetime(2026, 11, 25, 20, 0, tzinfo=timezone.utc)
        out = fs.e21_integrity(log_path=log, db_path=self.db, now=now)
        self.assertTrue(out["chain"]["intact"])
        # 11-25 is the newest session and its next pre-open (Sat 11-28) has
        # not come: still loggable, not yet due.
        self.assertEqual(out["sessions_expected"], 4)
        self.assertEqual((out["timely"], out["late"], out["missed_recorded"],
                          out["absent"], out["thin"], out["clean"]), (2, 1, 1, 0, 1, 1))
        self.assertEqual(out["late_sessions"], ["2026-11-22"])
        self.assertEqual(out["thin_sessions"], ["2026-11-24"])
        self.assertFalse(out["reservation"]["matured"])
        self.assertIn("locked until 2027-08-19", out["outcome_scoring"])

        later = datetime(2026, 11, 28, 6, 0, tzinfo=timezone.utc)   # Sat, past 08:45
        out = fs.e21_integrity(log_path=log, db_path=self.db, now=later)
        self.assertEqual(out["sessions_expected"], 5)
        self.assertEqual(out["absent_sessions"], ["2026-11-25"])

    def test_a_tampered_log_is_reported_broken(self):
        make_market_db(self.db, ["2026-11-21"])
        log = self.write_log([{"session": "2026-11-21", "n_names": 9,
                               "computed_at": "2026-11-21T15:00:00+00:00"}])
        with open(log, encoding="utf-8") as handle:
            row = json.loads(handle.readline())
        row["n_names"] = 10
        with open(log, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(row) + "\n")
        out = fs.e21_integrity(log_path=log, db_path=self.db)
        self.assertFalse(out["chain"]["intact"])


# ==============================
# The outlook, by its protocol
# ==============================

def name_record(code, fy="1405/12", central=1000, cons=False, basis="separate",
                last=800, ttm=900, months_known=7):
    ratios = {"q10": 0.5, "q25": 0.8, "q50": 1.0, "q75": 1.25, "q90": 1.5}
    return {"ins_code": code, "symbol": "س" + code, "fy": fy, "method": "monthly_sales",
            "months_known": months_known, "series_basis": basis, "consolidated": cons,
            "latest_period": {"end": "1405/06/31", "months": 6},
            "profit": {"central": central, "low": int(central * 0.8),
                       "high": int(central * 1.25), "low80": int(central * 0.5),
                       "high80": int(central * 1.5)},
            "eps": {"central": None, "low": None, "high": None},
            "ratios": ratios,
            "band": {"key": "M6", "n": 400, "basis": "monthly",
                     "nominal_inner": 0.5, "nominal_outer": 0.8},
            "naive": {"last_annual_profit": last, "period_end": "1404/12/29",
                      "consolidated": cons, "basis_substituted": False,
                      "ttm_profit": ttm},
            "price": {"price": 1.0, "shares": 10, "pe_forward": 1.0}}


class OutlookScoringTests(Temp):
    def setUp(self):
        super().setUp()
        self.log = os.path.join(self.dir, "ofl.jsonl")
        self.protocol = os.path.join(self.dir, "protocol.json")
        shutil.copyfile(ofl.PROTOCOL_PATH, self.protocol)
        self.digest = ofl.protocol_sha256(self.protocol)

    def entry(self, day, stamp, names):
        ofl.append_entry({"spec": ofl.SPEC, "date": day, "logged_at": day + "T10:00:00+00:00",
                          "logged_at_jalali": stamp, "protocol_sha256": self.digest,
                          "names": names}, self.log)

    def score(self, today):
        return fs.outlook_scorecard(today=today, log_path=self.log,
                                    protocol_path=self.protocol, db_path=self.db)

    def test_states_of_resolution(self):
        self.entry("2026-11-24", "1405/09/03 13:00:00", [
            name_record("1"),                                   # resolves, inside
            name_record("2"),                                   # public before log
            name_record("3"),                                   # pending
            name_record("4", fy="1404/12"),                     # expired
            name_record("5", cons=False, basis="mixed"),        # substituted
            name_record("6", cons=True),                        # other basis only
        ])
        self.entry("2026-11-25", "1405/09/04 13:00:00", [name_record("1", central=5000)])
        make_market_db(self.db, filings=[
            ("1", "1405/12/29", False, "1406/02/10 10:00:00", 1100.0),
            ("1", "1405/12/29", False, "1406/03/01 10:00:00", 9999.0),   # later version
            ("2", "1405/12/29", False, "1405/09/01 10:00:00", 1000.0),
            ("5", "1405/12/29", True, "1406/02/10 10:00:00", 1200.0),
            ("6", "1405/12/29", False, "1406/02/10 10:00:00", 1000.0),
        ])
        # 1406/03/11: three months after the 1405/12 year end.
        card = self.score(date(2027, 6, 1))
        self.assertEqual(card["resolved"], 1)
        unit = card["units"][0]
        self.assertEqual((unit["ins_code"], unit["actual"], unit["central"]),
                         ("1", 1100.0, 1000.0))       # first filing, first record
        self.assertTrue(unit["inner_hit"])
        self.assertEqual(card["states"]["outcome_public_at_log"], 1)
        self.assertEqual(card["states"]["pending"], 3)  # 3, 5 (before deadline), 6
        self.assertEqual(card["states"]["expired"], 1)
        self.assertEqual(card["state"], "collecting")
        self.assertIn("30", card["label_fa"])

        # Past the deadline the mixed-basis name resolves on the other basis;
        # the consolidated-only name (not mixed) never does.
        later = self.score(date(2027, 12, 1))
        codes = {u["ins_code"]: u for u in later["units"]}
        self.assertTrue(codes["5"]["basis_substituted"])
        self.assertNotIn("6", codes)

    def test_pinball_skill_and_coverage_by_hand(self):
        rec = name_record("1", central=1000, last=500, ttm=600)
        filing = {"profit": 1100.0, "published_at": "1406/02/10 10:00:00",
                  "tracing_no": "t"}
        params = ofl.load_protocol(self.protocol)["parameters"]
        unit = fs._score_unit(dict(rec, _date="2026-11-24"), filing, params)
        taus = [0.1, 0.25, 0.5, 0.75, 0.9]
        q = [500, 800, 1000, 1250, 1500]
        expected = sum((1100 - x) * (t - (1 if 1100 < x else 0))
                       for t, x in zip(taus, q)) / 5 / 600
        self.assertAlmostEqual(unit["pinball"], expected)
        naive = [250, 400, 500, 625, 750]
        expected_naive = sum((1100 - x) * t for t, x in zip(taus, naive)) / 5 / 600
        self.assertAlmostEqual(unit["pinball_naive"], expected_naive)
        self.assertTrue(unit["direction_hit"])
        self.assertAlmostEqual(unit["abs_error"], 0.1)

    def build(self, n, actual_ratio, last):
        names = [name_record(str(i), central=1000, last=last, ttm=last)
                 for i in range(n)]
        self.entry("2026-11-24", "1405/09/03 13:00:00", names)
        make_market_db(self.db, filings=[
            (str(i), "1405/12/29", False, "1406/02/%02d 10:00:00" % (1 + i % 28),
             1000.0 * actual_ratio(i)) for i in range(n)])

    def test_collecting_below_the_minimum(self):
        self.build(29, lambda i: 1.0, last=500)
        card = self.score(date(2027, 6, 1))
        self.assertEqual((card["resolved_in_window"], card["state"]), (29, "collecting"))

    def test_active_when_calibrated_and_better_than_last_year(self):
        # Half inside the inner band, outer hits as nominal, all near the estimate.
        ratios = [0.9, 1.1, 1.3, 0.7] * 10
        self.build(40, lambda i: ratios[i], last=400)
        card = self.score(date(2027, 6, 1))
        self.assertEqual(card["state"], "active")
        self.assertGreater(card["metrics_window"]["skill"]["value"], 0)
        self.assertEqual(card["metrics_window"]["coverage_inner"]["rate"], 0.5)

    def test_retired_when_the_band_misses_and_it_is_sticky(self):
        # Every outcome far above the band: coverage 0, and last year's
        # profit (equal to the outcome) beats the estimate.
        self.build(30, lambda i: 3.0, last=3000)
        card = self.score(date(2027, 6, 1))
        self.assertEqual(card["state"], "retired")
        self.assertTrue(card["retire_reasons"])
        self.assertIsNotNone(card["retired_since"])
        # Out of the rolling window a year later: still retired.
        self.assertEqual(self.score(date(2028, 9, 1))["state"], "retired")

    def test_entries_under_another_protocol_are_not_scored(self):
        ofl.append_entry({"spec": ofl.SPEC, "date": "2026-11-24",
                          "logged_at_jalali": "1405/09/03 13:00:00",
                          "protocol_sha256": "someone-else", "names": [name_record("1")]},
                         self.log)
        make_market_db(self.db, filings=[("1", "1405/12/29", False,
                                          "1406/02/10 10:00:00", 1000.0)])
        card = self.score(date(2027, 6, 1))
        self.assertEqual((card["resolved"], card["log"]["entries_under_other_protocol"]),
                         (0, 1))


class StatusTests(Temp):
    def test_status_on_an_empty_log_is_collecting(self):
        make_market_db(self.db)
        s = fs.status(log_path=os.path.join(self.dir, "none.jsonl"), db_path=self.db,
                      use_cache=False)
        self.assertEqual(s["state"], "collecting")
        self.assertEqual((s["resolved"], s["min_n"]), (0, 30))
        self.assertIsNone(s["error"])

    def test_status_never_raises(self):
        s = fs.status(log_path=os.path.join(self.dir, "none.jsonl"),
                      protocol_path=os.path.join(self.dir, "missing.json"),
                      db_path=self.db, use_cache=False)
        self.assertEqual(s["state"], "unknown")
        self.assertTrue(s["error"])

    def test_the_live_status_reads(self):
        s = fs.status(use_cache=False)
        self.assertIn(s["state"], ("collecting", "active", "retired"))


class IntervalTests(unittest.TestCase):
    def test_clopper_pearson_known_values(self):
        self.assertEqual(fs.clopper_pearson(15, 30), [0.313, 0.687])
        self.assertEqual(fs.clopper_pearson(0, 10), [0.0, 0.3085])
        self.assertEqual(fs.clopper_pearson(10, 10), [0.6915, 1.0])
        self.assertIsNone(fs.clopper_pearson(0, 0))


if __name__ == "__main__":
    unittest.main()
