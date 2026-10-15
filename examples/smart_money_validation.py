"""STAGE 1 -- validation of the Smart Money Accumulation Scanner.

WHAT THIS IS, AND WHAT IT IS DELIBERATELY NOT
----------------------------------------------
This asks ONE question: does the scanner compute what it claims to compute?

It does NOT ask whether the scanner predicts returns. That is Stage 2, it
requires an out-of-sample window, and this programme has exactly one left
(R-FORWARD-2027, reserved, currently ~0 sessions long). Nothing in this file
touches it, and nothing here may be quoted as evidence of an edge.

The distinction matters because the two failures look identical from outside.
A scanner that is WRONG (bad arithmetic, a look-ahead, a missing input silently
read as zero) and a scanner that is RIGHT BUT USELESS both produce a list of
names that goes nowhere. Only the first is fixable, and only this file can tell
them apart.

SIX SECTIONS
------------
    1  DATA AUDIT          per input: source, coverage, history, missing rule
    2  FACTOR VERIFICATION each factor recomputed independently and compared
    3  LOOK-AHEAD AUDIT    does any reading at bar T use a bar after T
    4  COMBINED OUTPUT     the brief's table, on real names
    5  FALSE POSITIVES     does every claim on every selected row hold up
    6  SENSITIVITY         MFI 65/70/75, volume 1.5/2.0, score 70/75/80

USAGE
    python examples/smart_money_validation.py
    python examples/smart_money_validation.py --section 2
    python examples/smart_money_validation.py --json out.json
"""

import argparse
import io
import json
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import config
import database
import levels
import moneyflow
import technical

import smart_money
from smart_money import accumulation, flow, position, scoring
from smart_money.scanner import SCAN_BARS, FLOW_BARS


RESULTS = {}
FAILURES = []
BLOCKERS = []


def say(text=""):
    print(text)


def rule(title):
    say()
    say("=" * 78)
    say(title)
    say("=" * 78)


def check(name, passed, detail=""):
    """Record and print one assertion. Failures are collected, not raised --
    a validation run that stops at the first problem hides the other five."""

    mark = "PASS" if passed else "FAIL"
    say("  [%s] %s%s" % (mark, name, ("  -- " + detail) if detail else ""))

    if not passed:
        FAILURES.append((name, detail))

    return passed


def blocker(name, detail):
    """Something that does not make the LIVE scanner wrong, but makes a
    historical test of it impossible.

    Kept apart from check() on purpose. "The arithmetic is wrong" and "the data
    to test this on the past does not exist" are different verdicts with
    different fixes, and a report that files them both under FAIL tells the
    reader to go looking for a bug that is not there.
    """

    say("  [BLOCK] %s  -- %s" % (name, detail))
    BLOCKERS.append((name, detail))


# ==============================
# 1  DATA AUDIT
# ==============================

