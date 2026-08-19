"""E19: does Alpha Core contain predictive information that survives trading?

REGISTERED BEFORE THIS RAN
--------------------------
`backtest_output/E19_preregistration.json`, hash 9fd80d29..., frozen in the
research database before a single number below was computed. The declared
traded horizon is h=20 -- the production rebalance interval -- and h=1/5/10 are
measured for the decay curve and cannot pass gate 3. Reporting whichever
horizon looks best would be a multiple comparison wearing a finding's clothes.

THE SEPARATION THIS MODULE EXISTS TO MAINTAIN
-----------------------------------------------
Three questions, answered in three isolated blocks, because Phase 1 answered
them together and could not then say which one had failed:

    PREDICTION      does the score order future returns?
                    no portfolio, no costs, no execution
    CONSTRUCTION    does that ordering survive being turned into a book?
                    top-N, turnover, costs -- still no execution
    EXECUTION       does the book survive T+1, queues and unfilled orders?

If the edge dies between block one and block three, the failure is an execution
failure and not a prediction failure, and the report has to be able to say so.

WHAT THIS DOES NOT DO
---------------------
It does not run the portfolio engine over thirty seeds -- E11 already did that
and the artifact is on disk. It works on the frozen panel, which is where the
prediction question actually lives, and it cites E11/E15 for the portfolio-level
numbers rather than recomputing them worse.

Alpha Core is not touched. Not one parameter, threshold, horizon or rule.
"""

import hashlib
import json
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import numpy as np                                                  # noqa: E402
import pandas as pd                                                 # noqa: E402

import backtest_config                                              # noqa: E402
import config                                                       # noqa: E402
import regime as regime_module                                      # noqa: E402
from indicators import DEFAULT_PARAMS                               # noqa: E402
from sirat.fixtures import build_panel                              # noqa: E402


OUT = os.path.join(ROOT, "backtest_output", "E19_edge_audit.json")

AUDIT_PANEL = os.path.join(ROOT, "backtest_output", "E19_audit_panel.csv.gz")

SEED = 20260819

HORIZON = 20                     # declared. Not chosen.

DECAY_HORIZONS = (1, 5, 10, 20)

BASKET = 10

MIN_NAMES = 50

ROUND_TRIP = config.TRANSACTION_COST_PCT

SESSIONS_PER_YEAR = config.TRADING_DAYS_PER_YEAR

NULL_ARMS = 30

# Preregistered splits, by date, fixed before any number existed.
IN_SAMPLE = ("2014-09-21", "2021-09-01")
VALIDATION = ("2021-09-01", "2023-03-01")
HOLDOUT = ("2023-03-01", "2024-09-01")


def say(message=""):
    print(message, flush=True)


def rule(title):
    say()
    say("=" * 78)
    say(title)
    say("=" * 78)


# ==============================================================
# THE AUDIT PANEL
# ==============================================================

def build_audit_panel(verbose=True):
    """The fixture panel, plus the conditioning variables the audit needs.

    Kept separate from the regression fixture's panel on purpose. The fixture
    holds the minimum needed to re-derive five anchors and must not grow; this
    one carries sector, liquidity and volatility so the IC can be cut by them.
    """

    if os.path.exists(AUDIT_PANEL):
        frame = pd.read_csv(AUDIT_PANEL, compression="gzip")
        frame["date"] = pd.to_datetime(frame["date"])
        frame["ins_code"] = frame["ins_code"].astype(str)
        for column in ("next_locked_up", "next_unbuyable"):
            frame[column] = frame[column].astype(bool)
        if verbose:
            say("  audit panel loaded: %d rows" % len(frame))
        return frame

    base = build_panel.load()

    path = os.path.join(backtest_config.OUTPUT_DIR, "factor_panel.csv")

    extra = pd.read_csv(path, encoding="utf-8-sig",
                        usecols=["date", "ins_code", "sector", "adv",
                                 "volatility_60", "close"])

    extra["date"] = pd.to_datetime(extra["date"])
    extra["ins_code"] = extra["ins_code"].astype(str)

    frame = base.merge(extra, on=["date", "ins_code"], how="left")

    frame.to_csv(AUDIT_PANEL, index=False, compression="gzip",
                 date_format="%Y-%m-%d", float_format="%.6f")

    if verbose:
        say("  audit panel built: %d rows, %d dates"
            % (len(frame), frame["date"].nunique()))

    return frame


def panel_hash(frame):
    return hashlib.sha256(
        frame.to_csv(index=False, date_format="%Y-%m-%d",
                     float_format="%.6f").encode("utf-8")).hexdigest()


# ==============================================================
# SHARED STATISTICS
# ==============================================================

def spearman(x, y):
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    good = np.isfinite(x) & np.isfinite(y)
    x, y = x[good], y[good]
    if len(x) < 3:
        return np.nan
    rx = pd.Series(x).rank().to_numpy()
    ry = pd.Series(y).rank().to_numpy()
    if rx.std() == 0 or ry.std() == 0:
        return np.nan
    return float(np.corrcoef(rx, ry)[0, 1])


