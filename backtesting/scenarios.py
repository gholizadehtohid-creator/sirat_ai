"""The V2 comparisons: evidence sets, execution costs, and sizing rules.

WHAT A "SCENARIO" IS HERE
-------------------------
A whole backtest, run over the same window on the same panel, with exactly one
thing changed. Not a sweep line, not a sensitivity number -- a full result with
its own equity curve, trades, drawdown and benchmark comparison, so that the
three questions the specification asks can be answered with the same evidence a
headline result is judged on.

    MODELS       what does each evidence set contribute
                 A: technical  B: + fundamental  C: + news
    EXECUTION    what does being wrong about slippage cost
                 0%, 0.25%, 0.50% per side
    SIZING       does the sizing model earn its complexity
                 risk-based, equal weight, fixed fractional, volatility-adjusted

ONE PANEL PER EVIDENCE SET, SHARED BY EVERYTHING ELSE
-----------------------------------------------------
The execution and sizing scenarios never touch the signals, so they all read
ONE panel. Rebuilding it per scenario would cost twelve panel builds and, worse,
would let two scenarios disagree about what a score was on a shared date -- a
comparison whose arms disagree about their own inputs is measuring its cache.

The model scenarios do need their own panels, because a fundamental score is
not computed at all in the technical variant. That is three panel builds and it
is unavoidable.

WHAT IS NOT DONE HERE, DELIBERATELY
-----------------------------------
Nothing in this module picks a winner and feeds it back into the configuration.
Every scenario is reported and the defaults stay where they were. Choosing the
best-performing slippage assumption would be choosing to believe the cheapest
one; choosing the best-performing sizing mode on the same data that measured it
is the definition of the overfitting this package exists to detect.
"""

import backtest_config
from backtesting import engine, metrics as metrics_module, validation
from backtesting import sizing as sizing_module
from backtesting.strategy import (
    TECHNICAL_FUNDAMENTAL,
    TECHNICAL_FUNDAMENTAL_NEWS,
    TECHNICAL_ONLY,
)


# The three models, named as the specification names them so the report and the
# request use one vocabulary.
MODELS = (
    ("A", "Technical only", TECHNICAL_ONLY),
    ("B", "Technical + Fundamental", TECHNICAL_FUNDAMENTAL),
    ("C", "Technical + Fundamental + News", TECHNICAL_FUNDAMENTAL_NEWS),
)


def _row(label, result, extra=None):
    """One comparison row, with every metric the specification lists.

    A result that could not run contributes a row saying so rather than being
    dropped. A missing row reads as an oversight; a row saying INSUFFICIENT
    DATA reads as the finding it is.
    """

    row = {"label": label}

    row.update(extra or {})

    if result is None or not getattr(result, "available", False):
        row.update({
            "available": False,
            "reason": ("; ".join(result.insufficient)
                       if result is not None and result.insufficient
                       else "no result"),
        })
        return row

    m = result.metrics
    trades = m.get("trades") or {}
    drawdown = m.get("drawdown") or {}
    exposure = m.get("exposure") or {}
    benchmarks = result.benchmarks or {}

    tedpix = benchmarks.get("tedpix") or {}

    row.update({
        "available": True,
        "reason": None,
        "total_return_pct": m.get("total_return_pct"),
        "cagr_pct": m.get("cagr_pct"),
        "sharpe": m.get("sharpe"),
        "sortino": m.get("sortino"),
        "max_drawdown_pct": drawdown.get("max_drawdown_pct"),
        "calmar": m.get("calmar"),
        "profit_factor": trades.get("profit_factor"),
        "win_rate_pct": trades.get("win_rate_pct"),
        "trades": trades.get("trades"),
        "average_holding_days": trades.get("average_holding_days"),
        "average_cash_pct": exposure.get("average_cash_pct"),
        "average_positions": exposure.get("average_positions"),
        "benchmark_difference_pp": tedpix.get("excess_pct"),
        "final_capital": m.get("final_capital"),
    })

    return row