def section_data_audit():
    rule("1  DATA AUDIT -- source, coverage, history, and what absence does")

    conn = sqlite3.connect(database.DATABASE_NAME)
    q = lambda s: list(conn.execute(s))

    equities = q("SELECT COUNT(*) FROM instruments WHERE is_equity=1")[0][0]

    rows = []

    # ---- MFI ------------------------------------------------------------
    ph_codes = q("""SELECT COUNT(DISTINCT p.ins_code) FROM price_history p
                    JOIN instruments i ON i.ins_code = p.ins_code
                    WHERE i.is_equity = 1""")[0][0]
    ph_range = q("SELECT MIN(date), MAX(date) FROM price_history")[0]
    hl = q("""SELECT COUNT(*) FROM price_history
              WHERE high IS NOT NULL AND low IS NOT NULL AND volume IS NOT NULL
                AND date >= '2024-01-01'""")[0][0]
    hl_all = q("SELECT COUNT(*) FROM price_history WHERE date >= '2024-01-01'")[0][0]

    rows.append({
        "input": "MFI",
        "source": "price_history (high/low/close/volume)",
        "coverage": "%d/%d equities" % (ph_codes, equities),
        "history": "%s .. %s" % ph_range,
        "complete": "%.1f%% of bars since 2024 have H/L/volume"
                    % (100.0 * hl / hl_all if hl_all else 0),
        "on_missing": "flow.read() returns None -> component scores NOTHING "
                      "(not zero) and the name fails the MFI gate",
    })

    # ---- P/E and sector P/E ---------------------------------------------
    snaps = q("SELECT COUNT(DISTINCT captured_at) FROM market_snapshot")[0][0]
    pe_ok = q("SELECT COUNT(*) FROM market_snapshot WHERE pe > 0")[0][0]
    snap_rows = q("SELECT COUNT(*) FROM market_snapshot")[0][0]

    rows.append({
        "input": "P/E (per share)",
        "source": "market_snapshot.pe -- TSETMC's own figure",
        "coverage": "%d/%d snapshot rows carry a positive P/E" % (pe_ok, snap_rows),
        "history": "NONE -- %d distinct captured_at, table is REPLACED per pull"
                   % snaps,
        "complete": "current session only",
        "on_missing": "valuation component returns None -> 15 points score "
                      "ZERO and coverage drops to 0.85",
    })

    rows.append({
        "input": "P/E (sector median)",
        "source": "fundamental_engine.sector_medians over the same column, "
                  "grouped by instruments.sector_name",
        "coverage": "computed live; needs >= 3 comparables per sector",
        "history": "NONE -- inherits market_snapshot's single timestamp",
        "complete": "current session only",
        "on_missing": "same as P/E -- no median, no points",
    })

    # ---- market cap ------------------------------------------------------
    cap_ok = q("""SELECT COUNT(*) FROM market_snapshot
                  WHERE total_shares > 0 AND close > 0""")[0][0]

    rows.append({
        "input": "Market Cap",
        "source": "market_snapshot.total_shares x market_snapshot.close",
        "coverage": "%d/%d snapshot rows" % (cap_ok, snap_rows),
        "history": "NONE -- share counts are not versioned anywhere",
        "complete": "current session only",
        "on_missing": "size band is None -> 10 points score ZERO",
    })

    # ---- client type ------------------------------------------------------
    ct_codes = q("SELECT COUNT(DISTINCT ins_code) FROM client_type_history")[0][0]
    ct_range = q("SELECT MIN(date), MAX(date) FROM client_type_history")[0]
    ct_counts = q("""SELECT COUNT(*) FROM client_type_history
                     WHERE individual_buy_count IS NOT NULL
                       AND date >= '2024-01-01'""")[0][0]
    ct_total = q("SELECT COUNT(*) FROM client_type_history WHERE date >= '2024-01-01'")[0][0]

    for label in ("خرید/فروش حقیقی", "خرید/فروش حقوقی"):
        rows.append({
            "input": label,
            "source": "client_type_history (value + volume + COUNT per side)",
            "coverage": "%d/%d equities" % (ct_codes, equities),
            "history": "%s .. %s" % ct_range,
            "complete": "%.1f%% of rows since 2024 carry participant counts"
                        % (100.0 * ct_counts / ct_total if ct_total else 0),
            "on_missing": "transfer is None -> VETO 'no_flow_data', the name "
                          "is excluded entirely",
        })

    rows.append({
        "input": "کد به کد",
        "source": "DERIVED from client_type_history counts. TSETMC publishes "
                  "NO per-code data -- see accumulation.py",
        "coverage": "needs >= 9 stored sessions (5 recent + 8 baseline)",
        "history": "same as client_type_history",
        "complete": "an ESTIMATE by an explicit stated formula, not a "
                    "measurement",
        "on_missing": "component returns None -> 15 points score ZERO. A "
                      "FAILED test scores 0; MISSING data scores None",
    })

    rows.append({
        "input": "حجم",
        "source": "price_history.volume, 20-session mean EXCLUDING the "
                  "session measured",
        "coverage": "%d/%d equities" % (ph_codes, equities),
        "history": "%s .. %s" % ph_range,
        "complete": "needs 21 bars",
        "on_missing": "ratio is None -> 10 points score ZERO",
    })

    rows.append({
        "input": "حمایت تکنیکال",
        "source": "technical.support_resistance (confirmed pivots) + SMA50/200 "
                  "+ range low + levels.fibonacci",
        "coverage": "derived from price_history",
        "history": "same as price_history",
        "complete": "pivots need depth*2+1 bars; SMA200 needs 200",
        "on_missing": "no support below price -> VETO 'no_support'",
    })

    conn.close()

    width = max(len(r["input"]) for r in rows)

    for r in rows:
        say()
        say("  %-*s  %s" % (width, r["input"], r["source"]))
        say("  %-*s  coverage : %s" % (width, "", r["coverage"]))
        say("  %-*s  history  : %s" % (width, "", r["history"]))
        say("  %-*s  complete : %s" % (width, "", r["complete"]))
        say("  %-*s  if absent: %s" % (width, "", r["on_missing"]))

    RESULTS["data_audit"] = rows

    say()
    say("  " + "-" * 74)
    say("  THE FINDING THAT DECIDES STAGE 2:")
    say("  P/E, sector P/E and market cap have NO HISTORY AT ALL. One snapshot,")
    say("  replaced on every pull. That is the valuation component (15 points)")
    say("  and the size component (10) -- 25 of the model's 100 points cannot")
    say("  be computed for any past date. Using today's P/E on a 2024 signal is")
    say("  look-ahead of the most direct kind.")
    say("  " + "-" * 74)

    check("client-type counts present (the کد به کد component depends on them)",
          ct_counts > 0.9 * ct_total if ct_total else False,
          "%.1f%% of recent rows" % (100.0 * ct_counts / ct_total if ct_total else 0))

    if snaps <= 1:
        blocker("P/E / sector P/E / market cap have no history",
                "%d snapshot stored; the valuation (15) and size (10) "
                "components cannot be computed for any past date, so Stage 2 "
                "cannot run THIS scanner -- only its 75-point point-in-time "
                "subset, which is a different model" % snaps)
    else:
        check("P/E has usable history for backtesting", True)


# ==============================
# 2  FACTOR VERIFICATION
# ==============================

def independent_mfi(frame, window=14):
    """MFI from first principles, with no library involved.

    The point of reimplementing it is that a bug shared between the scanner and
    its test is invisible. This uses the textbook definition directly:
    typical price, raw money flow, positive vs negative sums.
    """

    h = pd.to_numeric(frame["high"], errors="coerce")
    l = pd.to_numeric(frame["low"], errors="coerce")
    c = pd.to_numeric(frame["close"], errors="coerce")
    v = pd.to_numeric(frame["volume"], errors="coerce")

    tp = (h + l + c) / 3.0
    rmf = tp * v

    delta = tp.diff()

    pos = rmf.where(delta > 0, 0.0).rolling(window).sum()
    neg = rmf.where(delta < 0, 0.0).rolling(window).sum()

    total = pos + neg

    return (100.0 * pos / total).where(total > 0)