def ic_series(panel, horizon=HORIZON, min_names=MIN_NAMES):
    """Per-date IC. The unit every prediction number is built from."""

    column = "fwd_%d" % horizon

    rows = []

    for date, block in panel.groupby("date", sort=True):
        good = block[["alpha", column]].dropna()
        if len(good) < min_names:
            continue
        value = spearman(good["alpha"], good[column])
        if np.isfinite(value):
            rows.append({"date": date, "names": len(good), "ic": value})

    return pd.DataFrame(rows)


def summarise_ic(frame, label=""):
    if frame is None or frame.empty:
        return {"label": label, "dates": 0, "mean_ic": None, "icir": None,
                "t_stat": None, "pct_positive": None}

    ic = frame["ic"].to_numpy(float)

    sd = float(ic.std(ddof=1)) if len(ic) > 1 else 0.0

    return {
        "label": label,
        "dates": int(len(ic)),
        "mean_ic": round(float(ic.mean()), 5),
        "median_ic": round(float(np.median(ic)), 5),
        "icir": round(float(ic.mean() / sd), 4) if sd > 0 else None,
        "t_stat": (round(float(ic.mean() / sd * math.sqrt(len(ic))), 3)
                   if sd > 0 else None),
        "pct_positive": round(float((ic > 0).mean() * 100), 2),
    }


def hit_rate(panel, horizon=HORIZON):
    """Share of decision dates on which the top decile beat the cross-section."""

    column = "fwd_%d" % horizon

    wins, total = 0, 0

    for _, block in panel.groupby("date", sort=True):
        good = block[["alpha", column]].dropna()
        if len(good) < MIN_NAMES:
            continue
        k = max(1, int(round(len(good) / 10.0)))
        order = np.argsort(-good["alpha"].to_numpy(float), kind="stable")
        values = good[column].to_numpy(float)
        total += 1
        wins += int(values[order[:k]].mean() > values.mean())

    return {"dates": total,
            "hit_rate_pct": round(100.0 * wins / total, 2) if total else None}


# ==============================================================
# PHASE 2A -- PREDICTION
# ==============================================================

