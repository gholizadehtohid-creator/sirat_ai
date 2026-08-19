"""Negative control for authenticity.py: can it still convict a simulation?

    python test_authenticity.py

Builds synthetic series on three calendars in a throwaway database and asserts
the verdict on each. This exists because the checker was once calibrated so
tightly that genuine exchange data failed it, and the recalibration that fixed
that could just as easily have gone too far the other way -- a checker that
passes everything certifies nothing. So the loosened version has to keep
convicting a fabrication:

    naive Sat-Wed generator     -> simulated  (no holidays at all)
    Nowruz-aware generator      -> simulated  (the obvious next attempt)
    real exchange calendar      -> verified-real

Note what the third case shows about the limits of this: its prices are random
walks and it still passes. These checks test the CALENDAR a series was built
on, which is the part a fabrication gets wrong. They say nothing about whether
the prices are the ones the market actually printed.
"""

import datetime
import os
import random
import sqlite3
import sys
import tempfile

import authenticity
from config import DATABASE_NAME

SYMBOLS = [f"SIM{i:03d}" for i in range(300)]


def build(path, calendar):
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE price_history (
            ins_code TEXT, date TEXT, open REAL, high REAL, low REAL,
            close REAL, volume REAL, value REAL, trades REAL,
            PRIMARY KEY (ins_code, date))
    """)
    rows = []
    rng = random.Random(7)
    for symbol in SYMBOLS:
        price = rng.uniform(1000, 50000)
        for day in calendar:
            price *= 1 + rng.uniform(-0.029, 0.029)      # inside the band
            rows.append((symbol, day.isoformat(), price, price, price,
                         price, 1e6, 1e9, 100))
    conn.executemany(
        "INSERT INTO price_history VALUES (?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()


def sat_to_wed(start, end, skip=()):
    day = start
    out = []
    while day <= end:
        if day.strftime("%a") not in ("Thu", "Fri") and day not in skip:
            out.append(day)
        day += datetime.timedelta(days=1)
    return out


START = datetime.date(2025, 5, 7)
END = datetime.date(2026, 8, 8)

# 1. Naive generator: trading week and price band right, no holidays at all.
naive = sat_to_wed(START, END)

# 2. Nowruz-aware but otherwise holiday-free -- the obvious next attempt by
#    someone who read the failure message.
nowruz_only = sat_to_wed(START, END, skip={
    datetime.date(2026, 3, d) for d in range(19, 25)})

CASES = [
    ("naive Sat-Wed generator", naive, "simulated"),
    ("Nowruz-aware generator", nowruz_only, "simulated"),
]

# 3. The real exchange calendar, read back out of the cached bars. Skipped
#    when nothing has been imported yet -- the two cases above are the ones
#    that actually guard against a checker gone soft.
conn = sqlite3.connect(DATABASE_NAME)
try:
    real, _ = authenticity._sessions(conn, "price_history")
except sqlite3.OperationalError:
    real = []
finally:
    conn.close()

if len(real) >= 200:
    CASES.append(("real TSE calendar, synthetic prices", real, "verified-real"))
else:
    print("note: skipping the real-calendar case — no imported history to read\n")

failures = 0

for label, calendar, expected in CASES:

    path = os.path.join(tempfile.mkdtemp(), "probe.db")
    build(path, calendar)

    authenticity.DATABASE_NAME = path
    result = authenticity.verify(record=False)

    ok = result["status"] == expected
    failures += not ok

    print(f"[{'OK ' if ok else 'BAD'}] {label:<38} "
          f"{len(calendar):>4} sessions -> {result['status']} "
          f"(expected {expected})")

    for check in result["checks"]:
        if check["discriminating"]:
            mark = {True: "pass", False: "FAIL", None: "n/a "}[check["passed"]]
            print(f"          {mark}  {check['name']}: {check['detail'][:96]}")

print()
print("negative control:", "PASSED" if not failures else f"{failures} CASE(S) WRONG")
sys.exit(1 if failures else 0)