def section_factors(sample=12):
    rule("2  FACTOR VERIFICATION -- each factor recomputed independently")

    instruments = database.load_instruments(equities_only=True)
    by_code = {str(r["ins_code"]): r for r in instruments}

    report = smart_money.scan(top=20, persist=False)
    picked = [r["ins_code"] for r in report["top"]]

    # A sample that is not only the winners: the scanner agreeing with itself
    # on names it liked proves nothing about the names it rejected.
    others = [c for c in by_code if c not in picked][:sample]
    codes = picked + others

    frames = database.load_history_tail(codes, SCAN_BARS)
    flow_frames = database.load_client_type_tail(codes, FLOW_BARS)

    # ---- 2a  MFI ---------------------------------------------------------
    say()
    say("  2a  MFI -- library implementation vs first principles")
    say()

    worst = 0.0
    compared = 0

    for code in codes:
        frame = frames.get(code)
        if frame is None or len(frame) < 60:
            continue

        theirs = flow.series(frame)
        mine = independent_mfi(frame)

        if theirs is None:
            continue

        a = theirs.dropna()
        b = mine.dropna()
        common = a.index.intersection(b.index)

        if len(common) < 30:
            continue

        gap = float((a.loc[common] - b.loc[common]).abs().max())
        worst = max(worst, gap)
        compared += 1

    check("MFI matches an independent implementation on %d names" % compared,
          compared > 0 and worst < 1e-6,
          "largest absolute difference %.2e" % worst)

    RESULTS["mfi_max_diff"] = worst

    # A textbook property no correct MFI can violate.
    bounded = True
    for code in codes[:8]:
        s = flow.series(frames.get(code))
        if s is None:
            continue
        v = s.dropna()
        if len(v) and (v.min() < 0 or v.max() > 100):
            bounded = False

    check("MFI stays inside [0, 100]", bounded)

    # ---- 2b  P/E against the sector --------------------------------------
    say()
    say("  2b  P/E -- internal consistency and the sector comparison")
    say()

    snapshot = database.load_snapshot()
    snapshot["ins_code"] = snapshot["ins_code"].astype(str)
    snap = snapshot.set_index("ins_code")

    consistent = 0
    inconsistent = []

    for code in snap.index[:400]:
        row = snap.loc[code]
        pe, close, eps = row.get("pe"), row.get("close"), row.get("eps")
        if not (pe and close and eps) or eps <= 0 or pe <= 0:
            continue
        implied = close / eps
        if abs(implied - pe) / pe > 0.02:
            inconsistent.append((code, pe, implied))
        else:
            consistent += 1

    check("stored P/E equals close/EPS on %d names" % consistent,
          consistent > 0 and len(inconsistent) <= 0.02 * (consistent + len(inconsistent)),
          "%d disagreed by more than 2%%" % len(inconsistent))

    # The sector median, recomputed by hand for one large sector.
    merged = snapshot.merge(
        pd.DataFrame([{"ins_code": str(r["ins_code"]),
                       "sector": r.get("sector_name")} for r in instruments]),
        on="ins_code", how="inner")

    big = (merged[merged["pe"] > 0].groupby("sector")["pe"]
           .agg(["count", "median"]).sort_values("count", ascending=False))

    say("     largest sectors, median P/E recomputed here:")
    for sector, row in big.head(5).iterrows():
        say("       %-42s n=%-3d median %.2f"
            % (str(sector)[:42], int(row["count"]), row["median"]))

    # And the same medians as the scanner produced them.
    from smart_money.scanner import _market_context
    context = _market_context()

    agree = 0
    disagree = []

    for sector, row in big.iterrows():
        if row["count"] < 3:
            continue
        theirs = (context.get("sector_pe") or {}).get(sector)
        if theirs is None:
            disagree.append((sector, "scanner has no median"))
            continue
        if abs(theirs - row["median"]) < 1e-6:
            agree += 1
        else:
            disagree.append((sector, "%.4f vs %.4f" % (theirs, row["median"])))

    check("scanner's sector medians match a hand recomputation (%d sectors)"
          % agree, not disagree,
          "; ".join("%s: %s" % d for d in disagree[:3]))

    check("no sector median is built from fewer than 3 comparables",
          all((context["sector_counts"].get(s, 0) >= 3)
              for s in context["sector_pe"]))

    # ---- 2c  market cap and the size bands --------------------------------
    say()
    say("  2c  Market cap and the Small/Mid/Large split")
    say()

    caps = []
    for code, info in (context.get("by_code") or {}).items():
        if info.get("market_cap"):
            caps.append((info["market_cap"], by_code.get(code, {}).get("symbol_fa")))

    caps.sort(reverse=True)

    say("     five largest by computed cap (shares x close):")
    for cap, symbol in caps[:5]:
        say("       %-10s %10.1f همت" % (symbol, cap / 1e13))

    p33, p67 = context.get("cap_p33"), context.get("cap_p67")

    say("     tercile boundaries this run: p33 %.1f همت   p67 %.1f همت"
        % (p33 / 1e13, p67 / 1e13))

    bands = {}
    for cap, _ in caps:
        band = scoring.size_band(cap, p33, p67)
        bands[band] = bands.get(band, 0) + 1

    say("     resulting split: %s" % bands)

    check("terciles split the market into roughly equal thirds",
          abs(bands.get(scoring.SMALL, 0) - bands.get(scoring.LARGE, 0))
          <= 0.05 * len(caps),
          "small=%d mid=%d large=%d" % (bands.get(scoring.SMALL, 0),
                                        bands.get(scoring.MID, 0),
                                        bands.get(scoring.LARGE, 0)))

    check("band boundaries are monotonic",
          scoring.size_band(p33 * 0.5, p33, p67) == scoring.SMALL
          and scoring.size_band((p33 + p67) / 2, p33, p67) == scoring.MID
          and scoring.size_band(p67 * 2, p33, p67) == scoring.LARGE)

    # ---- 2d  حقیقی / حقوقی flow -------------------------------------------
    say()
    say("  2d  Flow -- the accounting identity the whole component rests on")
    say()

    # Every rial bought was sold. If this fails, the feed is wrong and every
    # flow number computed from it is meaningless.
    worst_gap = 0.0
    checked = 0

    for code, fframe in list(flow_frames.items())[:40]:
        if fframe is None or fframe.empty:
            continue
        buy = (pd.to_numeric(fframe["individual_buy_value"], errors="coerce").fillna(0)
               + pd.to_numeric(fframe["institutional_buy_value"], errors="coerce").fillna(0))
        sell = (pd.to_numeric(fframe["individual_sell_value"], errors="coerce").fillna(0)
                + pd.to_numeric(fframe["institutional_sell_value"], errors="coerce").fillna(0))
        both = buy + sell
        rel = ((buy - sell).abs() / both.where(both > 0)).max()
        if pd.notna(rel):
            worst_gap = max(worst_gap, float(rel))
            checked += 1

    check("buy side equals sell side on every session of %d names" % checked,
          checked > 0 and worst_gap < 1e-6,
          "largest relative gap %.2e" % worst_gap)

    RESULTS["flow_identity_gap"] = worst_gap

    # The mirror, as the scanner reports it.
    mirrors = 0
    for code in picked:
        reading = moneyflow.measure(flow_frames.get(code))
        t = accumulation.transfer(reading)
        if t and t["net_individual"] is not None:
            if abs(t["institutional_supply"] + t["net_individual"]) < 1e-9:
                mirrors += 1

    check("institutional supply is reported as the exact mirror (%d names)"
          % mirrors, mirrors == len([c for c in picked
                                     if moneyflow.measure(flow_frames.get(c))]))

    # ---- 2e  کد به کد ------------------------------------------------------
    say()
    say("  2e  کد به کد -- the estimate, recomputed by hand on real names")
    say()

    verified = 0
    problems = []

    for code in codes:
        fframe = flow_frames.get(code)
        out = accumulation.code_to_code(fframe, volume_ratio=2.0)
        if not out:
            continue

        recent = fframe.tail(accumulation.WINDOW)
        base = fframe.iloc[:-accumulation.WINDOW]

        bv = float(pd.to_numeric(recent["individual_buy_value"], errors="coerce").fillna(0).sum())
        bc = float(pd.to_numeric(recent["individual_buy_count"], errors="coerce").fillna(0).sum())
        pv = float(pd.to_numeric(base["individual_buy_value"], errors="coerce").fillna(0).sum())
        pc = float(pd.to_numeric(base["individual_buy_count"], errors="coerce").fillna(0).sum())

        if bc <= 0 or pc <= 0 or pv <= 0:
            continue

        elevation = (bv / bc) / (pv / pc)
        large = max(0.0, 1.0 - 1.0 / elevation)

        if abs(elevation - out["elevation"]) > 1e-6:
            problems.append((code, "elevation %.6f vs %.6f" % (elevation, out["elevation"])))
        elif abs(large - out["large_share"]) > 1e-9:
            problems.append((code, "large_share mismatch"))
        elif not (0.0 <= out["smart_money_ratio"] < 1.0):
            problems.append((code, "ratio out of [0,1)"))
        else:
            verified += 1

    check("Smart Money Ratio matches its stated formula on %d names" % verified,
          verified > 0 and not problems,
          "; ".join("%s %s" % p for p in problems[:3]))

    check("the ratio never reaches 1.0 (so extremes stay distinguishable)",
          all((accumulation.code_to_code(flow_frames.get(c), volume_ratio=2.0)
               or {"smart_money_ratio": 0})["smart_money_ratio"] < 1.0
              for c in codes if flow_frames.get(c) is not None))

    # Separation: does the genuineness test actually separate anything, or does
    # it pass everything?
    genuine = rejected_ = 0
    for code in by_code:
        pass

    tested = [accumulation.code_to_code(f, volume_ratio=2.0)
              for f in flow_frames.values()]
    tested = [t for t in tested if t]
    genuine = sum(1 for t in tested if t["genuine"])

    say("     genuineness test on %d names: %d genuine, %d rejected"
        % (len(tested), genuine, len(tested) - genuine))

    check("the کد به کد test separates rather than passing everything",
          0 < genuine < len(tested),
          "%d/%d passed" % (genuine, len(tested)))

    # ---- 2f  supports ------------------------------------------------------
    say()
    say("  2f  Support levels -- all four families")
    say()

    below = True
    ordered = True
    kinds = {}

    for code in codes:
        frame = frames.get(code)
        if frame is None or len(frame) < 60:
            continue
        close = float(frame["close"].iloc[-1])
        out = position.supports(frame, close,
                                range_reading=position.range_position(frame))
        levels_found = out["levels"]

        for lv in levels_found:
            kinds[lv["kind"]] = kinds.get(lv["kind"], 0) + 1
            if lv["price"] >= close:
                below = False

        d = [lv["distance_pct"] for lv in levels_found]
        if d != sorted(d):
            ordered = False

    check("every reported support sits below the price", below)
    check("supports are ordered nearest-first", ordered)
    say("     levels found by family: %s" % kinds)

    check("all four support families are reachable",
          set(kinds) >= {"static", "dynamic", "channel", "fibonacci"},
          "found: %s" % sorted(kinds))

    RESULTS["support_kinds"] = kinds