def bucket_ic(panel, column, labels, horizon=HORIZON):
    """IC computed WITHIN each bucket, cross-sectionally, per date.

    Within, not across. Splitting the panel and correlating the pooled result
    would measure whether the buckets differ from each other, which is a
    different question and a much easier one to pass.
    """

    out = {}

    for name in labels:
        subset = panel[panel[column] == name]
        frame = ic_series(subset, horizon=horizon,
                          min_names=max(20, MIN_NAMES // 2))
        out[str(name)] = summarise_ic(frame, label=str(name))

    return out


def add_buckets(panel):
    """Liquidity and volatility quintiles, assigned WITHIN each date.

    Cross-date quintiles would mix a 2014 illiquid name with a 2024 liquid one
    and label them the same, because this market's nominal turnover grew by
    orders of magnitude over the window.
    """

    frame = panel.copy()

    frame["adv_bucket"] = frame.groupby("date")["adv"].transform(
        lambda s: pd.qcut(s.rank(method="first"), 5,
                          labels=["Q1_illiquid", "Q2", "Q3", "Q4",
                                  "Q5_liquid"])
        if s.notna().sum() >= 25 else pd.Series(["?"] * len(s), index=s.index))

    frame["vol_bucket"] = frame.groupby("date")["volatility_60"].transform(
        lambda s: pd.qcut(s.rank(method="first"), 5,
                          labels=["V1_calm", "V2", "V3", "V4", "V5_wild"])
        if s.notna().sum() >= 25 else pd.Series(["?"] * len(s), index=s.index))

    return frame


def index_regimes(dates):
    """Per-date regime, using regime.py's OWN definitions.

    BULL   close above the slow MA and fast above slow
    BEAR   below both
    HIGH_VOLATILITY  annualised 60-session volatility >= 30%

    These constants are in `regime.py` and predate this audit by the entire
    programme. Inventing a regime split now, after seeing where the strategy
    made money, is the classic way to manufacture a story.
    """

    from backtesting.pit import PointInTimeData

    try:
        data = PointInTimeData(verbose=False)
        frame = data.index_series(backtest_config.BENCHMARK_TEDPIX)
    except Exception as exc:                                   # noqa: BLE001
        say("    index unavailable (%s); regime analysis skipped" % exc)
        return {}

    if frame is None or frame.empty:
        return {}

    frame = frame.sort_values("date").reset_index(drop=True)

    closes = frame["close"].astype(float)

    fast = closes.rolling(DEFAULT_PARAMS["ma_fast"]).mean()
    slow = closes.rolling(DEFAULT_PARAMS["ma_slow"]).mean()

    returns = closes.pct_change()

    vol = (returns.rolling(60).std()
           * math.sqrt(SESSIONS_PER_YEAR) * 100.0)

    stamps = pd.DatetimeIndex(frame["date"])

    out = {}

    for date in dates:
        cut = int(stamps.searchsorted(pd.Timestamp(date), side="right")) - 1
        if cut < DEFAULT_PARAMS["ma_slow"]:
            continue

        close_v, fast_v, slow_v = closes.iloc[cut], fast.iloc[cut], slow.iloc[cut]

        if not np.isfinite(fast_v) or not np.isfinite(slow_v):
            continue

        if close_v > slow_v and fast_v > slow_v:
            trend = regime_module.BULL
        elif close_v < slow_v and fast_v < slow_v:
            trend = regime_module.BEAR
        else:
            trend = regime_module.SIDEWAYS

        vol_v = vol.iloc[cut]

        if not np.isfinite(vol_v):
            volatility = "UNKNOWN"
        elif vol_v >= regime_module.VOLATILITY_HIGH:
            volatility = regime_module.HIGH_VOLATILITY
        elif vol_v <= regime_module.VOLATILITY_LOW:
            volatility = regime_module.LOW_VOLATILITY
        else:
            volatility = regime_module.NORMAL_VOLATILITY

        out[pd.Timestamp(date)] = {"trend": trend, "volatility": volatility,
                                   "index_vol_pct": round(float(vol_v), 2)
                                   if np.isfinite(vol_v) else None}

    return out


def phase2_prediction(panel, regimes):
    rule("PHASE 2A -- PREDICTION (no portfolio, no costs, no execution)")

    result = {}

    # ---- the declared horizon ------------------------------------------
    primary = ic_series(panel, horizon=HORIZON)

    result["declared_horizon"] = HORIZON

    result["primary"] = summarise_ic(primary, "h=%d (declared)" % HORIZON)

    result["hit_rate"] = hit_rate(panel, HORIZON)

    say("  DECLARED HORIZON h=%d" % HORIZON)
    for key in ("dates", "mean_ic", "icir", "t_stat", "pct_positive"):
        say("    %-16s %s" % (key, result["primary"][key]))
    say("    %-16s %s" % ("hit rate %",
                          result["hit_rate"]["hit_rate_pct"]))

    # ---- the decay curve, reported only --------------------------------
    result["decay_curve"] = {
        str(h): summarise_ic(ic_series(panel, horizon=h), "h=%d" % h)
        for h in DECAY_HORIZONS
    }

    say()
    say("  DECAY CURVE (reported only -- cannot pass gate 3)")
    say("    %-6s %-10s %-8s %-8s %s" % ("h", "mean IC", "ICIR", "t", "%pos"))
    for h in DECAY_HORIZONS:
        entry = result["decay_curve"][str(h)]
        say("    %-6s %-10s %-8s %-8s %s"
            % (h, entry["mean_ic"], entry["icir"], entry["t_stat"],
               entry["pct_positive"]))

    peak = max(e["mean_ic"] for e in result["decay_curve"].values()
               if e["mean_ic"] is not None)

    retention = (result["primary"]["mean_ic"] / peak) if peak else None

    result["peak_ic"] = peak
    result["retention_at_declared_horizon"] = (
        round(retention, 4) if retention is not None else None)

    say()
    say("    peak IC %.5f at h=1; retained at h=%d: %.1f%%"
        % (peak, HORIZON, (retention or 0) * 100))

    # ---- stability over time -------------------------------------------
    primary["year"] = primary["date"].dt.year

    result["by_year"] = {
        str(year): summarise_ic(block, str(year))
        for year, block in primary.groupby("year")
    }

    say()
    say("  IC STABILITY BY YEAR (h=%d)" % HORIZON)
    for year in sorted(result["by_year"]):
        entry = result["by_year"][year]
        say("    %-6s dates %-4s mean IC %-10s %%pos %s"
            % (year, entry["dates"], entry["mean_ic"], entry["pct_positive"]))

    # ---- by sector, liquidity, volatility ------------------------------
    bucketed = add_buckets(panel)

    sectors = (bucketed["sector"].value_counts().head(10).index.tolist()
               if "sector" in bucketed.columns else [])

    result["by_sector"] = bucket_ic(bucketed, "sector", sectors)

    result["by_liquidity"] = bucket_ic(
        bucketed, "adv_bucket",
        ["Q1_illiquid", "Q2", "Q3", "Q4", "Q5_liquid"])

    result["by_volatility"] = bucket_ic(
        bucketed, "vol_bucket",
        ["V1_calm", "V2", "V3", "V4", "V5_wild"])

    say()
    say("  IC BY LIQUIDITY QUINTILE (within date)")
    for name in ["Q1_illiquid", "Q2", "Q3", "Q4", "Q5_liquid"]:
        entry = result["by_liquidity"][name]
        say("    %-14s dates %-4s mean IC %-10s t %s"
            % (name, entry["dates"], entry["mean_ic"], entry["t_stat"]))

    say()
    say("  IC BY VOLATILITY QUINTILE (within date)")
    for name in ["V1_calm", "V2", "V3", "V4", "V5_wild"]:
        entry = result["by_volatility"][name]
        say("    %-14s dates %-4s mean IC %-10s t %s"
            % (name, entry["dates"], entry["mean_ic"], entry["t_stat"]))

    say()
    say("  IC BY SECTOR (10 largest)")
    for name in sectors[:10]:
        entry = result["by_sector"][str(name)]
        say("    %-34s dates %-4s mean IC %s"
            % (str(name)[:34], entry["dates"], entry["mean_ic"]))

    # ---- by regime -------------------------------------------------------
    if regimes:
        labelled = panel.copy()
        labelled["trend"] = labelled["date"].map(
            lambda d: (regimes.get(pd.Timestamp(d)) or {}).get("trend"))
        labelled["vol_regime"] = labelled["date"].map(
            lambda d: (regimes.get(pd.Timestamp(d)) or {}).get("volatility"))

        result["by_trend_regime"] = bucket_ic(
            labelled, "trend",
            [regime_module.BULL, regime_module.BEAR, regime_module.SIDEWAYS])

        result["by_volatility_regime"] = bucket_ic(
            labelled, "vol_regime",
            [regime_module.HIGH_VOLATILITY, regime_module.NORMAL_VOLATILITY,
             regime_module.LOW_VOLATILITY])

        say()
        say("  IC BY MARKET REGIME (regime.py's own definitions)")
        for name in (regime_module.BULL, regime_module.BEAR,
                     regime_module.SIDEWAYS):
            entry = result["by_trend_regime"][name]
            say("    %-14s dates %-4s mean IC %-10s t %s"
                % (name, entry["dates"], entry["mean_ic"], entry["t_stat"]))

    return result


# ==============================================================
# PHASE 2B/2C -- CONSTRUCTION AND EXECUTION
# ==============================================================

def cash_per_period(horizon):
    return ((1.0 + config.RISK_FREE_RATE)
            ** (horizon / float(SESSIONS_PER_YEAR)) - 1.0) * 100.0


def build_book(panel, horizon=HORIZON, basket=BASKET, ranker=None,
               execute=True):
    """One decision date at a time: rank, select, request, fill.

    `execute=False` gives the FRICTIONLESS book -- what the selection would
    have earned if every order filled at the decision price. `execute=True`
    leaves an unbuyable slot in cash rather than refilling it, which is the one
    rule that separates a request from a result.
    """

    column = "fwd_%d" % horizon

    cash = cash_per_period(horizon)

    rows = []
    held = set()

    for date, block in panel.groupby("date", sort=True):

        pool = block.dropna(subset=["alpha", column])

        if len(pool) < MIN_NAMES:
            continue

        picks = (pool.sort_values("alpha", ascending=False).head(basket)
                 if ranker is None else ranker(pool, date))

        if picks is None or len(picks) < basket:
            continue

        market = float(pool[column].mean())

        unbuyable = picks["next_unbuyable"].astype(bool).to_numpy()

        if execute:
            filled = picks[~unbuyable]
            realised = float((filled[column].sum()
                              + unbuyable.sum() * cash) / len(picks))
        else:
            realised = float(picks[column].mean())

        names = set(picks["ins_code"])
        turnover = 1.0 if not held else len(names - held) / float(len(names))
        held = names

        rows.append({
            "date": date,
            "market_pct": market,
            "requested_pct": float(picks[column].mean()),
            "realised_pct": realised,
            "fill_rate": float((~unbuyable).mean()),
            "turnover": turnover,
            "basket_lock_rate": float(
                picks["next_locked_up"].astype(bool).mean()),
            "universe_lock_rate": float(
                pool["next_locked_up"].astype(bool).mean()),
        })

    return pd.DataFrame(rows)


def summarise_book(book, horizon=HORIZON, round_trip=ROUND_TRIP,
                   slippage=0.0, label=""):
    """Gross, cost, net -- and the drawdown of the resulting equity curve."""

    if book is None or book.empty:
        return {"label": label, "periods": 0}

    excess = book["realised_pct"] - book["market_pct"]

    turnover = float(book["turnover"].mean())

    cost = turnover * round_trip

    slip = turnover * 2.0 * slippage

    net = float(excess.mean()) - cost - slip

    periods_per_year = SESSIONS_PER_YEAR / float(horizon)

    sd = float(excess.std(ddof=1)) if len(excess) > 1 else 0.0

    # Absolute equity curve, compounded across non-overlapping periods.
    absolute = (book["realised_pct"] - cost - slip) / 100.0
    equity = (1.0 + absolute).cumprod()
    peak = equity.cummax()
    drawdown = (equity / peak - 1.0) * 100.0

    return {
        "label": label,
        "periods": int(len(book)),
        "gross_excess_pct": round(float(excess.mean()), 4),
        "excess_t": (round(float(excess.mean() / (sd / math.sqrt(len(excess)))),
                           3) if sd > 0 and len(excess) > 1 else None),
        "turnover_per_period": round(turnover, 4),
        "turnover_per_year": round(turnover * periods_per_year, 2),
        "cost_per_period_pct": round(cost, 4),
        "slippage_per_period_pct": round(slip, 4),
        "net_per_period_pct": round(net, 4),
        "net_annualised_pct": round(
            ((1 + net / 100.0) ** periods_per_year - 1) * 100, 2),
        "fill_rate_pct": round(float(book["fill_rate"].mean()) * 100, 2),
        "queue_ratio": (round(float(book["basket_lock_rate"].mean()
                                    / book["universe_lock_rate"].mean()), 3)
                        if book["universe_lock_rate"].mean() else None),
        "requested_excess_pct": round(
            float((book["requested_pct"] - book["market_pct"]).mean()), 4),
        "max_drawdown_pct": round(float(drawdown.min()), 2),
        "final_equity": round(float(equity.iloc[-1]), 4),
        "net_positive": bool(net > 0),
    }


def phase5_execution(panel):
    rule("PHASE 5 -- EXECUTION REALISM AND RECONCILIATION")

    frictionless = build_book(panel, execute=False)
    executed = build_book(panel, execute=True)

    fr = summarise_book(frictionless, label="frictionless")
    ex = summarise_book(executed, label="executed")
    ex_slip = summarise_book(executed, slippage=0.10, label="executed+slippage")

    result = {"frictionless": fr, "executed": ex, "executed_slippage": ex_slip}

    say("  RECONCILIATION -- signal to realised, at h=%d, basket %d"
        % (HORIZON, BASKET))
    say()
    say("    %-38s %10s" % ("stage", "excess %"))
    say("    " + "-" * 50)
    say("    %-38s %10.4f" % ("1. signal portfolio (top-10 requested)",
                              ex["requested_excess_pct"]))
    say("    %-38s %10.4f" % ("2. executable after T+1 queues",
                              ex["gross_excess_pct"]))
    say("    %-38s %10.4f" % ("3. less turnover x round trip",
                              ex["gross_excess_pct"]
                              - ex["cost_per_period_pct"]))
    say("    %-38s %10.4f" % ("4. less slippage (0.10%/leg)",
                              ex_slip["net_per_period_pct"]))
    say()
    say("    fill rate            %.2f%%" % ex["fill_rate_pct"])
    say("    queue ratio          %s" % ex["queue_ratio"])
    say("    turnover / year      %.2f" % ex["turnover_per_year"])
    say("    cost / period        %.4f%%" % ex["cost_per_period_pct"])
    say("    NET / period         %.4f%%" % ex["net_per_period_pct"])
    say("    net annualised       %.2f%%" % ex["net_annualised_pct"])
    say("    max drawdown         %.2f%%" % ex["max_drawdown_pct"])

    shortfall = ex["requested_excess_pct"] - ex["gross_excess_pct"]

    result["execution_shortfall_pct"] = round(shortfall, 4)
    result["shortfall_share"] = (round(shortfall / ex["requested_excess_pct"], 4)
                                 if ex["requested_excess_pct"] else None)

    say()
    say("    execution shortfall  %.4f pp (%.1f%% of the request)"
        % (shortfall, 100.0 * (result["shortfall_share"] or 0)))

    # Where does the edge die -- prediction, construction, or execution?
    say()
    say("  WHERE THE EDGE DIES")
    say("    frictionless gross   %+.4f%%" % fr["gross_excess_pct"])
    say("    after execution      %+.4f%%" % ex["gross_excess_pct"])
    say("    after cost           %+.4f%%" % ex["net_per_period_pct"])
    say("    after slippage       %+.4f%%" % ex_slip["net_per_period_pct"])

    result["attribution"] = {
        "prediction_gross_pct": fr["gross_excess_pct"],
        "lost_to_execution_pp": round(fr["gross_excess_pct"]
                                      - ex["gross_excess_pct"], 4),
        "lost_to_cost_pp": round(ex["cost_per_period_pct"], 4),
        "lost_to_slippage_pp": round(ex_slip["slippage_per_period_pct"], 4),
        "net_pct": ex_slip["net_per_period_pct"],
    }

    return result


# ==============================================================
# PHASE 3 -- VALIDATION
# ==============================================================

def phase3_validation(panel):
    rule("PHASE 3 -- WALK-FORWARD, OUT-OF-SAMPLE, MONTE CARLO")

    result = {}

    # ---- walk-forward ---------------------------------------------------
    frame = ic_series(panel, horizon=HORIZON).sort_values("date")
    frame = frame.reset_index(drop=True)

    train, test = 20, 10

    folds = []
    start = 0

    while start + train + test <= len(frame):
        train_block = frame.iloc[start:start + train]
        test_block = frame.iloc[start + train:start + train + test]

        dates = set(test_block["date"])
        book = build_book(panel[panel["date"].isin(dates)], execute=True)
        stats = summarise_book(book, label="fold")

        folds.append({
            "fold": len(folds) + 1,
            "train_start": str(train_block["date"].iloc[0].date()),
            "train_end": str(train_block["date"].iloc[-1].date()),
            "test_start": str(test_block["date"].iloc[0].date()),
            "test_end": str(test_block["date"].iloc[-1].date()),
            "train_ic": round(float(train_block["ic"].mean()), 5),
            "test_ic": round(float(test_block["ic"].mean()), 5),
            "test_net_pct": stats.get("net_per_period_pct"),
            "test_turnover_per_year": stats.get("turnover_per_year"),
            "test_max_drawdown_pct": stats.get("max_drawdown_pct"),
        })

        start += test

    positive = [f for f in folds if f["test_ic"] > 0]

    result["walk_forward"] = {
        "train_dates": train, "test_dates": test,
        "folds": len(folds),
        "positive_folds": len(positive),
        "positive_fold_share": (round(len(positive) / float(len(folds)), 4)
                                if folds else None),
        "mean_train_ic": (round(float(np.mean([f["train_ic"] for f in folds])),
                                5) if folds else None),
        "mean_test_ic": (round(float(np.mean([f["test_ic"] for f in folds])), 5)
                         if folds else None),
        "detail": folds,
    }

    say("  WALK-FORWARD -- %d sequential folds, train %d / test %d dates"
        % (len(folds), train, test))
    say("    %-4s %-12s %-12s %-10s %-10s %s"
        % ("#", "test start", "test end", "train IC", "test IC", "net %"))
    for fold in folds:
        say("    %-4d %-12s %-12s %-10s %-10s %s"
            % (fold["fold"], fold["test_start"], fold["test_end"],
               fold["train_ic"], fold["test_ic"], fold["test_net_pct"]))
    say()
    say("    positive out-of-fold: %d of %d (%.0f%%)"
        % (len(positive), len(folds),
           100.0 * len(positive) / len(folds) if folds else 0))

    # ---- out of sample --------------------------------------------------
    segments = {}

    for name, (start_date, end_date) in (("in_sample", IN_SAMPLE),
                                         ("validation", VALIDATION),
                                         ("holdout", HOLDOUT)):
        block = panel[(panel["date"] >= start_date) & (panel["date"] < end_date)]
        ics = ic_series(block, horizon=HORIZON)
        book = build_book(block, execute=True)
        segments[name] = {
            "window": [start_date, end_date],
            "ic": summarise_ic(ics, name),
            "book": summarise_book(book, label=name),
        }

    in_ic = segments["in_sample"]["ic"]["mean_ic"]
    out_ic = segments["holdout"]["ic"]["mean_ic"]

    degradation = (None if not in_ic or in_ic <= 0
                   else round(1.0 - out_ic / in_ic, 4))

    result["out_of_sample"] = {"segments": segments,
                               "degradation": degradation}

    say()
    say("  OUT-OF-SAMPLE -- preregistered sequential split")
    say("    %-12s %-24s %-8s %-8s %-8s %s"
        % ("segment", "window", "dates", "IC", "t", "net %"))
    for name in ("in_sample", "validation", "holdout"):
        entry = segments[name]
        say("    %-12s %-24s %-8s %-8s %-8s %s"
            % (name, "%s..%s" % tuple(entry["window"]),
               entry["ic"]["dates"], entry["ic"]["mean_ic"],
               entry["ic"]["t_stat"], entry["book"].get("net_per_period_pct")))
    say()
    say("    degradation in-sample -> holdout: %s" % degradation)

    # ---- monte carlo ----------------------------------------------------
    executed = build_book(panel, execute=True)

    excess = (executed["realised_pct"] - executed["market_pct"]).to_numpy(float)

    rng = np.random.default_rng(SEED)

    # (a) block bootstrap of the realised excess series
    n = len(excess)
    block_size = 5
    draws = 5000

    means = []
    for _ in range(draws):
        sample = []
        while len(sample) < n:
            start = int(rng.integers(0, n))
            sample.extend(excess[start:start + block_size])
        means.append(float(np.mean(sample[:n])))

    means = np.asarray(means, float)

    # (b) timing perturbation: shift the decision date by one slot
    shifted = build_book(panel, execute=True)
    shifted_excess = (shifted["realised_pct"].shift(1)
                      - shifted["market_pct"]).dropna()

    result["monte_carlo"] = {
        "method": "stationary block bootstrap on realised excess, "
                  "block=%d, draws=%d, seed=%d" % (block_size, draws, SEED),
        "observed_mean_excess_pct": round(float(excess.mean()), 5),
        "ci95": [round(float(np.percentile(means, 2.5)), 5),
                 round(float(np.percentile(means, 97.5)), 5)],
        "p_le_zero": round(float((means <= 0).mean()), 4),
        "timing_shift_mean_excess_pct": round(float(shifted_excess.mean()), 5),
    }

    say()
    say("  MONTE CARLO -- block bootstrap on realised excess")
    say("    observed mean excess  %+.5f%%"
        % result["monte_carlo"]["observed_mean_excess_pct"])
    say("    95%% CI                [%+.5f, %+.5f]"
        % tuple(result["monte_carlo"]["ci95"]))
    say("    P(mean <= 0)          %.4f" % result["monte_carlo"]["p_le_zero"])

    return result


# ==============================================================
# PHASE 4 -- NULL AND PLACEBO TESTS
# ==============================================================

def matched_ranker(kind, rng, column=None):
    """A null that preserves the structure the strategy is constrained by.

    Comparing a sector-capped, liquidity-filtered strategy against an
    unconstrained random pick measures the constraints, not the signal. Each
    null here draws from the same strata the alpha basket occupied on that date.
    """

    def rank_random(pool, date):
        return pool.iloc[rng.permutation(len(pool))].head(BASKET)

    def rank_matched(pool, date):
        alpha_picks = pool.sort_values("alpha", ascending=False).head(BASKET)

        if column not in pool.columns:
            return rank_random(pool, date)

        chosen = []
        used = set()

        for stratum in alpha_picks[column]:
            candidates = pool[(pool[column] == stratum)
                              & (~pool.index.isin(used))]
            if candidates.empty:
                candidates = pool[~pool.index.isin(used)]
            if candidates.empty:
                break
            pick = candidates.iloc[int(rng.integers(0, len(candidates)))]
            used.add(pick.name)
            chosen.append(pick)

        return pd.DataFrame(chosen) if chosen else None

    return rank_random if kind == "random" else rank_matched


def phase4_nulls(panel):
    rule("PHASE 4 -- NULL AND PLACEBO TESTS")

    bucketed = add_buckets(panel)

    treatment_book = build_book(bucketed, execute=True)
    treatment = summarise_book(treatment_book, label="alpha core")

    treatment_excess = float((treatment_book["realised_pct"]
                              - treatment_book["market_pct"]).mean())

    nulls = {}

    definitions = [
        ("random_ranking", "random", None),
        ("sector_matched", "matched", "sector"),
        ("liquidity_matched", "matched", "adv_bucket"),
        ("volatility_matched", "matched", "vol_bucket"),
    ]

    for name, kind, column in definitions:

        arms = []

        for arm in range(NULL_ARMS):
            rng = np.random.default_rng(SEED + arm)
            ranker = matched_ranker(kind, rng, column)
            book = build_book(bucketed, ranker=ranker, execute=True)
            if book.empty:
                continue
            arms.append(float((book["realised_pct"]
                               - book["market_pct"]).mean()))

        arms = np.asarray(arms, float)

        percentile = float((arms < treatment_excess).mean() * 100.0)

        nulls[name] = {
            "arms": int(len(arms)),
            "null_mean_excess_pct": round(float(arms.mean()), 5),
            "null_sd": round(float(arms.std(ddof=1)), 5),
            "null_p95": round(float(np.percentile(arms, 95)), 5),
            "treatment_excess_pct": round(treatment_excess, 5),
            "percentile": round(percentile, 2),
            "empirical_p": round(float((arms >= treatment_excess).mean()), 4),
            "beats_p95": bool(treatment_excess > np.percentile(arms, 95)),
        }

    # The market itself: equal-weight universe return is the benchmark, so its
    # excess is zero by construction. Recorded so the comparison is explicit
    # rather than implied.
    nulls["equal_weight_market"] = {
        "arms": 1, "null_mean_excess_pct": 0.0,
        "treatment_excess_pct": round(treatment_excess, 5),
        "percentile": 100.0 if treatment_excess > 0 else 0.0,
        "beats_p95": bool(treatment_excess > 0),
        "note": "the benchmark is the cross-sectional mean, so its excess is "
                "zero by construction",
    }

    say("  Treatment (Alpha Core, executed): %+.5f%% mean excess per period"
        % treatment_excess)
    say()
    say("  %-22s %-6s %-12s %-12s %-10s %s"
        % ("null model", "arms", "null mean", "null p95", "percentile",
           "beats p95"))
    say("  " + "-" * 84)

    for name, entry in nulls.items():
        say("  %-22s %-6s %-12s %-12s %-10s %s"
            % (name, entry["arms"], entry["null_mean_excess_pct"],
               entry.get("null_p95", "-"), entry["percentile"],
               entry["beats_p95"]))

    worst = min(e["percentile"] for e in nulls.values())

    say()
    say("  Worst percentile against any matched null: %.2f" % worst)
    say("  Gate 6 requires >= 95 against EVERY null, not merely the easiest.")

    return {"treatment": treatment, "nulls": nulls,
            "worst_percentile": round(worst, 2)}


# ==============================================================
# PHASE 7 -- HOLDING PERIOD DECOMPOSITION
# ==============================================================

def phase7_holding(panel):
    rule("PHASE 7 -- HOLDING PERIOD AND SOURCE DECOMPOSITION")

    result = {"candidates": {}}

    say("  Candidate holding rules were fixed a priori as the panel's four")
    say("  horizons. The DECLARED rule is h=%d; the others are measured so the"
        % HORIZON)
    say("  decomposition is visible, and NONE of them may be selected now.")
    say()
    say("  %-8s %-12s %-12s %-12s %-12s %s"
        % ("h", "gross %", "turnover/y", "cost/period", "net/period",
           "net ann. %"))
    say("  " + "-" * 78)

    for horizon in DECAY_HORIZONS:
        book = build_book(panel, horizon=horizon, execute=True)
        stats = summarise_book(book, horizon=horizon, label="h=%d" % horizon)
        result["candidates"][str(horizon)] = stats
        say("  %-8s %-12s %-12s %-12s %-12s %s"
            % (horizon, stats["gross_excess_pct"],
               stats["turnover_per_year"], stats["cost_per_period_pct"],
               stats["net_per_period_pct"], stats["net_annualised_pct"]))

    # ---- where does the result come from --------------------------------
    frictionless = summarise_book(build_book(panel, execute=False),
                                  label="frictionless")
    executed = summarise_book(build_book(panel, execute=True),
                              label="executed")

    result["decomposition"] = {
        "stock_selection_gross_pct": frictionless["gross_excess_pct"],
        "execution_effect_pp": round(executed["gross_excess_pct"]
                                     - frictionless["gross_excess_pct"], 4),
        "cost_effect_pp": round(-executed["cost_per_period_pct"], 4),
        "net_pct": executed["net_per_period_pct"],
        "market_exposure_effect": "not separable on a panel -- the benchmark "
                                  "IS the cross-sectional mean, so market "
                                  "direction is differenced out by "
                                  "construction",
    }

    say()
    say("  SOURCE DECOMPOSITION at the declared horizon")
    for key, value in result["decomposition"].items():
        say("    %-30s %s" % (key, value))

    say()
    say("  NOTE: the declared rule is h=%d and it stays h=%d. The table above"
        % (HORIZON, HORIZON))
    say("  exists to show the shape of the decay, not to choose from.")

    return result


# ==============================================================
# PHASE 9 -- ROBUSTNESS
# ==============================================================

def phase9_robustness(panel):
    rule("PHASE 9 -- ROBUSTNESS / FRAGILITY")

    result = {"cost_stress": {}, "basket_stress": {}, "timing_stress": {}}

    say("  COST AND SLIPPAGE PERTURBATION (declared h=%d)" % HORIZON)
    say("  %-28s %-14s %s" % ("scenario", "net/period %", "positive"))
    say("  " + "-" * 56)

    book = build_book(panel, execute=True)

    for label, multiplier, slippage in (
            ("baseline", 1.00, 0.00),
            ("cost x1.25 (preregistered)", 1.25, 0.00),
            ("cost x1.50", 1.50, 0.00),
            ("slippage 0.10%/leg", 1.00, 0.10),
            ("cost x1.25 + slippage", 1.25, 0.10),
            ("cost x0.75 (favourable)", 0.75, 0.00)):
        stats = summarise_book(book, round_trip=ROUND_TRIP * multiplier,
                               slippage=slippage, label=label)
        result["cost_stress"][label] = stats
        say("  %-28s %-14s %s" % (label, stats["net_per_period_pct"],
                                  stats["net_positive"]))

    say()
    say("  BASKET SIZE (structural, not a parameter to tune)")
    say("  %-12s %-14s %-14s %s" % ("basket", "gross %", "net/period %",
                                    "positive"))
    say("  " + "-" * 56)

    for size in (5, 10, 20, 47):
        sized = build_book(panel, basket=size, execute=True)
        stats = summarise_book(sized, label="basket=%d" % size)
        result["basket_stress"][str(size)] = stats
        say("  %-12s %-14s %-14s %s"
            % (size, stats["gross_excess_pct"], stats["net_per_period_pct"],
               stats["net_positive"]))

    positives = [s["net_positive"] for s in result["cost_stress"].values()]

    if all(positives):
        verdict = "ROBUST"
    elif any(positives):
        verdict = "HIGHLY FRAGILE"
    else:
        verdict = "UNIFORMLY NEGATIVE"

    result["verdict"] = verdict

    say()
    say("  Fragility verdict: %s" % verdict)

    if verdict == "UNIFORMLY NEGATIVE":
        say("  Every perturbation, including the FAVOURABLE one, leaves the")
        say("  net edge negative. There is no fragility question to answer --")
        say("  the result is not near a boundary, it is on the wrong side of "
            "it.")

    return result


# ==============================================================
# PHASE 10 -- REGIME
# ==============================================================

def phase10_regime(panel, regimes):
    rule("PHASE 10 -- REGIME DEPENDENCE")

    if not regimes:
        say("  the benchmark index is unavailable; regime analysis is "
            "INCONCLUSIVE")
        return {"available": False}

    labelled = panel.copy()
    labelled["trend"] = labelled["date"].map(
        lambda d: (regimes.get(pd.Timestamp(d)) or {}).get("trend"))

    out = {"available": True, "by_trend": {}}

    say("  %-12s %-8s %-12s %-14s %s"
        % ("regime", "dates", "gross %", "net/period %", "positive"))
    say("  " + "-" * 60)

    for name in (regime_module.BULL, regime_module.BEAR,
                 regime_module.SIDEWAYS):
        block = labelled[labelled["trend"] == name]
        if block.empty:
            continue
        book = build_book(block, execute=True)
        stats = summarise_book(book, label=name)
        out["by_trend"][name] = stats
        say("  %-12s %-8s %-12s %-14s %s"
            % (name, stats.get("periods"), stats.get("gross_excess_pct"),
               stats.get("net_per_period_pct"), stats.get("net_positive")))

    positive_regimes = [n for n, s in out["by_trend"].items()
                        if s.get("net_positive")]

    out["positive_regimes"] = positive_regimes

    out["single_regime_dependent"] = bool(len(positive_regimes) == 1)

    say()
    say("  regimes with a positive net edge: %s"
        % (", ".join(positive_regimes) or "none"))

    return out


# ==============================================================

def main():
    say("=" * 78)
    say("E19 -- ALPHA CORE TRADABLE EDGE AUDIT")
    say("=" * 78)
    say("  preregistration  backtest_output/E19_preregistration.json")
    say("  declared horizon h=%d (NOT selected after the fact)" % HORIZON)
    say("  seed             %d" % SEED)
    say()

    panel = build_audit_panel()

    digest = panel_hash(panel)

    say("  audit panel hash %s" % digest[:32])

    regimes = index_regimes(sorted(panel["date"].unique()))

    say("  regimes labelled %d dates" % len(regimes))

    report = {
        "experiment_id": "E19-alpha-core-tradable-edge",
        "preregistration_hash":
            "9fd80d297b6ec43632e742a9c529d84e810585de39cc5ec140889aa35665475f",
        "audit_panel_sha256": digest,
        "declared_horizon": HORIZON,
        "seed": SEED,
        "rows": int(len(panel)),
        "dates": int(panel["date"].nunique()),
        "instruments": int(panel["ins_code"].nunique()),
    }

    report["phase2_prediction"] = phase2_prediction(panel, regimes)
    report["phase5_execution"] = phase5_execution(panel)
    report["phase3_validation"] = phase3_validation(panel)
    report["phase4_nulls"] = phase4_nulls(panel)
    report["phase7_holding"] = phase7_holding(panel)
    report["phase9_robustness"] = phase9_robustness(panel)
    report["phase10_regime"] = phase10_regime(panel, regimes)

    with open(OUT, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, default=str)

    rule("AUDIT COMPLETE")
    say("  written to %s" % os.path.relpath(OUT, ROOT))

    return 0


if __name__ == "__main__":
    sys.exit(main())