# ==============================
# MODELS A / B / C
# ==============================

def models(cfg, data, start, end, verbose=True, panels=None):
    """Run the three evidence sets and return a comparison table.

    Each keeps its own result object as well as its row, because the report
    needs the row and the failure analysis needs the diagnostics behind it.
    """

    out = {"rows": [], "results": {}, "panels": {}}

    for key, label, variant in MODELS:

        if verbose:
            print()
            print("-- model %s: %s --" % (key, label), flush=True)

        panel = dates = None

        if panels and variant in panels:
            panel, dates = panels[variant]
        else:
            # A variant refused before any panel is built stays refused: news
            # is unavailable on this database whatever the panel holds, and
            # scoring 818 symbols for two minutes to reach a conclusion already
            # known is waste rather than thoroughness.
            available, reason = data.news_available()

            if variant == TECHNICAL_FUNDAMENTAL_NEWS and not available:
                result = engine.run(start, end, cfg=cfg, variant=variant,
                                    data=data, verbose=False)
                out["results"][key] = result
                out["rows"].append(_row("%s — %s" % (key, label), result,
                                        {"model": key, "variant": variant}))
                if verbose:
                    print("   INSUFFICIENT DATA — NEWS: %s" % reason, flush=True)
                continue

            panel, dates = validation.master_panel(cfg, data, start, end,
                                                   variant, verbose=verbose)

        out["panels"][variant] = (panel, dates)

        result = engine.run(start, end, cfg=cfg, variant=variant, data=data,
                            panel=panel, rebalance_dates=dates, verbose=verbose)

        out["results"][key] = result

        out["rows"].append(_row("%s — %s" % (key, label), result,
                                {"model": key, "variant": variant}))

    return out


# ==============================
# EXECUTION: SLIPPAGE
# ==============================

def execution_scenarios(cfg, data, start, end, variant=TECHNICAL_ONLY,
                        panel=None, dates=None, values=None, verbose=True):
    """The same strategy at several slippage assumptions, reported in full.

    Slippage is the one cost in this project with NO evidence behind it: daily
    OHLCV cannot measure it, so backtest_config defaults it to zero -- which is
    a refusal to invent a number, not a claim that trading is free. These runs
    price being wrong about it.

    Zero is kept as one of the arms rather than removed. It is the reference
    the other two are read against, and dropping it would hide how much of the
    result is an artefact of the assumption rather than of the strategy.
    """

    values = values or backtest_config.SLIPPAGE_SCENARIOS

    if panel is None:
        panel, dates = validation.master_panel(cfg, data, start, end, variant,
                                               verbose=verbose)

    rows = []
    results = {}

    baseline = None

    for value in values:

        trial = cfg.replace(slippage_pct=value)

        if verbose:
            print("   slippage %.2f%% per side (round trip %.3f%%)"
                  % (value, trial.round_trip_pct()), flush=True)

        result = engine.run(start, end, cfg=trial, variant=variant, data=data,
                            panel=panel, rebalance_dates=dates, verbose=False)

        results[value] = result

        row = _row("slippage %.2f%%" % value, result, {
            "slippage_pct": value,
            "round_trip_pct": round(trial.round_trip_pct(), 3),
        })

        if row.get("available"):
            if baseline is None:
                baseline = row["total_return_pct"]
            row["return_lost_pp"] = (
                round(baseline - row["total_return_pct"], 2)
                if baseline is not None and row["total_return_pct"] is not None
                else None
            )

        rows.append(row)

    return {"rows": rows, "results": results, "values": list(values),
            "variant": variant}


# ==============================
# POSITION SIZING
# ==============================