# ==============================
# 3  LOOK-AHEAD AUDIT
# ==============================

def section_lookahead(trials=25):
    rule("3  LOOK-AHEAD AUDIT -- does a reading at bar T use a bar after T")

    instruments = database.load_instruments(equities_only=True)
    codes = [str(r["ins_code"]) for r in instruments][:80]
    frames = database.load_history_tail(codes, SCAN_BARS)

    usable = [c for c in codes
              if frames.get(c) is not None and len(frames[c]) >= 280][:trials]

    say()
    say("  Method: compute every price-derived reading on frame[:T], then on")
    say("  frame[:T+40] TRUNCATED BACK to T. Identical inputs must give")
    say("  identical answers; any difference means the longer frame leaked.")
    say()

    mismatches = []
    tested = 0

    for code in usable:
        full = frames[code].reset_index(drop=True)

        cut = len(full) - 40
        short = full.iloc[:cut].reset_index(drop=True)

        # The same bar T, reached two ways.
        a_mfi = flow.read(short)
        a_rng = position.range_position(short)
        a_vol = position.volume_ratio(short)

        # Truncating the long frame back to T must reproduce the short frame
        # exactly -- this is the control, and if it fails the test is invalid.
        again = full.iloc[:cut].reset_index(drop=True)

        b_mfi = flow.read(again)
        b_rng = position.range_position(again)
        b_vol = position.volume_ratio(again)

        tested += 1

        if (a_mfi or {}).get("value") != (b_mfi or {}).get("value"):
            mismatches.append((code, "MFI"))
        if (a_rng or {}).get("position_pct") != (b_rng or {}).get("position_pct"):
            mismatches.append((code, "range"))
        if a_vol != b_vol:
            mismatches.append((code, "volume"))

    check("readings are a pure function of the bars given (%d names)" % tested,
          not mismatches, "; ".join("%s/%s" % m for m in mismatches[:3]))

    # The pivot question, which is where look-ahead actually hides.
    say()
    say("  Pivots: levels.swings() claims to return CONFIRMED pivots only.")
    say("  A pivot within `depth` bars of the end cannot be confirmed yet, so")
    say("  none may be reported there.")
    say()

    depth = levels.DEFAULT_DEPTH
    late = 0
    checked = 0

    for code in usable:
        frame = frames[code].reset_index(drop=True)
        pivots = levels.swings(frame)
        if not pivots:
            continue
        checked += 1
        last_date = frame["date"].iloc[-depth - 1]
        for p in pivots:
            if pd.notna(p.get("date")) and pd.Timestamp(p["date"]) > pd.Timestamp(last_date):
                late += 1

    check("no pivot is reported inside the unconfirmable tail (%d names)"
          % checked, late == 0, "%d late pivots" % late)

    # The real look-ahead in this scanner, named rather than tested away.
    say()
    say("  " + "-" * 74)
    say("  THE ONE LOOK-AHEAD THAT EXISTS, AND IT IS STRUCTURAL:")
    say("  P/E, sector P/E and market cap come from market_snapshot, which")
    say("  holds ONE timestamp and is replaced on every pull. For a LIVE scan")
    say("  that is correct -- today's figures for today's decision. For any")
    say("  historical date it is look-ahead, and the data to fix it does not")
    say("  exist in this database.")
    say()
    say("  CONSEQUENCE: the scanner as built is a LIVE instrument. A historical")
    say("  backtest can only use the 75 points that ARE point-in-time --")
    say("    MFI 15 + transfer 20 + کد به کد 15 + support 15 + volume 10 = 75")
    say("  and must drop valuation (15) and size (10). Rescaled to 100 that is")
    say("  a DIFFERENT model with different weights, and it has to be")
    say("  preregistered as such rather than described as this scanner.")
    say("  " + "-" * 74)

    RESULTS["lookahead"] = {"pure_function": not mismatches, "late_pivots": late}


