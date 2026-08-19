"""Does the market-regime call predict what happens next?

regime.py decides how much of the account is allowed to be invested, and the
combined backtest showed that decision matters more than stock picking: the
same rule returned +18.5% a trade in the 2020 bull and -5.4% in the 2023 bear.
A cash target driven by a regime read is therefore the largest single lever in
the system -- and it had never been checked against an outcome.

WHAT IS BEING TESTED
--------------------
At each sample date the regime is recomputed from data available on that date
only (regime.detect(as_of=...)), then compared with what the market actually
did over the following N sessions.

The bar it has to clear is not "is it right more often than not". A call that
says BULL in a market that rises 70% of the time is right 70% of the time
while carrying no information at all. What matters is the SEPARATION: the
forward return after a BULL call minus the forward return after a BEAR call.
If that gap is not clearly positive, the regime read is decoration.

WHAT THIS CANNOT SHOW
---------------------
The universe is the instruments listed today, so breadth in 2020 is measured
across the survivors of 2020. Breadth is the third voter, and survivors were
by definition the stronger half -- so the participation reading is biased
upward in every historical window, most in the worst ones.
"""

import argparse
import sys

import pandas as pd

import config
import database
import regime


# Sampling every session would be 3,000 regime computations, each of which
# reads 818 instrument histories. Fortnightly is dense enough to catch a turn
# and cheap enough to run.
DEFAULT_STEP_DAYS = 14

FORWARD_HORIZONS = (20, 60)


def _index_frame(key="TEDPIX"):
    code = config.INDICES[key]["insCode"]

    frame = database.load_index_history(code)

    if frame is None or frame.empty:
        return None

    frame = frame.dropna(subset=["close"]).reset_index(drop=True)
    frame["date"] = pd.to_datetime(frame["date"])

    return frame


def forward_return(frame, as_of, sessions):
    """Index return over the `sessions` bars after `as_of`, or None."""

    later = frame[frame["date"] <= pd.Timestamp(as_of)]

    if later.empty:
        return None

    position = later.index[-1]

    if position + sessions >= len(frame):
        return None

    start = float(frame["close"].iloc[position])
    end = float(frame["close"].iloc[position + sessions])

    if start <= 0:
        return None

    return (end / start - 1.0) * 100


def sample_dates(frame, start, end, step_days=DEFAULT_STEP_DAYS):
    dates = frame["date"]

    window = dates[(dates >= pd.Timestamp(start)) & (dates <= pd.Timestamp(end))]

    if window.empty:
        return []

    picked = []
    last = None

    for date in window:
        if last is None or (date - last).days >= step_days:
            picked.append(date)
            last = date

    return picked


def run(start="2015-01-01", end=None, step_days=DEFAULT_STEP_DAYS,
        verbose=True):
    """Regime call vs realised forward return, one row per sample date."""

    frame = _index_frame("TEDPIX")

    if frame is None:
        print("no index history — nothing to test")
        return []

    if end is None:
        end = frame["date"].iloc[-1]

    dates = sample_dates(frame, start, end, step_days)

    if verbose:
        print(f"sampling {len(dates)} dates from {start} to "
              f"{pd.Timestamp(end).date()}, every ~{step_days} days")

    rows = []

    for n, date in enumerate(dates, start=1):

        try:
            read = regime.detect(as_of=date)
        except Exception as exc:
            if verbose:
                print(f"  {date.date()}: detect failed ({exc})")
            continue

        if not read.get("available"):
            continue

        row = {
            "date": date,
            "regime": read.get("regime"),
            "label": read.get("label"),
            "confidence": read.get("confidence"),
            "cash_target_pct": read.get("cash_target_pct"),
            "votes": read.get("votes"),
        }

        for horizon in FORWARD_HORIZONS:
            row[f"fwd_{horizon}"] = forward_return(frame, date, horizon)

        rows.append(row)

        if verbose and n % 20 == 0:
            print(f"  {n}/{len(dates)} sampled")

    if verbose:
        print(f"done: {len(rows)} usable regime reads")

    return rows