def sizing_scenarios(cfg, data, start, end, variant=TECHNICAL_ONLY, panel=None,
                     dates=None, modes=None, verbose=True):
    """The four sizing rules on identical signals and identical risk rules.

    Everything except the number of shares is held constant -- same stop, same
    target, same caps, same fills -- so the table answers "does this sizing
    rule earn its complexity" rather than "are these four strategies
    different", which they would otherwise trivially be.
    """

    modes = modes or sizing_module.MODES

    if panel is None:
        panel, dates = validation.master_panel(cfg, data, start, end, variant,
                                               verbose=verbose)

    rows = []
    results = {}

    for mode in modes:

        if verbose:
            print("   sizing: %s" % mode, flush=True)

        trial = cfg.replace(sizing_mode=mode)

        result = engine.run(start, end, cfg=trial, variant=variant, data=data,
                            panel=panel, rebalance_dates=dates, verbose=False)

        results[mode] = result

        rows.append(_row(mode, result, {"sizing_mode": mode}))

    return {"rows": rows, "results": results, "modes": list(modes),
            "variant": variant}


# ==============================
# WHY THE RESULT IS WHAT IT IS
# ==============================

def failure_analysis(result, benchmarks=None):
    """What actually cost the money. Counts and rials, never adjectives.

    Ordered by size, so the largest line is the first thing read. This is the
    section a reader goes to after a FAIL verdict, and its job is to make the
    next decision obvious -- more data, a different rule, or abandonment.
    """

    if result is None or not result.available:
        return {"available": False,
                "reason": "; ".join(result.insufficient) if result else "no result"}

    m = result.metrics
    trades = m.get("trades") or {}
    diagnostics = result.diagnostics or {}
    benchmarks = benchmarks or result.benchmarks or {}

    findings = []

    # ---- 1. did it beat the alternatives that needed no work -------------
    for key, label in (("risk_free", "a bank deposit"),
                       ("tedpix", "شاخص کل"),
                       ("tedpix_equal_weight", "شاخص هم‌وزن"),
                       ("liquid_basket", "a liquid buy-and-hold basket")):

        entry = benchmarks.get(key) or {}

        if entry.get("excess_pct") is None:
            continue

        if entry["excess_pct"] < 0:
            findings.append({
                "finding": "lost to %s by %.2f points of total return"
                           % (label, abs(entry["excess_pct"])),
                "magnitude": abs(entry["excess_pct"]),
                "unit": "pp",
                "kind": "benchmark",
            })

    # ---- 2. cost of trading ----------------------------------------------
    costs = (diagnostics.get("commission_paid") or 0) + (
        diagnostics.get("tax_paid") or 0)

    initial = m.get("initial_capital") or 0

    if costs and initial:
        findings.append({
            "finding": "commission and tax consumed %.2f%% of the starting "
                       "capital across %d trades"
                       % (costs / initial * 100, trades.get("trades") or 0),
            "magnitude": round(costs / initial * 100, 2),
            "unit": "pp",
            "kind": "cost",
        })

    # ---- 3. capital that never worked ------------------------------------
    cash = m.get("exposure", {}).get("average_cash_pct")

    if cash:
        analysis = diagnostics.get("cash_analysis") or {}
        findings.append({
            "finding": "the book averaged %.1f%% cash; the largest single "
                       "cause was: %s" % (cash, analysis.get("largest_cause")
                                          or "not attributed"),
            "magnitude": cash,
            "unit": "%",
            "kind": "cash",
        })

    # ---- 4. what the market refused --------------------------------------
    blocked_buy = diagnostics.get("blocked_buy_queue") or 0
    blocked_sell = diagnostics.get("blocked_sell_queue") or 0
    expired = diagnostics.get("orders_expired") or 0

    if blocked_buy:
        findings.append({
            "finding": "%d buy orders were locked out by صف خرید -- the "
                       "strategy wanted names it could not get into"
                       % blocked_buy,
            "magnitude": blocked_buy, "unit": "orders", "kind": "execution",
        })

    if blocked_sell:
        findings.append({
            "finding": "%d exits were trapped in صف فروش -- a stop-loss that "
                       "cannot fill is not a stop-loss" % blocked_sell,
            "magnitude": blocked_sell, "unit": "orders", "kind": "execution",
        })

    if expired:
        findings.append({
            "finding": "%d orders expired unfilled" % expired,
            "magnitude": expired, "unit": "orders", "kind": "execution",
        })

    # ---- 5. the trades themselves ----------------------------------------
    if trades.get("available"):

        if (trades.get("payoff_ratio") or 0) < 1:
            findings.append({
                "finding": "the average winner (%s) was smaller than the "
                           "average loser (%s): payoff ratio %.2f"
                           % ("{:,.0f}".format(trades.get("average_win") or 0),
                              "{:,.0f}".format(abs(trades.get("average_loss") or 0)),
                              trades.get("payoff_ratio") or 0),
                "magnitude": 1 - (trades.get("payoff_ratio") or 0),
                "unit": "ratio", "kind": "trades",
            })

        reasons = trades.get("exit_reasons") or {}

        stops = reasons.get("stop", 0)

        if stops and trades.get("trades"):
            share = stops / float(trades["trades"]) * 100
            if share > 40:
                findings.append({
                    "finding": "%.0f%% of trades ended on the stop rather than "
                               "the target or a rotation" % share,
                    "magnitude": round(share, 1), "unit": "%", "kind": "trades",
                })

    findings.sort(key=lambda f: -(f["magnitude"] or 0))

    return {"available": True, "findings": findings}