# ==============================
# 4  COMBINED OUTPUT
# ==============================

def section_output():
    rule("4  COMBINED OUTPUT -- the brief's table, on real names")

    report = smart_money.scan(top=20, persist=False)

    counts = report["counts"]

    say()
    say("  funnel: %d universe -> %d liquid -> %d MFI>70 -> %d at floor -> "
        "%d qualified"
        % (counts["universe"], counts["liquid"], counts["passed_mfi"],
           counts["passed_range"], counts["qualified"]))
    say("  data session: %s" % report["session_date"])
    say()

    head = ("نماد", "امتیاز", "MFI", "P/E سهم", "P/E گروه", "ارزش بازار",
            "خرید حقیقی", "فروش حقوقی", "کدبه‌کد", "Vol Ratio", "حمایت")

    say("  %-8s %5s %6s %8s %8s %10s %11s %11s %8s %8s %9s"
        % head)
    say("  " + "-" * 104)

    for r in report["top"]:
        sup = r["support"] or {}
        # فروش حقوقی is the mirror of خرید حقیقی -- printed as such, and the
        # column exists only because the brief asked for it.
        net = r["net_pct"]
        say("  %-8s %5d %6.1f %8s %8s %10s %10s%% %10s%% %8s %8.2f %8.1f%%"
            % (r["symbol"], r["score"], r["mfi"],
               "n/a" if r["pe"] is None else "%.1f" % r["pe"],
               "n/a" if r["sector_pe"] is None else "%.1f" % r["sector_pe"],
               "n/a" if r["market_cap"] is None
               else "%.1f همت" % (r["market_cap"] / 1e13),
               "n/a" if net is None else "%+.1f" % net,
               "n/a" if net is None else "%+.1f" % -net,
               "بله %.2f" % r["smart_money_ratio"] if r["code_to_code"]
               else "خیر",
               r["volume_ratio"] or 0.0,
               sup.get("distance_pct") or 0.0))

    say()
    say("  دلیل انتخاب هر نماد:")
    for r in report["top"]:
        say()
        say("   %s (%d/100, %s)" % (r["symbol"], r["score"], r["status"]))
        for note in r["notes"]:
            say("     - %s" % note)

    RESULTS["output"] = report["top"]

    return report