def _stats(values):
    values = [v for v in values if v is not None]

    if not values:
        return None

    values_sorted = sorted(values)
    mid = len(values_sorted) // 2

    return {
        "n": len(values),
        "mean": sum(values) / len(values),
        "median": (values_sorted[mid] if len(values_sorted) % 2
                   else (values_sorted[mid - 1] + values_sorted[mid]) / 2),
        "positive_pct": sum(1 for v in values if v > 0) / len(values) * 100,
        "worst": min(values),
        "best": max(values),
    }


def report(rows):
    if not rows:
        print("no regime reads to report")
        return

    print()
    print("=" * 96)
    print("REGIME CALL vs REALISED FORWARD RETURN OF شاخص کل")
    print("=" * 96)

    for horizon in FORWARD_HORIZONS:

        key = f"fwd_{horizon}"

        print()
        print(f"--- next {horizon} sessions " + "-" * 60)
        print(f"{'regime':<12} {'n':>5} {'mean%':>9} {'median%':>9} "
              f"{'up%':>7} {'worst%':>9} {'best%':>9}")
        print("-" * 96)

        by_regime = {}

        for row in rows:
            by_regime.setdefault(row["regime"], []).append(row.get(key))

        order = [regime.BULL, regime.SIDEWAYS, regime.BEAR]

        stats = {}

        for name in order:
            values = by_regime.get(name)
            if not values:
                continue
            s = _stats(values)
            stats[name] = s
            if not s:
                continue
            print(f"{name:<12} {s['n']:>5} {s['mean']:>9.2f} {s['median']:>9.2f} "
                  f"{s['positive_pct']:>7.1f} {s['worst']:>9.1f} {s['best']:>9.1f}")

        # ---- the number that decides whether this module earns its place ----
        bull = stats.get(regime.BULL)
        bear = stats.get(regime.BEAR)

        print()

        if bull and bear:
            gap = bull["mean"] - bear["mean"]
            print(f"  SEPARATION  bull {bull['mean']:+.2f}%  vs  "
                  f"bear {bear['mean']:+.2f}%   ->  gap {gap:+.2f} points")

            baseline = _stats([r.get(key) for r in rows])
            print(f"  baseline (every date, no regime read): "
                  f"{baseline['mean']:+.2f}%")

            if gap <= 0:
                print("  the call is INVERTED or worthless at this horizon")
            elif gap < abs(baseline["mean"]) * 0.25:
                print("  the gap is small next to the market's own drift — weak")
        else:
            print("  not enough of both calls to compare")

    # ---- how often is each call made ------------------------------------
    print()
    print("=" * 96)
    print("CALL FREQUENCY")
    print("=" * 96)

    counts = {}
    for row in rows:
        counts[row["regime"]] = counts.get(row["regime"], 0) + 1

    for name, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"   {name:<12} {count:>5}  ({count / len(rows) * 100:.1f}%)")

    print()
    print("A call made 90% of the time cannot be steering anything.")

    # ---- did the cash target track the danger? --------------------------
    print()
    print("=" * 96)
    print("CASH TARGET vs WHAT FOLLOWED  (next 20 sessions)")
    print("=" * 96)
    print(f"{'cash target':<14} {'n':>5} {'mean fwd%':>11} {'worst%':>9}")
    print("-" * 96)

    by_cash = {}
    for row in rows:
        cash = row.get("cash_target_pct")
        if cash is None:
            continue
        by_cash.setdefault(round(cash / 10) * 10, []).append(row.get("fwd_20"))

    for cash in sorted(by_cash):
        s = _stats(by_cash[cash])
        if not s:
            continue
        print(f"{cash:>10}%    {s['n']:>5} {s['mean']:>11.2f} {s['worst']:>9.1f}")

    print()
    print("More cash should line up with worse forward returns. If the highest")
    print("cash targets sit on the best outcomes, the module is backwards.")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Validate regime.py against realised forward returns."
    )
    parser.add_argument("--start", default="2015-01-01")
    parser.add_argument("--end", default=None)
    parser.add_argument("--step", type=int, default=DEFAULT_STEP_DAYS,
                        help="calendar days between samples (default 14)")

    args = parser.parse_args(argv)

    rows = run(start=args.start, end=args.end, step_days=args.step)

    report(rows)

    return 0


if __name__ == "__main__":
    sys.exit(main())
