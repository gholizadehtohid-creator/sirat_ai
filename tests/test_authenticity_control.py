"""authenticity.py in CI: it convicts a fabrication, clears a real calendar,
and its verdict alone cannot delete the price tables.

authenticity.verify's SIMULATED verdict is what purge_simulated.py acts on,
and it was once wrong about bars fetched live from TSETMC. Its only negative
control was a root-level script CI never ran (the full test, 2026-11-12).
"""

import datetime
import os
import random
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import authenticity  # noqa: E402
import config  # noqa: E402
import purge_simulated  # noqa: E402


def build(calendar, step=0.029, old_steps=None, symbols=120):
    """A throwaway price_history on `calendar`: random walks inside `step`;
    `old_steps` widens the moves on the first half (an older, wider band)."""

    path = os.path.join(tempfile.mkdtemp(), "probe.db")
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE price_history (
            ins_code TEXT, date TEXT, open REAL, high REAL, low REAL,
            close REAL, volume REAL, value REAL, trades REAL,
            PRIMARY KEY (ins_code, date))""")
    rng = random.Random(7)
    rows = []
    half = len(calendar) // 2
    for i in range(symbols):
        price = rng.uniform(1000, 50000)
        for n, day in enumerate(calendar):
            width = old_steps if old_steps and n < half else step
            price *= 1 + rng.uniform(-width, width)
            rows.append(("S%03d" % i, day.isoformat(), price, price, price,
                         price, 1e6, 1e9, 100))
    conn.executemany("INSERT INTO price_history VALUES (?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    return path


def sat_to_wed(start, end, skip=()):
    out, day = [], start
    while day <= end:
        if day.strftime("%a") not in ("Thu", "Fri") and day not in skip:
            out.append(day)
        day += datetime.timedelta(days=1)
    return out


START, END = datetime.date(2025, 5, 7), datetime.date(2026, 8, 8)


def real_calendar():
    if not os.path.exists(config.DATABASE_NAME):
        return []
    conn = sqlite3.connect("file:%s?mode=ro" % config.DATABASE_NAME.replace("\\", "/"),
                           uri=True)
    try:
        days, _ = authenticity._sessions(conn, "price_history")
    except sqlite3.Error:
        days = []
    finally:
        conn.close()
    return [d if isinstance(d, datetime.date) else datetime.date.fromisoformat(str(d)[:10])
            for d in days][-400:]


def verdict(path):
    with mock.patch.object(authenticity, "DATABASE_NAME", path):
        return authenticity.verify(table="price_history", record=False)


class ControlTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.real = real_calendar()

    def test_a_naive_generator_is_convicted(self):
        self.assertEqual(verdict(build(sat_to_wed(START, END)))["status"],
                         authenticity.SIMULATED)

    def test_a_nowruz_aware_generator_is_convicted(self):
        skip = {datetime.date(2026, 3, d) for d in range(19, 25)}
        self.assertEqual(verdict(build(sat_to_wed(START, END, skip)))["status"],
                         authenticity.SIMULATED)

    def test_the_real_calendar_is_cleared(self):
        if len(self.real) < 200:
            self.skipTest("no imported history to read the calendar from")
        self.assertEqual(verdict(build(self.real))["status"],
                         authenticity.VERIFIED_REAL)

    def test_wide_old_moves_on_the_real_calendar_are_not_a_conviction(self):
        """Moves under an older, wider band (here ±6% on the first half)."""
        if len(self.real) < 200:
            self.skipTest("no imported history to read the calendar from")
        result = verdict(build(self.real, old_steps=0.06))
        self.assertNotEqual(result["status"], authenticity.SIMULATED)

    def test_a_structural_failure_alone_is_unverified(self):
        if len(self.real) < 200:
            self.skipTest("no imported history to read the calendar from")
        result = verdict(build(self.real, step=0.08))     # every series too wild
        self.assertEqual(result["status"], authenticity.UNVERIFIED)


class PurgeTests(unittest.TestCase):

    def run_purge(self, status, discriminating_failed, **kw):
        checks = [{"name": "Nowruz", "discriminating": True,
                   "passed": not discriminating_failed}]
        path = build(sat_to_wed(START, START + datetime.timedelta(days=20)), symbols=2)
        with mock.patch.object(purge_simulated, "create_database"), \
                mock.patch.object(purge_simulated, "set_provenance"), \
                mock.patch.object(purge_simulated, "DATABASE_NAME", path), \
                mock.patch.object(authenticity, "verify", return_value={
                    "status": status, "summary": "t", "checks": checks}), \
                mock.patch("builtins.print"):
            purge_simulated.purge(**kw)
        conn = sqlite3.connect(path)
        try:
            return conn.execute("SELECT COUNT(*) FROM price_history").fetchone()[0]
        finally:
            conn.close()

    def test_an_unverified_verdict_never_purges(self):
        self.assertGreater(self.run_purge(authenticity.UNVERIFIED, False,
                                          confirmed=True), 0)

    def test_a_conviction_without_yes_only_reports(self):
        self.assertGreater(self.run_purge(authenticity.SIMULATED, True), 0)

    def test_a_conviction_with_yes_purges(self):
        self.assertEqual(self.run_purge(authenticity.SIMULATED, True,
                                        confirmed=True), 0)


if __name__ == "__main__":
    unittest.main()