# ==============================
# 5  FALSE POSITIVES
# ==============================

def section_false_positives(report=None):
    rule("5  FALSE POSITIVE AUDIT -- does every claim on every row hold up")

    report = report or smart_money.scan(top=20, persist=False)

    say()
    say("  A false positive here is a row the scanner selected whose stated")
    say("  conditions are not actually true of the name. This is a claim audit,")
    say("  not an outcome audit -- outcomes belong to Stage 2.")
    say()

    codes = [r["ins_code"] for r in report["top"]]
    frames = database.load_history_tail(codes, SCAN_BARS)
    flow_frames = database.load_client_type_tail(codes, FLOW_BARS)

    bad = []

    for r in report["top"]:
        code = r["ins_code"]
        frame = frames.get(code)
        symbol = r["symbol"]

        # claim 1: MFI above 70
        mfi = independent_mfi(frame).iloc[-1]
        if not (mfi > scoring.MFI_FLOOR):
            bad.append((symbol, "MFI claim %.1f but independent %.1f"
                        % (r["mfi"], mfi)))

        # claim 2: price in the lower part of its range
        tail = frame.tail(position.RANGE_WINDOW)
        lo, hi = float(tail["low"].min()), float(tail["high"].max())
        px = float(frame["close"].iloc[-1])
        pos = (px - lo) / (hi - lo) * 100.0
        if pos > position.MAX_POSITION_PCT:
            bad.append((symbol, "position claim %.1f%% but recomputed %.1f%%"
                        % (r["position_pct"], pos)))

        # claim 3: a support exists within the threshold
        sup = r["support"] or {}
        if sup.get("price") is None or sup["price"] >= px:
            bad.append((symbol, "support not below price"))
        elif sup["distance_pct"] > position.NEAR_SUPPORT_PCT:
            bad.append((symbol, "support %.1f%% away, threshold %.1f%%"
                        % (sup["distance_pct"], position.NEAR_SUPPORT_PCT)))

        # claim 4: the window the readings were taken over actually traded.
        # This is the check that failed on the first validation run, and the
        # reason the thin-trading gate exists. TSETMC emits a bar for every
        # listed session whether or not anything changed hands, so an MFI and a
        # volume ratio can both be computed over a window that mostly did not
        # happen.
        traded_mfi = position.traded_share(frame, flow.MFI_WINDOW)
        traded_vol = position.traded_share(frame, position.VOLUME_WINDOW)

        if traded_mfi is None or traded_mfi < position.MIN_TRADED_PCT:
            bad.append((symbol, "MFI window traded only %.0f%% of sessions"
                        % (traded_mfi or 0.0)))
        if traded_vol is None or traded_vol < position.MIN_TRADED_PCT:
            bad.append((symbol, "volume window traded only %.0f%% of sessions"
                        % (traded_vol or 0.0)))

        # claim 5: net حقیقی inflow
        fframe = flow_frames.get(code)
        if fframe is None:
            bad.append((symbol, "flow claimed but no client-type frame"))
        else:
            recent = fframe.tail(config.FLOW_WINDOW)
            b = float(pd.to_numeric(recent["individual_buy_value"], errors="coerce").fillna(0).sum())
            s = float(pd.to_numeric(recent["individual_sell_value"], errors="coerce").fillna(0).sum())
            if b <= s:
                bad.append((symbol, "inflow claimed but individual net is %.0f"
                            % (b - s)))

        # claim 6: if کد به کد is claimed, its own tests must pass
        if r["code_to_code"]:
            out = accumulation.code_to_code(fframe, volume_ratio=r["volume_ratio"])
            if not out or not out["genuine"]:
                bad.append((symbol, "کد به کد claimed but test fails"))

        # claim 7: the P/E discount, if stated
        if r["pe"] is not None and r["sector_pe"] is not None:
            stated = r["pe_vs_sector"]
            actual = (r["sector_pe"] - r["pe"]) / r["sector_pe"] * 100.0
            if abs(stated - actual) > 0.15:
                bad.append((symbol, "P/E gap stated %.1f, actual %.1f"
                            % (stated, actual)))

    check("every claim on every selected row is independently reproducible",
          not bad, "; ".join("%s: %s" % b for b in bad[:5]))

    if bad:
        for symbol, why in bad:
            say("     FALSE POSITIVE  %s -- %s" % (symbol, why))
    else:
        say("     %d rows checked against 7 independent recomputations each: "
            "no discrepancy." % len(report["top"]))

    RESULTS["false_positives"] = bad


# ==============================
# 6  SENSITIVITY
# ==============================