def recommendation(verdict, overfit, result, coverage=None, news=None):
    """One of the four statuses the specification defines, with its reasons.

    The mapping is deliberately conservative and is stated in full so it can be
    argued with:

      FAIL                     the run produced a FAIL verdict on the criteria
      NEEDS MORE DEVELOPMENT   no FAIL, but a required input is missing --
                               overfitting UNKNOWN, coverage below the bar, or
                               a variant that could not be measured at all
      PAPER TRADING ONLY       passes the criteria but has not been observed
                               forward, or carries a MEDIUM overfitting risk
      READY FOR PAPER TRADING  passes, with LOW overfitting risk

    Note that the top two both end in paper trading. Nothing this package can
    compute justifies live money: every result it produces is measured on a
    survivors-only universe with an approximated queue model, and neither of
    those is fixed by a better Sharpe.
    """

    reasons = []

    band = (overfit or {}).get("risk")

    outcome = (verdict or {}).get("verdict")

    if outcome == "FAIL":
        failed = (verdict or {}).get("failed") or []
        reasons.append("failed: " + ", ".join(failed))
        return {"status": "FAIL", "reasons": reasons}

    if coverage is not None and coverage.get("decision_coverage") is not None:
        if coverage["decision_coverage"] < 0.80:
            reasons.append(
                "point-in-time fundamental coverage is %.1f%% of decisions, "
                "below the 80%% the specification asks for"
                % (coverage["decision_coverage"] * 100)
            )

    if news is not None and not news.get("available"):
        reasons.append("news could not be measured at all: %s"
                       % (news.get("reason") or "no provider"))

    if band in (None, "UNKNOWN"):
        reasons.append("overfitting risk is UNKNOWN -- too little of the "
                       "model could be evaluated to name a band")

    if band == "HIGH":
        reasons.append("overfitting risk is HIGH")
        return {"status": "FAIL", "reasons": reasons}

    if outcome == "INCONCLUSIVE":
        reasons.append("criteria could not all be measured: %s"
                       % ", ".join((verdict or {}).get("unmeasured") or []))

    if reasons:
        return {"status": "NEEDS MORE DEVELOPMENT", "reasons": reasons}

    if band == "MEDIUM":
        return {
            "status": "PAPER TRADING ONLY",
            "reasons": ["every criterion passed, but overfitting risk is "
                        "MEDIUM and the universe is survivors-only"],
        }

    return {
        "status": "READY FOR PAPER TRADING",
        "reasons": ["every criterion passed with LOW overfitting risk; paper "
                    "trading is still the next step, because the queue model "
                    "is an approximation and survivorship bias is not removed"],
    }