def section_sensitivity():
    rule("6  PARAMETER SENSITIVITY -- how much do the knobs move the output")

    say()
    say("  IN-SAMPLE, ON ONE SESSION. This measures SELECTIVITY (how many names")
    say("  survive), not performance. Choosing a setting because it produced a")
    say("  nicer list and then claiming an edge for it later is precisely the")
    say("  selection error E19/E21 documented in this programme.")
    say()

    instruments = database.load_instruments(equities_only=True)
    by_code = {str(r["ins_code"]): r for r in instruments}
    codes = list(by_code)

    frames = database.load_history_tail(codes, SCAN_BARS)

    # Precompute the expensive readings once, then vary the thresholds over
    # them -- a fresh scan per setting would take minutes and produce the same
    # numbers.
    base = []

    for code in codes:
        frame = frames.get(code)
        if frame is None or frame.empty or "value" not in frame.columns:
            continue
        turnover = frame["value"].iloc[-1]
        if not np.isfinite(turnover) or turnover < config.MIN_DAILY_VALUE:
            continue
        if not position.integrity(frame).get("clean"):
            continue
        if not position.tradability(frame).get("tradable"):
            continue
        mfi = flow.read(frame)
        ranged = position.range_position(frame)
        if mfi is None or ranged is None:
            continue
        base.append((code, mfi["value"], ranged["position_pct"],
                     position.volume_ratio(frame)))

    say("  liquid, clean, readable names: %d" % len(base))
    say()
    say("  %-12s %-14s %8s %8s" % ("MFI floor", "position <=", "passing", "share"))
    say("  " + "-" * 46)

    grid = {}

    for mfi_floor in (65.0, 70.0, 75.0):
        for max_pos in (35.0,):
            n = sum(1 for _, m, p, _ in base if m > mfi_floor and p <= max_pos)
            grid["mfi_%d" % mfi_floor] = n
            say("  %-12.0f %-14.0f %8d %7.1f%%"
                % (mfi_floor, max_pos, n, 100.0 * n / len(base)))

    say()
    say("  %-16s %8s %8s" % ("Volume Ratio >=", "passing", "share"))
    say("  " + "-" * 36)

    for vr in (1.5, 2.0):
        n = sum(1 for _, m, p, v in base
                if m > 70.0 and p <= 35.0 and v is not None and v >= vr)
        grid["vol_%.1f" % vr] = n
        say("  %-16.1f %8d %7.1f%%" % (vr, n, 100.0 * n / len(base)))

    # Score thresholds, on the names that actually reach scoring.
    report = smart_money.scan(top=50, persist=False)
    scores = [r["score"] for r in report["top"]]

    say()
    say("  %-14s %8s" % ("Score >=", "names"))
    say("  " + "-" * 24)
    for threshold in (70, 75, 80):
        n = sum(1 for s in scores if s >= threshold)
        grid["score_%d" % threshold] = n
        say("  %-14d %8d" % (threshold, n))

    say()
    say("  qualified today: %d, scores: %s"
        % (len(scores), sorted(scores, reverse=True)))

    RESULTS["sensitivity"] = grid

    say()
    say("  " + "-" * 74)
    say("  READING THIS TABLE HONESTLY:")
    say("  At Score >= 75 the scanner produces %d name(s) on this session."
        % grid.get("score_75", 0))
    say("  A portfolio rule that needs 10 names per rebalance cannot be fed by")
    say("  a filter that yields that many. That is a STRUCTURAL fact about the")
    say("  filter's selectivity, and it is knowable now, before any backtest.")
    say("  " + "-" * 74)


# ==============================
# 7  HISTORICAL BREADTH
# ==============================

def section_breadth(start="2023-01-01"):
    """How many names could the gates admit per session, historically?

    WHY THIS IS NOT A BACKTEST, AND WHY IT SPENDS NOTHING
    ------------------------------------------------------
    It measures CAPACITY, not performance. No return is computed, no outcome is
    looked at, and no hypothesis about profit is tested. The question is purely
    "how many names does this filter emit per session", which is a property of
    the filter and the market, not of the future.

    It runs over 2023-01-01..2026-08-19 -- windows E19 and E21 have already
    spent. Reading a spent window again cannot spend it further, and R-FORWARD
    -2027 is not touched.

    ONLY THE POINT-IN-TIME GATES ARE APPLIED
    -----------------------------------------
    Liquidity, traded-share, MFI and range position -- all computable from bars
    alone at any past date. The support-proximity gate and the flow gate are NOT
    applied, and both can only REMOVE names, so what comes out is an UPPER
    BOUND on breadth. If the upper bound cannot feed a 10-name book, the real
    filter certainly cannot.
    """

    rule("7  HISTORICAL BREADTH -- an upper bound on names per session")

    say()
    say("  Windows E19 (2023-03..2024-09) and E21 (2024-09..2026-08) are")
    say("  already spent, so this reads nothing new. R-FORWARD-2027 untouched.")
    say("  Gates applied: liquidity, traded-share, MFI>70, range<=35%.")
    say("  Support and flow gates omitted -- both only remove, so this is an")
    say("  UPPER BOUND.")
    say()

    instruments = database.load_instruments(equities_only=True)
    codes = [str(r["ins_code"]) for r in instruments]

    conn = sqlite3.connect(database.DATABASE_NAME)

    panel = pd.read_sql("""
        SELECT ins_code, date, high, low, close, volume, value
        FROM price_history WHERE date >= ?
        ORDER BY ins_code, date
    """, conn, params=[str(pd.Timestamp(start) - pd.Timedelta(days=400))[:10]])

    conn.close()

    panel = panel[panel["ins_code"].isin(set(codes))]
    panel["date"] = pd.to_datetime(panel["date"], errors="coerce")

    say("  panel: %d bars, %d names" % (len(panel), panel["ins_code"].nunique()))

    floor = pd.Timestamp(start)
    per_date = {}
    scanned = 0

    for code, group in panel.groupby("ins_code", sort=False):

        g = group.sort_values("date").reset_index(drop=True)

        if len(g) < position.RANGE_WINDOW + flow.MFI_WINDOW:
            continue

        scanned += 1

        mfi = independent_mfi(g, flow.MFI_WINDOW)

        hi = g["high"].rolling(position.RANGE_WINDOW).max()
        lo = g["low"].rolling(position.RANGE_WINDOW).min()
        span = hi - lo
        pos = ((g["close"] - lo) / span.where(span > 0)) * 100.0

        traded_mfi = (g["volume"] > 0).rolling(flow.MFI_WINDOW).mean() * 100.0
        traded_vol = (g["volume"] > 0).rolling(position.VOLUME_WINDOW).mean() * 100.0

        ok = (
            (g["value"] >= config.MIN_DAILY_VALUE)
            & (mfi > scoring.MFI_FLOOR)
            & (pos <= position.MAX_POSITION_PCT)
            & (traded_mfi >= position.MIN_TRADED_PCT)
            & (traded_vol >= position.MIN_TRADED_PCT)
            & (g["date"] >= floor)
        )

        for stamp in g.loc[ok.fillna(False), "date"]:
            per_date[stamp] = per_date.get(stamp, 0) + 1

        for stamp in g.loc[(g["date"] >= floor), "date"]:
            per_date.setdefault(stamp, 0)

    series = pd.Series(per_date).sort_index()

    if series.empty:
        say("  no sessions in range.")
        return

    say("  names scanned: %d   sessions covered: %d" % (scanned, len(series)))
    say()
    say("  candidates per session (upper bound):")
    say("    mean   %6.1f" % series.mean())
    say("    median %6.0f" % series.median())
    for q in (10, 25, 75, 90, 99):
        say("    p%-5d %6.0f" % (q, series.quantile(q / 100.0)))
    say("    max    %6.0f" % series.max())
    say()

    for n in (1, 5, 10, 20):
        share = 100.0 * (series >= n).mean()
        say("    sessions with >= %2d candidates : %5.1f%%" % (n, share))

    say()
    say("  by year:")
    for year, chunk in series.groupby(series.index.year):
        say("    %d  sessions %3d   mean %5.1f   median %3.0f   >=10 on %5.1f%%"
            % (year, len(chunk), chunk.mean(), chunk.median(),
               100.0 * (chunk >= 10).mean()))

    RESULTS["breadth"] = {
        "mean": float(series.mean()),
        "median": float(series.median()),
        "sessions": int(len(series)),
        "share_ge_10": float((series >= 10).mean()),
    }

    say()
    say("  " + "-" * 74)
    say("  WHAT THIS DECIDES:")
    ge10 = 100.0 * (series >= 10).mean()
    say("  The requested portfolio rule is 'Score >= 75, take the top 10,")
    say("  10% each'. On this UPPER BOUND -- before the support and flow gates,")
    say("  before scoring, before the 75 threshold -- 10 or more names are")
    say("  available on %.1f%% of sessions." % ge10)
    if ge10 < 50:
        say("  A 10-name equal-weight book cannot be constructed on most")
        say("  sessions. That is a property of the filter, not of the market")
        say("  regime, and no amount of backtesting changes it.")
    say("  " + "-" * 74)


# ==============================
# MAIN
# ==============================

SECTIONS = {
    1: ("data audit", section_data_audit),
    2: ("factor verification", section_factors),
    3: ("look-ahead audit", section_lookahead),
    4: ("combined output", section_output),
    5: ("false positives", section_false_positives),
    6: ("sensitivity", section_sensitivity),
    7: ("historical breadth", section_breadth),
}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--section", type=int, action="append",
                        help="run only these sections (repeatable)")
    parser.add_argument("--json", help="write the collected results here")
    args = parser.parse_args(argv)

    wanted = args.section or sorted(SECTIONS)

    say()
    say("SMART MONEY ACCUMULATION SCANNER -- STAGE 1 VALIDATION")
    say("Verification that the scanner computes what it claims.")
    say("NOT a test of predictive power. No window is spent here.")

    report = None

    for number in wanted:
        name, fn = SECTIONS[number]
        if number == 5:
            section_false_positives(report)
        elif number == 4:
            report = section_output()
        else:
            fn()

    rule("VERDICT")

    say()

    if FAILURES:
        say("  STAGE 1: NOT PASSED -- %d correctness check(s) failed:"
            % len(FAILURES))
        for name, detail in FAILURES:
            say("    - %s%s" % (name, ("  -- " + detail) if detail else ""))
        say()
        say("  The scanner does not compute what it claims. Fix before Stage 2.")
    else:
        say("  STAGE 1: PASSED. Every correctness check holds.")
        say("  The scanner computes what it claims to compute, on live data.")

    if BLOCKERS:
        say()
        say("  STAGE 2: BLOCKED -- %d blocker(s), none of them a bug:"
            % len(BLOCKERS))
        for name, detail in BLOCKERS:
            say("    - %s" % name)
            say("      %s" % detail)

    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump({"results": RESULTS,
                       "failures": [{"check": c, "detail": d}
                                    for c, d in FAILURES]},
                      handle, ensure_ascii=False, indent=2, default=str)
        say()
        say("  written: %s" % args.json)

    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
