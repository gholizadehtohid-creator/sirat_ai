"""Output: console, CSV, JSON, HTML -- and the PASS / FAIL that decides.

A REPORT THAT OMITS ITS ASSUMPTIONS IS NOT A RESULT
---------------------------------------------------
Every report this module writes carries, above the numbers:

  * what a round trip was assumed to cost, and where each part of that figure
    came from (documented, statutory, inferred, or unmeasured)
  * which parts of the model are approximations -- queues above all
  * that the universe is survivors-only, so the result is optimistic

None of that is decoration. A reader who sees "+34%" without seeing "queues
were modelled from daily bars" has been given a number they cannot judge.

CHARTS WITHOUT A DEPENDENCY
---------------------------
matplotlib is not in requirements.txt, and this project has a stated
preference for not adding one. The HTML report therefore draws its own charts
as inline SVG from the equity curve -- no library, no image files, nothing to
install. If matplotlib happens to be present, PNGs are written as well.
"""

import csv
import json
import math
import os

import pandas as pd

import backtest_config
import config
import performance
from backtesting import metrics as metrics_module


# ==============================
# PASS / FAIL
# ==============================

PASS = "PASS"
FAIL = "FAIL"
INCONCLUSIVE = "INCONCLUSIVE"


def evaluate(result, oos=None, overfit=None, criteria=None):
    """Check the run against every threshold in backtest_config.PASS_CRITERIA.

    Three outcomes, not two. A criterion whose input does not exist is neither
    passed nor failed -- it is unmeasured, and a run with unmeasured criteria
    and no failures is INCONCLUSIVE. Reporting that as PASS is precisely the
    move this whole package exists to prevent.
    """

    criteria = criteria or backtest_config.PASS_CRITERIA

    checks = []

    if result is None or not result.available:
        return {
            "verdict": FAIL,
            "checks": [],
            "reason": "; ".join(result.insufficient) if result else "no result",
        }

    m = result.metrics
    trades = m.get("trades") or {}
    drawdown = m.get("drawdown") or {}

    # ---- CAGR against cash, not against zero ----------------------------
    cagr = m.get("cagr_pct")
    hurdle = config.RISK_FREE_RATE * 100

    checks.append(_check(
        "CAGR over risk-free",
        actual=(round(cagr - hurdle, 2) if cagr is not None else None),
        required=criteria["min_cagr_over_risk_free_pct"],
        compare=">=",
        note="CAGR %.2f%% against a %.0f%% policy rate"
             % (cagr, hurdle) if cagr is not None else "no CAGR",
    ))

    checks.append(_check(
        "Profit factor", trades.get("profit_factor"),
        criteria["min_profit_factor"], ">=",
        note="gross profit / gross loss, in rials",
    ))

    checks.append(_check(
        "Sharpe", m.get("sharpe"), criteria["min_sharpe"], ">=",
        note="already excess of the %.0f%% hurdle" % hurdle,
    ))

    checks.append(_check(
        "Max drawdown", drawdown.get("max_drawdown_pct"),
        -abs(criteria["max_drawdown_pct"]), ">=",
        note="deeper than this and the account stops being traded",
    ))

    checks.append(_check(
        "Closed trades", trades.get("trades"), criteria["min_trades"], ">=",
        note="below this the statistics are not statistics",
    ))

    # ---- out-of-sample ---------------------------------------------------
    retained = None

    if oos and (oos.get("degradation") or {}).get("available"):
        retained = oos["degradation"].get("retained")

    checks.append(_check(
        "Out-of-sample CAGR retained", retained,
        criteria["min_oos_cagr_retained"], ">=",
        note="share of in-sample CAGR that survived the hold-out",
    ))

    # ---- benchmark --------------------------------------------------------
    excess = None

    tedpix = (result.benchmarks or {}).get("tedpix") or {}

    if tedpix.get("excess_pct") is not None:
        excess = tedpix["excess_pct"]

    checks.append(_check(
        "Excess over شاخص کل", excess,
        criteria["min_excess_over_benchmark_pct"], ">=",
        note="beating cash while losing to the index means the index was the "
             "better trade",
    ))

    # ---- overfitting ------------------------------------------------------
    order = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}

    band = (overfit or {}).get("risk")

    allowed = criteria["max_overfitting_risk"]

    if band in order:
        checks.append({
            "name": "Overfitting risk",
            "actual": band,
            "required": allowed,
            "compare": "at most",
            "passed": order[band] <= order.get(allowed, 1),
            "note": (overfit or {}).get("note"),
        })
    else:
        checks.append({
            "name": "Overfitting risk", "actual": None,
            "required": allowed, "compare": "at most", "passed": None,
            "note": "not measured",
        })

    failures = [c for c in checks if c["passed"] is False]
    unmeasured = [c for c in checks if c["passed"] is None]

    if failures:
        verdict = FAIL
    elif unmeasured:
        verdict = INCONCLUSIVE
    else:
        verdict = PASS

    return {
        "verdict": verdict,
        "checks": checks,
        "failed": [c["name"] for c in failures],
        "unmeasured": [c["name"] for c in unmeasured],
    }


def _check(name, actual, required, compare=">=", note=None):

    if actual is None:
        passed = None
    elif compare == ">=":
        passed = actual >= required
    elif compare == "<=":
        passed = actual <= required
    else:
        passed = None

    return {
        "name": name,
        "actual": actual,
        "required": required,
        "compare": compare,
        "passed": passed,
        "note": note,
    }


# ==============================
# CONSOLE
# ==============================

def _line(label, value, unit="", width=30):
    if value is None:
        return "  %-*s %s" % (width, label, "—")
    if isinstance(value, float):
        return "  %-*s %s%s" % (width, label, ("%+.2f" % value
                                               if unit == "%" else "%.2f" % value), unit)
    return "  %-*s %s%s" % (width, label, value, unit)


def _rials(value):
    if value is None:
        return "—"
    return "{:,.0f}".format(value)


def print_report(result, oos=None, overfit=None, monte=None, sweep=None,
                 folds=None, variants=None, verdict=None):
    """The full console report, in the order a reader needs it."""

    print()
    print("=" * 78)
    print("           SIRAT-AI BACKTEST REPORT")
    print("=" * 78)

    print()
    print("Period:")
    print("  Start:            %s" % result.start)
    print("  End:              %s" % result.end)
    print("  Variant:          %s" % result.variant)

    if not result.available:
        print()
        print("INSUFFICIENT DATA")
        print("-" * 78)
        for line in result.insufficient:
            print("  * " + line)
        print()
        print("Nothing below this line would be a measurement. No numbers are")
        print("reported for a period the data cannot support.")
        return

    m = result.metrics
    trades = m.get("trades") or {}
    drawdown = m.get("drawdown") or {}

    print()
    print("-" * 78)
    print("ASSUMPTIONS (read before the numbers)")
    print("-" * 78)
    print("  Round trip cost:  %.3f%%  (buy %.3f%% + sell %.3f%% + tax %.2f%%"
          " + slippage %.2f%%)"
          % (result.cfg.round_trip_pct(), result.cfg.buy_commission_pct,
             result.cfg.sell_commission_pct, result.cfg.sell_tax_pct,
             result.cfg.slippage_pct))

    for name, value, provenance, source in backtest_config.cost_provenance():
        print("     %-16s %6.3f%%  %-10s %s" % (name, value, provenance, source))

    print("  Execution:        decide at T close, fill at T+%d %s"
          % (result.cfg.execution_delay_sessions, result.cfg.execution_price))
    print("  Queues (صف):      %s -- APPROXIMATION, inferred from daily bars"
          % ("modelled" if result.cfg.model_queues else "NOT modelled"))
    print("  Participation:    max %.1f%% of a session's own traded value"
          % result.cfg.max_participation_pct)
    print("  Universe:         instruments listed TODAY -- delisted companies")
    print("                    are absent, so every result is optimistic by an")
    print("                    unknown margin (survivorship bias)")

    print()
    print("-" * 78)
    print("PERFORMANCE")
    print("-" * 78)
    print("  Initial capital   %s" % _rials(m.get("initial_capital")))
    print("  Final capital     %s" % _rials(m.get("final_capital")))
    print("  Profit            %s" % _rials(m.get("profit")))
    print(_line("Total return", m.get("total_return_pct"), "%"))
    print(_line("CAGR", m.get("cagr_pct"), "%"))
    print(_line("  vs cash at %.0f%%" % (config.RISK_FREE_RATE * 100),
                m.get("excess_over_cash_pct"), "%"))

    if m.get("annual_returns"):
        print("  Per calendar year:")
        for row in m["annual_returns"]:
            print("     %d  %+8.2f%%   (%d sessions)"
                  % (row["year"], row["return_pct"], row["sessions"]))

    print()
    print("-" * 78)
    print("RISK")
    print("-" * 78)
    print(_line("Max drawdown", drawdown.get("max_drawdown_pct"), "%"))
    if drawdown.get("peak_date"):
        print("     %s → %s, %s"
              % (drawdown["peak_date"], drawdown["trough_date"],
                 "recovered" if drawdown.get("recovered") else "NOT recovered"))
    print(_line("Average drawdown", m.get("average_drawdown_pct"), "%"))
    print(_line("Drawdown episodes", m.get("drawdown_episodes")))
    print(_line("Volatility (ann.)", m.get("volatility_pct"), "%"))
    print(_line("Downside volatility", m.get("downside_volatility_pct"), "%"))

    print()
    print("-" * 78)
    print("RISK-ADJUSTED")
    print("-" * 78)
    print(_line("Sharpe", m.get("sharpe")))
    print(_line("Sortino", m.get("sortino")))
    print(_line("Calmar", m.get("calmar")))
    print("     Sharpe and Sortino are EXCESS of the %.0f%% policy rate, so a"
          % (config.RISK_FREE_RATE * 100))
    print("     negative figure means 'lost to cash'. Calmar in performance.py")
    print("     divides raw CAGR by the drawdown and does NOT subtract it, so")
    print("     the two can disagree in sign. Read them together.")

    print()
    print("-" * 78)
    print("TRADING")
    print("-" * 78)

    if trades.get("available"):
        print(_line("Trades", trades.get("trades")))
        print(_line("Winners", trades.get("wins")))
        print(_line("Losers", trades.get("losses")))
        print(_line("Win rate", trades.get("win_rate_pct"), "%"))
        print("  %-30s %s" % ("Average win", _rials(trades.get("average_win"))))
        print("  %-30s %s" % ("Average loss", _rials(trades.get("average_loss"))))
        print("  %-30s %s" % ("Largest win", _rials(trades.get("largest_win"))))
        print("  %-30s %s" % ("Largest loss", _rials(trades.get("largest_loss"))))
        print(_line("Payoff ratio", trades.get("payoff_ratio")))
        print(_line("Profit factor", trades.get("profit_factor")))
        print("  %-30s %s" % ("Expectancy per trade",
                              _rials(trades.get("expectancy"))))
        print(_line("Average holding (days)", trades.get("average_holding_days")))
        print("  %-30s %s" % ("Exit reasons", trades.get("exit_reasons")))
    else:
        print("  no closed trades: " + str(trades.get("reason")))

    exposure = m.get("exposure") or {}

    print()
    print(_line("Average cash", exposure.get("average_cash_pct"), "%"))
    print(_line("Average positions", exposure.get("average_positions")))
    print(_line("Sessions fully in cash", exposure.get("sessions_fully_in_cash")))

    # ---- benchmarks -------------------------------------------------------
    print()
    print("-" * 78)
    print("BENCHMARK — the question that actually matters")
    print("-" * 78)

    benchmarks = result.benchmarks or {}

    print("  Strategy          %+8.2f%%" % (benchmarks.get("strategy_return_pct") or 0.0))

    for key in ("tedpix", "tedpix_equal_weight", "liquid_basket"):
        entry = benchmarks.get(key)
        if not entry:
            continue
        if entry.get("return_pct") is None:
            print("  %-17s %s" % (entry["label"][:17], entry.get("reason", "—")))
            continue
        print("  %-17s %+8.2f%%   excess %+8.2f pp"
              % (entry["label"][:17], entry["return_pct"],
                 entry.get("excess_pct") or 0.0))

    bench = m.get("benchmark")

    if bench and bench.get("available"):
        print()
        print(_line("Alpha (annualised)", bench.get("alpha_pct"), "%"))
        print(_line("Beta", bench.get("beta")))
        print(_line("Information ratio", bench.get("information_ratio")))
        print(_line("Correlation", bench.get("correlation")))

    # ---- execution reality ------------------------------------------------
    diagnostics = result.diagnostics or {}

    print()
    print("-" * 78)
    print("EXECUTION REALITY (what the market refused)")
    print("-" * 78)
    print(_line("Orders raised", diagnostics.get("orders_raised")))
    print(_line("Fill events (completing)", diagnostics.get("filled")))
    print(_line("Fill events (partial)", diagnostics.get("partial")))
    print(_line("Expired still unfilled", diagnostics.get("orders_expired")))
    print(_line("Blocked by صف خرید", diagnostics.get("blocked_buy_queue")))
    print(_line("Blocked by صف فروش", diagnostics.get("blocked_sell_queue")))
    print(_line("Cut by participation cap",
                diagnostics.get("capped_by_participation")))
    print(_line("Exits trapped in صف فروش",
                diagnostics.get("queue_blocked_exit")))
    print("  %-30s %s" % ("Commission + tax paid",
                          _rials((diagnostics.get("commission_paid") or 0)
                                 + (diagnostics.get("tax_paid") or 0))))
    print(_line("Rebalances with no signal",
                diagnostics.get("empty_rebalances")))
    print(_line("Future reads refused",
                diagnostics.get("blocked_future_reads")))

    # ---- validation -------------------------------------------------------
    if oos and oos.get("available"):
        print()
        print("-" * 78)
        print("IN-SAMPLE / VALIDATION / OUT-OF-SAMPLE")
        print("-" * 78)
        print("  %-14s %-12s %10s %10s %8s %8s %7s"
              % ("period", "window", "return%", "CAGR%", "maxDD%", "Sharpe", "trades"))

        for name in ("in_sample", "validation", "out_of_sample"):
            segment = oos["segments"].get(name)
            if not segment:
                continue
            hm = segment.get("metrics") or {}
            if not segment.get("available"):
                print("  %-14s %-12s  INSUFFICIENT DATA — %s"
                      % (name, segment["start"], (segment.get("reason") or "")[:40]))
                continue
            print("  %-14s %-12s %10s %10s %8s %8s %7s"
                  % (name, segment["start"],
                     _fmt(hm.get("total_return_pct")), _fmt(hm.get("cagr_pct")),
                     _fmt(hm.get("max_drawdown_pct")), _fmt(hm.get("sharpe")),
                     hm.get("trades")))

        degradation = oos.get("degradation") or {}

        if degradation.get("retained") is not None:
            print()
            print("  Out-of-sample kept %.0f%% of in-sample CAGR"
                  % (degradation["retained"] * 100))
        elif degradation.get("note"):
            print()
            print("  " + degradation["note"])

    if folds and folds.get("folds"):
        print()
        print("-" * 78)
        print("WALK-FORWARD (%s chosen on train, measured on test)"
              % folds["parameter"])
        print("-" * 78)
        print("  %-5s %-24s %-24s %8s %10s %10s"
              % ("fold", "train", "test", "chosen", "train " + folds["metric"],
                 "test " + folds["metric"]))

        for fold in folds["folds"]:
            if fold.get("chosen") is None:
                print("  %-5s %s" % (fold["fold"], fold.get("reason", "—")))
                continue
            print("  %-5s %-24s %-24s %8s %10s %10s"
                  % (fold["fold"],
                     "%s..%s" % tuple(fold["train"]),
                     "%s..%s" % tuple(fold["test"]),
                     fold["chosen"], _fmt(fold.get("train_metric")),
                     _fmt(fold.get("test_metric"))))

        summary = folds.get("summary") or {}

        if summary.get("available"):
            print()
            print("  mean train %s = %s   mean test %s = %s   "
                  "positive test folds %d/%d"
                  % (folds["metric"], summary["mean_train_metric"],
                     folds["metric"], summary["mean_test_metric"],
                     summary["positive_test_folds"], summary["folds"]))

            if summary.get("parameter_stability") is not None:
                print("  parameter stability %.2f (1.00 = a different choice "
                      "every window, i.e. fitted to noise)"
                      % summary["parameter_stability"])
            else:
                print("  parameter stability: undefined — only one fold fits "
                      "in this window")

    if monte and monte.get("available"):
        print()
        print("-" * 78)
        print("MONTE CARLO (%d paths, %s)" % (monte["runs"], monte["method"]))
        print("-" * 78)
        print(_line("Median return", monte.get("median_return_pct"), "%"))
        print(_line("5th percentile", monte["percentiles"].get("p5_return_pct"), "%"))
        print(_line("95th percentile", monte["percentiles"].get("p95_return_pct"), "%"))
        print(_line("Probability of loss", monte.get("probability_of_loss_pct"), "%"))
        print(_line("Expected max drawdown",
                    monte.get("expected_max_drawdown_pct"), "%"))
        print(_line("Worst path drawdown", monte.get("worst_drawdown_pct"), "%"))
        print(_line("Realised result's percentile",
                    monte.get("realised_percentile"), "%"))
        if (monte.get("realised_percentile") or 0) > 80:
            print("     The realised path sits in the top fifth of its own")
            print("     distribution: the ORDER of these trades flattered them.")

    if sweep and sweep.get("parameters"):
        print()
        print("-" * 78)
        print("SENSITIVITY (metric: %s)" % sweep["metric"])
        print("-" * 78)
        print("  %-26s %8s %10s %10s %s"
              % ("parameter", "cv", "live", "best", "values"))

        for name, entry in sweep["parameters"].items():
            if "values" not in entry:
                continue
            values = " ".join(
                "%s=%s" % (v["value"], _fmt(v["metric"])) for v in entry["values"]
            )
            print("  %-26s %8s %10s %10s %s"
                  % (name + (" FRAGILE" if entry["fragile"] else ""),
                     _fmt(entry["coefficient_of_variation"]),
                     entry.get("live_value"), entry.get("best_value"), values))

    if variants:
        print()
        print("-" * 78)
        print("WHAT EACH EVIDENCE SET CONTRIBUTES")
        print("-" * 78)
        print("  %-30s %9s %9s %9s %8s %8s %8s"
              % ("variant", "return%", "CAGR%", "maxDD%", "Sharpe", "win%", "PF"))

        for row in metrics_module.compare(variants):
            if not row["available"]:
                print("  %-30s %s" % (row["label"], row["reason"]))
                continue
            print("  %-30s %9s %9s %9s %8s %8s %8s"
                  % (row["label"], _fmt(row["total_return_pct"]),
                     _fmt(row["cagr_pct"]), _fmt(row["max_drawdown_pct"]),
                     _fmt(row["sharpe"]), _fmt(row["win_rate_pct"]),
                     _fmt(row["profit_factor"])))

    if overfit:
        print()
        print("-" * 78)
        print("OVERFITTING RISK: %s" % overfit["risk"])
        print("-" * 78)
        for finding in overfit["findings"]:
            points = ("%.1f/%s" % (finding["points"], finding["of"])
                      if finding["points"] is not None else "  -/%s" % finding["of"])
            print("  %-22s %-10s %s" % (finding["test"], points, finding["detail"]))
        print()
        print("  score %s of 100 -- %s" % (overfit["score"], overfit["note"]))

    if verdict:
        print()
        print("=" * 78)
        print("FINAL VERDICT: %s" % verdict["verdict"])
        print("=" * 78)
        for check in verdict["checks"]:
            mark = {True: "PASS", False: "FAIL", None: "  ? "}[check["passed"]]
            print("  [%s] %-28s %10s  (needs %s %s)"
                  % (mark, check["name"], _fmt(check["actual"]),
                     check["compare"], check["required"]))
            if check.get("note"):
                print("         %s" % check["note"])

        if verdict["verdict"] == INCONCLUSIVE:
            print()
            print("  INCONCLUSIVE is not a soft pass. These criteria could not")
            print("  be measured on the data available: %s"
                  % ", ".join(verdict["unmeasured"]))


def _fmt(value):
    if value is None:
        return "—"
    if isinstance(value, float):
        return "%.2f" % value
    return str(value)


def _wrap(text, width):
    """Soft-wrap a sentence for the console. No dependency, no hyphenation."""

    lines, current = [], ""

    for word in str(text or "").split():
        if current and len(current) + len(word) + 1 > width:
            lines.append(current)
            current = word
        else:
            current = (current + " " + word).strip()

    if current:
        lines.append(current)

    return lines or [""]


# ==============================
# FILES
# ==============================

def write_all(result, oos=None, overfit=None, monte=None, sweep=None,
              folds=None, variants=None, verdict=None, directory=None,
              prefix="backtest"):
    """Write every configured output format. Returns the paths written."""

    directory = directory or backtest_config.OUTPUT_DIR

    os.makedirs(directory, exist_ok=True)

    written = []

    if backtest_config.WRITE_CSV:
        written.extend(_write_csv(result, directory, prefix))

    payload = _payload(result, oos, overfit, monte, sweep, folds, variants,
                       verdict)

    if backtest_config.WRITE_JSON:
        path = os.path.join(directory, prefix + "_report.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2,
                      default=str)
        written.append(path)

    if backtest_config.WRITE_HTML:
        path = os.path.join(directory, prefix + "_report.html")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(_html(result, payload))
        written.append(path)

    if backtest_config.WRITE_CHARTS:
        written.extend(_charts(result, directory, prefix))

    return written


def _write_csv(result, directory, prefix, scoped=False):
    """Curve, trades and a headline row.

    `scoped` prefixes the curve and trade files. V1 writes them under their
    plain names and has since its first commit, so that stays the default; V2
    scopes them, because a V2 run silently overwriting V1's trades.csv would
    destroy the artefact the two are being compared against.
    """

    written = []

    stem = (prefix + "_") if scoped else ""

    frame = metrics_module.equity_frame(result.curve)

    if not frame.empty:
        path = os.path.join(directory, stem + "equity_curve.csv")
        out = frame.reset_index()
        out.to_csv(path, index=False, encoding="utf-8-sig")
        written.append(path)

    if result.trades:
        path = os.path.join(directory, stem + "trades.csv")
        keys = sorted({k for t in result.trades for k in t})
        with open(path, "w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=keys)
            writer.writeheader()
            writer.writerows(result.trades)
        written.append(path)

    # One row of headline numbers, so several runs can be diffed in a
    # spreadsheet without opening the JSON.
    path = os.path.join(directory, prefix + "_results.csv")

    m = result.metrics or {}
    trades = m.get("trades") or {}

    row = {
        "variant": result.variant,
        "start": result.start,
        "end": result.end,
        "available": result.available,
        "initial_capital": m.get("initial_capital"),
        "final_capital": m.get("final_capital"),
        "total_return_pct": m.get("total_return_pct"),
        "cagr_pct": m.get("cagr_pct"),
        "max_drawdown_pct": (m.get("drawdown") or {}).get("max_drawdown_pct"),
        "sharpe": m.get("sharpe"),
        "sortino": m.get("sortino"),
        "calmar": m.get("calmar"),
        "trades": trades.get("trades"),
        "win_rate_pct": trades.get("win_rate_pct"),
        "profit_factor": trades.get("profit_factor"),
        "expectancy": trades.get("expectancy"),
        "round_trip_pct": round(result.cfg.round_trip_pct(), 3),
    }

    with open(path, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)

    written.append(path)

    return written


def _payload(result, oos, overfit, monte, sweep, folds, variants, verdict):

    payload = result.to_dict()

    payload["assumptions"] = {
        "cost_provenance": [
            {"item": name, "value_pct": value, "provenance": provenance,
             "source": source}
            for name, value, provenance, source in backtest_config.cost_provenance()
        ],
        "round_trip_pct": round(result.cfg.round_trip_pct(), 3),
        "queues_are_approximated": bool(result.cfg.model_queues),
        "survivorship_bias": "the universe is instruments listed today; "
                             "delisted companies are absent from every window",
    }

    if oos:
        payload["out_of_sample"] = {
            "available": oos.get("available"),
            "reason": oos.get("reason"),
            "degradation": oos.get("degradation"),
            "segments": {
                name: {k: v for k, v in segment.items() if k != "result"}
                for name, segment in (oos.get("segments") or {}).items()
            },
        }

    if folds:
        payload["walk_forward"] = folds

    if monte:
        payload["monte_carlo"] = monte

    if sweep:
        payload["sensitivity"] = sweep

    if overfit:
        payload["overfitting"] = overfit

    if variants:
        payload["variants"] = metrics_module.compare(variants)

    if verdict:
        payload["verdict"] = verdict

    return payload


# ==============================
# V2 REPORT
# ==============================
#
# Separate entry points rather than flags on print_report(), so that the V1
# report keeps rendering exactly as it always did. Anyone comparing a V1 and a
# V2 report side by side is comparing runs, not formatting changes.

V2_COLUMNS = [
    ("total_return_pct", "return%", 9),
    ("cagr_pct", "CAGR%", 8),
    ("sharpe", "Sharpe", 8),
    ("sortino", "Sortino", 8),
    ("max_drawdown_pct", "maxDD%", 8),
    ("calmar", "Calmar", 8),
    ("profit_factor", "PF", 6),
    ("win_rate_pct", "win%", 7),
    ("trades", "trades", 7),
    ("average_holding_days", "hold", 6),
    ("average_cash_pct", "cash%", 7),
    ("average_positions", "pos", 6),
    ("benchmark_difference_pp", "vs idx", 9),
]


def _v2_table(rows, first="", width=32):
    """One comparison table. Unavailable rows say why instead of vanishing."""

    header = "  %-*s" % (width, first)

    for _key, label, size in V2_COLUMNS:
        header += "%*s" % (size, label)

    print(header)
    print("  " + "-" * (width + sum(s for _k, _l, s in V2_COLUMNS)))

    for row in rows:

        if not row.get("available"):
            print("  %-*s  INSUFFICIENT DATA — %s"
                  % (width, str(row["label"])[:width],
                     (row.get("reason") or "")[:60]))
            continue

        line = "  %-*s" % (width, str(row["label"])[:width])

        for key, _label, size in V2_COLUMNS:
            line += "%*s" % (size, _fmt(row.get(key)))

        print(line)


def print_v2(out):
    """The V2 console report, in the order the specification asks for it."""

    result = out.get("result")

    print()
    print("=" * 78)
    print("           SIRAT-AI BACKTEST V2 — REPORT")
    print("=" * 78)
    print("  window     %s .. %s" % (out["start"].date(), out["end"].date()))
    print("  stages     %s" % ", ".join(out.get("stages_run") or ["—"]))

    # ---- executive summary -----------------------------------------------
    v1 = out.get("v1") or {}

    print()
    print("-" * 78)
    print("EXECUTIVE SUMMARY")
    print("-" * 78)

    verdict = out.get("verdict") or {}
    recommendation = out.get("recommendation") or {}

    better, why = _v2_is_better(out, v1)

    print("  Is V2 better than V1?   %s" % better)
    for line in why:
        print("     - " + line)

    print()
    print("  FINAL VERDICT:    %s" % verdict.get("verdict", "—"))
    print("  OVERFITTING RISK: %s" % (out.get("overfit") or {}).get("risk", "—"))
    print("  RECOMMENDATION:   %s" % recommendation.get("status", "—"))
    for reason in recommendation.get("reasons") or []:
        print("     - " + reason)

    # ---- V1 vs V2 ---------------------------------------------------------
    print()
    print("-" * 78)
    print("V1 vs V2  (same window, same evidence set: technical)")
    print("-" * 78)
    print("  V1 figures from: %s" % v1.get("source"))
    print()

    m = result.metrics if result is not None and result.available else {}
    trades = m.get("trades") or {}
    exposure = m.get("exposure") or {}
    drawdown = m.get("drawdown") or {}

    v2 = {
        "total_return_pct": m.get("total_return_pct"),
        "cagr_pct": m.get("cagr_pct"),
        "max_drawdown_pct": drawdown.get("max_drawdown_pct"),
        "sharpe": m.get("sharpe"),
        "sortino": m.get("sortino"),
        "profit_factor": trades.get("profit_factor"),
        "win_rate_pct": trades.get("win_rate_pct"),
        "trades": trades.get("trades"),
        "average_holding_days": trades.get("average_holding_days"),
        "average_cash_pct": exposure.get("average_cash_pct"),
        "average_positions": exposure.get("average_positions"),
        "final_capital": m.get("final_capital"),
    }

    print("  %-28s %14s %14s %12s" % ("metric", "V1", "V2", "change"))
    print("  " + "-" * 70)

    for key, label, better_when in (
        ("total_return_pct", "Total return %", "higher"),
        ("cagr_pct", "CAGR %", "higher"),
        ("max_drawdown_pct", "Max drawdown %", "higher"),
        ("sharpe", "Sharpe", "higher"),
        ("sortino", "Sortino", "higher"),
        ("profit_factor", "Profit factor", "higher"),
        ("win_rate_pct", "Win rate %", "higher"),
        ("trades", "Trades", "neutral"),
        ("average_holding_days", "Avg holding (days)", "neutral"),
        ("average_cash_pct", "Average cash %", "neutral"),
        ("average_positions", "Average positions", "neutral"),
        ("final_capital", "Final capital", "higher"),
    ):
        old, new = v1.get(key), v2.get(key)

        change = "—"

        if isinstance(old, (int, float)) and isinstance(new, (int, float)):
            delta = new - old
            mark = ""
            if better_when == "higher":
                mark = "  better" if delta > 0 else ("  worse" if delta < 0 else "")
            change = ("%+.2f%s" % (delta, mark)) if abs(delta) < 1e9 else "%+.0f" % delta

        print("  %-28s %14s %14s %12s"
              % (label, _fmt(old), _fmt(new), change))

    # ---- data readiness ---------------------------------------------------
    readiness = out.get("readiness") or {}
    coverage = readiness.get("fundamental_coverage") or {}
    news = readiness.get("news") or {}
    bias = readiness.get("survivorship") or {}

    print()
    print("-" * 78)
    print("FUNDAMENTAL COVERAGE (point-in-time)")
    print("-" * 78)

    if coverage.get("available"):
        print("  decisions with a real filing   %.1f%%  (%s of %s)"
              % (coverage["decision_coverage"] * 100,
                 "{:,}".format(coverage["decisions_covered"]),
                 "{:,}".format(coverage["decisions"])))
        # V1's own report only recorded this figure for a fundamental variant,
        # so a technical V1 run leaves it absent. Printing 0.0% there would
        # invent a measurement; the request's stated 12.7% is cited as what it
        # is -- a reported figure, not one this run reproduced.
        v1_coverage = v1.get("fundamental_coverage")

        print("  V1 reported                    %s"
              % ("%.1f%%" % (v1_coverage * 100) if v1_coverage
                 else "12.7% (as stated in the V2 request; the V1 report on "
                      "disk did not record it)"))

        print("  target in the specification    80.0%")
        print("  companies fully / partly / never covered   %d / %d / %d"
              % (coverage["companies_full"], coverage["companies_partial"],
                 coverage["companies_none"]))
        print()
        for row in coverage["by_year"]:
            print("     %d   %6.1f%%" % (row["year"], row["coverage"] * 100))

        if coverage["decision_coverage"] < 0.80:
            print()
            print("  BELOW TARGET. This is a data-acquisition limit, not a")
            print("  modelling one: codal.ir throttles at roughly 40 requests")
            print("  per burst, and filling the window needs thousands. Run")
            print("  `python codal_backfill.py --hours 12` repeatedly to close")
            print("  the gap; nothing here estimates the missing filings.")
    else:
        print("  not measurable: %s" % coverage.get("reason"))

    print()
    print("-" * 78)
    print("NEWS")
    print("-" * 78)
    print("  provider   %s" % news.get("provider"))

    if news.get("available"):
        print("  available  yes")
    else:
        print("  NEWS = INSUFFICIENT DATA")
        for line in _wrap(news.get("reason") or "", 72):
            print("     " + line)

    # ---- models -----------------------------------------------------------
    models = out.get("models") or {}

    if models.get("rows"):
        print()
        print("-" * 78)
        print("MODEL COMPARISON")
        print("-" * 78)
        _v2_table(models["rows"], first="model")

    # ---- execution --------------------------------------------------------
    execution = out.get("execution") or {}

    if execution.get("rows"):
        print()
        print("-" * 78)
        print("EXECUTION SENSITIVITY — slippage")
        print("-" * 78)
        _v2_table(execution["rows"], first="scenario", width=20)
        print()
        for row in execution["rows"]:
            if row.get("available") and row.get("return_lost_pp"):
                print("     %.2f%% slippage costs %.2f points of total return"
                      % (row["slippage_pct"], row["return_lost_pp"]))

    # ---- sizing -----------------------------------------------------------
    sizing = out.get("sizing") or {}

    if sizing.get("rows"):
        print()
        print("-" * 78)
        print("POSITION SIZING")
        print("-" * 78)
        _v2_table(sizing["rows"], first="mode", width=24)

    # ---- cash -------------------------------------------------------------
    diagnostics = (result.diagnostics or {}) if result is not None else {}

    cash = diagnostics.get("cash_analysis") or {}

    if cash.get("available"):
        print()
        print("-" * 78)
        print("CASH — why the book was not invested")
        print("-" * 78)
        print("  average cash            %.1f%%" % (cash["average_cash_pct"] or 0))
        print("  average positions       %.2f of %d"
              % (cash["average_positions"] or 0, cash["max_positions"]))
        print("  configured cash floor   %.1f%%  (explains %s%% of the cash)"
              % (cash["cash_reserve_pct"], _fmt(cash.get("floor_explains_pct"))))
        print()
        print("  THE FUNNEL — the binding constraint is the steepest drop,")
        print("  not the largest count. The stages measure different things.")
        print()
        print("  %-46s %8s %9s" % ("stage", "count", "survived"))
        print("  " + "-" * 66)

        for stage in cash.get("funnel") or []:
            print("  %-46s %8d %8s"
                  % (stage["stage"][:46], stage["count"],
                     ("%.1f%%" % (stage["survived"] * 100))
                     if stage["survived"] is not None else "—"))
            for row in (stage.get("detail") or [])[:8]:
                print("       lost to %-36s %6d  %5.1f%%"
                      % (str(row["reason"])[:36], row["count"],
                         row["share"] * 100))

        print()
        print("  BINDING CONSTRAINT: %s" % (cash.get("largest_cause") or "—"))

    # ---- survivorship -----------------------------------------------------
    if bias.get("available"):
        print()
        print("-" * 78)
        print("SURVIVORSHIP BIAS")
        print("-" * 78)
        print("  " + bias["verdict"].replace("; ", "\n  "))

    # ---- validation -------------------------------------------------------
    if out.get("oos") or out.get("folds") or out.get("monte") or out.get("sweep"):
        print_report(result, oos=out.get("oos"), overfit=out.get("overfit"),
                     monte=out.get("monte"), sweep=out.get("sweep"),
                     folds=out.get("folds"), verdict=out.get("verdict"))

    # ---- failure analysis -------------------------------------------------
    failure = out.get("failure") or {}

    if failure.get("available"):
        print()
        print("=" * 78)
        print("FAILURE ANALYSIS — what cost the money")
        print("=" * 78)
        for finding in failure["findings"]:
            for index, line in enumerate(_wrap(finding["finding"], 72)):
                print(("  * " if index == 0 else "    ") + line)

    print()
    print("=" * 78)
    print("RECOMMENDATION: %s" % recommendation.get("status", "—"))
    print("=" * 78)
    for reason in recommendation.get("reasons") or []:
        for index, line in enumerate(_wrap(reason, 72)):
            print(("  - " if index == 0 else "    ") + line)


def _v2_is_better(out, v1):
    """A one-word answer with its arithmetic, not an impression.

    "Better" is decided on risk-adjusted return and the drawdown, not on the
    headline return: a version that made more money by holding more risk has
    not improved the strategy, it has changed the bet.
    """

    result = out.get("result")

    if result is None or not result.available:
        return "NO — V2 produced no measurable result", [
            "; ".join(result.insufficient) if result else "no result"
        ]

    m = result.metrics
    trades = m.get("trades") or {}
    drawdown = m.get("drawdown") or {}

    checks = []

    wins = losses = 0

    for key, label, new in (
        ("sharpe", "Sharpe", m.get("sharpe")),
        ("sortino", "Sortino", m.get("sortino")),
        ("total_return_pct", "total return", m.get("total_return_pct")),
        ("max_drawdown_pct", "max drawdown", drawdown.get("max_drawdown_pct")),
        ("profit_factor", "profit factor", trades.get("profit_factor")),
    ):
        old = v1.get(key)

        if old is None or new is None:
            checks.append("%s: not comparable" % label)
            continue

        delta = new - old

        if abs(delta) < 1e-9:
            checks.append("%s unchanged at %s" % (label, _fmt(new)))
            continue

        direction = "improved" if delta > 0 else "worsened"

        if delta > 0:
            wins += 1
        else:
            losses += 1

        checks.append("%s %s: %s -> %s (%+.2f)"
                      % (label, direction, _fmt(old), _fmt(new), delta))

    if wins and not losses:
        answer = "YES — every comparable metric improved"
    elif losses and not wins:
        answer = "NO — every comparable metric worsened"
    elif wins > losses:
        answer = "PARTLY — %d metrics improved, %d worsened" % (wins, losses)
    elif losses > wins:
        answer = "NO — %d metrics worsened, %d improved" % (losses, wins)
    else:
        answer = "NO MEANINGFUL DIFFERENCE"

    # The verdict overrides an arithmetic improvement. A run that improved
    # every metric and still fails its criteria has not become a strategy.
    if (out.get("verdict") or {}).get("verdict") == FAIL:
        answer += " — but the run still FAILS its criteria"

    return answer, checks


def _v2_payload(out):
    """The whole V2 run as JSON-safe data. Results become their dicts."""

    result = out.get("result")

    payload = {
        "version": 2,
        "start": str(out["start"]),
        "end": str(out["end"]),
        "stages_run": out.get("stages_run"),
        "seconds": out.get("seconds"),
        "config": out["cfg"].as_dict() if out.get("cfg") else {},
        "v1_baseline": out.get("v1"),
        "readiness": _readiness_payload(out.get("readiness")),
        "verdict": out.get("verdict"),
        "overfitting": out.get("overfit"),
        "failure_analysis": out.get("failure"),
        "recommendation": out.get("recommendation"),
    }

    if result is not None:
        payload["headline"] = result.to_dict()

    for key in ("models", "execution", "sizing"):
        section = out.get(key)
        if section:
            payload[key] = {k: v for k, v in section.items()
                            if k not in ("results", "panels")}

    if out.get("oos"):
        payload["out_of_sample"] = {
            "available": out["oos"].get("available"),
            "reason": out["oos"].get("reason"),
            "degradation": out["oos"].get("degradation"),
            "segments": {
                name: {k: v for k, v in segment.items() if k != "result"}
                for name, segment in (out["oos"].get("segments") or {}).items()
            },
        }

    for key, name in (("folds", "walk_forward"), ("monte", "monte_carlo"),
                      ("sweep", "sensitivity")):
        if out.get(key):
            payload[name] = out[key]

    return payload


def _readiness_payload(readiness):
    """Coverage without the per-company dictionary, which is 818 keys of noise.

    The counts stay; the roll call goes to its own CSV, where a reader who
    wants it can sort it.
    """

    if not readiness:
        return {}

    coverage = dict(readiness.get("fundamental_coverage") or {})

    coverage.pop("company_detail", None)

    bias = dict(readiness.get("survivorship") or {})

    return {
        "fundamental_coverage": coverage,
        "news": readiness.get("news"),
        "survivorship": bias,
    }


def write_v2(out, directory=None, prefix="backtest_v2"):
    """Write the V2 outputs. Returns the paths written."""

    directory = directory or backtest_config.OUTPUT_DIR

    os.makedirs(directory, exist_ok=True)

    written = []

    payload = _v2_payload(out)

    path = os.path.join(directory, prefix + "_report.json")

    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, default=str)

    written.append(path)

    # ---- the comparison tables, as CSV a spreadsheet can sort -------------
    for key in ("models", "execution", "sizing"):

        section = out.get(key) or {}

        rows = section.get("rows")

        if not rows:
            continue

        csv_path = os.path.join(directory, "%s_%s.csv" % (prefix, key))

        keys = []

        for row in rows:
            for field in row:
                if field not in keys:
                    keys.append(field)

        with open(csv_path, "w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=keys)
            writer.writeheader()
            writer.writerows(rows)

        written.append(csv_path)

    # ---- per-company fundamental coverage --------------------------------
    coverage = ((out.get("readiness") or {}).get("fundamental_coverage") or {})

    detail = coverage.get("company_detail")

    if detail:
        csv_path = os.path.join(directory, prefix + "_fundamental_coverage.csv")
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            writer.writerow(["ins_code", "dates_covered", "of_dates", "share"])
            total = coverage.get("dates") or 1
            for code, count in sorted(detail.items(), key=lambda kv: -kv[1]):
                writer.writerow([code, count, total, round(count / total, 4)])
        written.append(csv_path)

    # ---- the headline run's own CSVs, unchanged from V1 ------------------
    result = out.get("result")

    if result is not None and result.available:
        written.extend(_write_csv(result, directory, prefix, scoped=True))

    if backtest_config.WRITE_HTML and result is not None:
        html_path = os.path.join(directory, prefix + "_report.html")
        with open(html_path, "w", encoding="utf-8") as handle:
            handle.write(_html_v2(out, payload))
        written.append(html_path)

    return written


def _html_table(rows, first="", columns=None):
    """One comparison table as HTML. Unavailable rows keep their reason."""

    columns = columns or V2_COLUMNS

    head = "".join("<th class='num'>%s</th>" % label
                   for _key, label, _size in columns)

    body = []

    for row in rows:

        if not row.get("available"):
            body.append(
                "<tr><td>%s</td><td colspan='%d' class='unknown'>"
                "INSUFFICIENT DATA — %s</td></tr>"
                % (row["label"], len(columns),
                   (row.get("reason") or "")[:160])
            )
            continue

        cells = "".join("<td class='num'>%s</td>" % _fmt(row.get(key))
                        for key, _label, _size in columns)

        body.append("<tr><td>%s</td>%s</tr>" % (row["label"], cells))

    return ("<table><tr><th>%s</th>%s</tr>%s</table>"
            % (first, head, "".join(body)))


def _html_v2(out, payload):
    """The V2 report as one self-contained page. No external dependency.

    Carries the same three warnings the console does, above the numbers rather
    than below them: what a trade was assumed to cost, that the queue model is
    an approximation, and that the universe is survivors-only. A reader who
    scrolls to the equity curve first should still have met them.
    """

    result = out.get("result")

    frame = metrics_module.equity_frame(result.curve) if result is not None \
        else None

    charts = ""

    if frame is not None and not frame.empty:
        charts += _svg_chart("Equity curve (rials)",
                             list(frame["portfolio_value"]), fill=True)
        charts += _svg_chart("Drawdown (%)",
                             list(frame["drawdown"] * 100), colour="#dc2626",
                             fill=True, baseline=0)
        charts += _svg_chart("Cash (%)", list(frame["cash_pct"]),
                             colour="#0891b2", baseline=0)

    readiness = out.get("readiness") or {}
    coverage = readiness.get("fundamental_coverage") or {}
    news = readiness.get("news") or {}
    bias = readiness.get("survivorship") or {}

    recommendation = out.get("recommendation") or {}
    verdict = out.get("verdict") or {}

    sections = []

    for key, title, first in (("models", "Model comparison", "model"),
                              ("execution", "Execution sensitivity", "scenario"),
                              ("sizing", "Position sizing", "mode")):
        rows = (out.get(key) or {}).get("rows")
        if rows:
            sections.append("<h2>%s</h2>%s" % (title, _html_table(rows, first)))

    failure = out.get("failure") or {}

    failure_html = ""

    if failure.get("available"):
        failure_html = "<h2>Failure analysis</h2><ul>%s</ul>" % "".join(
            "<li>%s</li>" % f["finding"] for f in failure["findings"])

    coverage_html = "<p class='unknown'>not measurable</p>"

    if coverage.get("available"):
        coverage_html = (
            "<table>"
            "<tr><td>decisions with a point-in-time filing</td>"
            "<td class='num'>%.1f%%</td></tr>"
            "<tr><td>decisions measured</td><td class='num'>%s</td></tr>"
            "<tr><td>companies fully / partly / never covered</td>"
            "<td class='num'>%d / %d / %d</td></tr>"
            "<tr><td>target in the specification</td>"
            "<td class='num'>80.0%%</td></tr>"
            "</table>"
            % (coverage["decision_coverage"] * 100,
               "{:,}".format(coverage["decisions"]),
               coverage["companies_full"], coverage["companies_partial"],
               coverage["companies_none"])
        )

    return """<!doctype html>
<html lang="fa" dir="rtl"><head><meta charset="utf-8">
<title>SIRAT-AI Backtest V2</title>
<style>
 body{font-family:Tahoma,system-ui,sans-serif;margin:0;padding:24px;
      background:#f8fafc;color:#0f172a;line-height:1.6}
 main{max-width:1100px;margin:0 auto}
 h1{font-size:22px;margin:0 0 4px}
 h2{font-size:16px;margin:28px 0 8px;border-bottom:1px solid #e2e8f0;
    padding-bottom:4px}
 table{width:100%%;border-collapse:collapse;font-size:12px;direction:ltr}
 td,th{padding:5px 7px;border-bottom:1px solid #e2e8f0;text-align:left}
 .num{text-align:right;font-variant-numeric:tabular-nums}
 .pass{color:#15803d;font-weight:600}.fail{color:#b91c1c;font-weight:600}
 .unknown{color:#a16207;font-weight:600}
 .verdict{font-size:20px;font-weight:700;padding:12px;border-radius:8px;
          background:#fff;border:2px solid #cbd5e1;text-align:center}
 .warn{background:#fef2f2;border:1px solid #fecaca;padding:12px;
       border-radius:8px;margin:16px 0}
 figure{margin:16px 0;background:#fff;border:1px solid #e2e8f0;
        border-radius:8px;padding:8px}
 figcaption{font-size:12px;color:#475569;margin-bottom:4px;direction:ltr}
 .note{font-size:12px;color:#64748b;direction:ltr}
 pre{background:#fff;border:1px solid #e2e8f0;padding:12px;overflow:auto;
     font-size:11px;direction:ltr;max-height:520px}
</style></head><body><main>
<h1>SIRAT-AI Backtest V2</h1>
<p class="note">%(start)s .. %(end)s &nbsp;|&nbsp; round trip %(cost).3f%%
 &nbsp;|&nbsp; sizing %(sizing)s &nbsp;|&nbsp; stages: %(stages)s</p>

<div class="warn">
<b>Read before the numbers.</b>
Queues (صف) are an APPROXIMATION inferred from daily bars — this database
stores no order book. The universe is instruments listed today, so delisted
companies are absent from every historical window and every figure below is
optimistic by an unknown margin. Slippage defaults to 0%%, which is a refusal
to invent a number rather than a claim that trading is free; the execution
table prices being wrong about it.
</div>

<div class="verdict">%(recommendation)s</div>
<p class="note">FINAL VERDICT: %(verdict)s &nbsp;|&nbsp;
 OVERFITTING RISK: %(overfit)s</p>
<ul>%(reasons)s</ul>

<h2>Point-in-time fundamental coverage</h2>
%(coverage)s

<h2>News</h2>
<p>provider <b>%(news_provider)s</b> — %(news_state)s</p>
<p class="note">%(news_reason)s</p>

%(sections)s

<h2>Charts (headline model)</h2>
%(charts)s

<h2>Survivorship</h2>
<p class="note">%(bias)s</p>

%(failure)s

<h2>Full payload</h2>
<pre>%(json)s</pre>
</main></body></html>""" % {
        "start": out["start"],
        "end": out["end"],
        "cost": out["cfg"].round_trip_pct(),
        "sizing": out["cfg"].sizing_mode,
        "stages": ", ".join(out.get("stages_run") or ["—"]),
        "recommendation": recommendation.get("status", "—"),
        "verdict": verdict.get("verdict", "—"),
        "overfit": (out.get("overfit") or {}).get("risk", "—"),
        "reasons": "".join("<li>%s</li>" % r
                           for r in recommendation.get("reasons") or []),
        "coverage": coverage_html,
        "news_provider": news.get("provider", "—"),
        "news_state": ("available" if news.get("available")
                       else "<span class='fail'>INSUFFICIENT DATA</span>"),
        "news_reason": news.get("reason") or "",
        "sections": "".join(sections),
        "charts": charts,
        "bias": bias.get("verdict", "not measured"),
        "failure": failure_html,
        "json": json.dumps(payload, ensure_ascii=False, indent=2,
                           default=str).replace("<", "&lt;"),
    }


def print_v2_from_disk(path=None):
    """Re-print the headline numbers of the last V2 run."""

    path = path or os.path.join(backtest_config.OUTPUT_DIR,
                                "backtest_v2_report.json")

    if not os.path.exists(path):
        print("no V2 report at %s -- run `python run_v2.py` first" % path)
        return 1

    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)

    print()
    print("=" * 78)
    print("SIRAT-AI BACKTEST V2 (from %s)" % path)
    print("=" * 78)
    print("  %s .. %s" % (payload.get("start"), payload.get("end")))

    coverage = (payload.get("readiness") or {}).get("fundamental_coverage") or {}

    print("  fundamental coverage  %.1f%% of decisions"
          % ((coverage.get("decision_coverage") or 0) * 100))

    news = (payload.get("readiness") or {}).get("news") or {}

    print("  news                  %s"
          % ("available" if news.get("available") else "INSUFFICIENT DATA"))

    for key in ("models", "execution", "sizing"):
        section = payload.get(key) or {}
        rows = section.get("rows") or []
        if not rows:
            continue
        print()
        print("  " + key.upper())
        for row in rows:
            if not row.get("available"):
                print("     %-34s INSUFFICIENT DATA" % str(row["label"])[:34])
                continue
            print("     %-34s return %8s  Sharpe %6s  maxDD %7s  trades %4s"
                  % (str(row["label"])[:34], _fmt(row.get("total_return_pct")),
                     _fmt(row.get("sharpe")), _fmt(row.get("max_drawdown_pct")),
                     _fmt(row.get("trades"))))

    print()
    print("  OVERFITTING RISK: %s" % (payload.get("overfitting") or {}).get("risk"))
    print("  FINAL VERDICT:    %s" % (payload.get("verdict") or {}).get("verdict"))
    print("  RECOMMENDATION:   %s"
          % (payload.get("recommendation") or {}).get("status"))

    return 0


# ==============================
# CHARTS
# ==============================

def _svg_path(values, width, height, pad=8, baseline=None):
    """Polyline points for a series scaled into a box. No dependency."""

    clean = [v for v in values if v is not None and v == v]

    if len(clean) < 2:
        return "", 0, 0

    low = min(clean)
    high = max(clean)

    if baseline is not None:
        low = min(low, baseline)
        high = max(high, baseline)

    span = (high - low) or 1.0

    step = (width - 2 * pad) / float(len(clean) - 1)

    points = []

    for index, value in enumerate(clean):
        x = pad + index * step
        y = height - pad - (value - low) / span * (height - 2 * pad)
        points.append("%.1f,%.1f" % (x, y))

    return " ".join(points), low, high


def _svg_chart(title, values, colour="#2563eb", fill=False, baseline=None,
               width=880, height=200):

    points, low, high = _svg_path(values, width, height, baseline=baseline)

    if not points:
        return "<p>%s: not enough data to draw</p>" % title

    area = ""

    if fill:
        first_x = points.split()[0].split(",")[0]
        last_x = points.split()[-1].split(",")[0]
        area = ('<polygon points="%s %s,%d %s,%d" fill="%s" opacity="0.12"/>'
                % (points, last_x, height - 8, first_x, height - 8, colour))

    return """
<figure>
  <figcaption>%s</figcaption>
  <svg viewBox="0 0 %d %d" width="100%%" role="img" aria-label="%s">
    <rect width="%d" height="%d" fill="none"/>
    %s
    <polyline points="%s" fill="none" stroke="%s" stroke-width="1.6"/>
    <text x="8" y="14" font-size="11" fill="#64748b">max %.2f</text>
    <text x="8" y="%d" font-size="11" fill="#64748b">min %.2f</text>
  </svg>
</figure>""" % (title, width, height, title, width, height, area, points,
                colour, high, height - 4, low)


def _charts(result, directory, prefix):
    """PNGs, if matplotlib happens to be installed. Never required."""

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return []

    frame = metrics_module.equity_frame(result.curve)

    if frame.empty:
        return []

    written = []

    figure, axes = plt.subplots(3, 1, figsize=(10, 11), sharex=True)

    axes[0].plot(frame.index, frame["portfolio_value"], color="#2563eb")
    axes[0].set_title("Equity curve")
    axes[0].grid(alpha=0.3)

    axes[1].fill_between(frame.index, frame["drawdown"] * 100, 0,
                         color="#dc2626", alpha=0.35)
    axes[1].set_title("Drawdown %")
    axes[1].grid(alpha=0.3)

    axes[2].bar(frame.index, frame["daily_return"] * 100, color="#0f766e",
                width=1.0)
    axes[2].set_title("Daily returns %")
    axes[2].grid(alpha=0.3)

    figure.tight_layout()

    path = os.path.join(directory, prefix + "_charts.png")

    figure.savefig(path, dpi=backtest_config.CHART_DPI)

    plt.close(figure)

    written.append(path)

    return written


# ==============================
# HTML
# ==============================

def _html(result, payload):

    frame = metrics_module.equity_frame(result.curve)

    m = result.metrics or {}
    trades = m.get("trades") or {}

    charts = ""

    if not frame.empty:
        charts += _svg_chart("Equity curve (rials)",
                             list(frame["portfolio_value"]), fill=True)
        charts += _svg_chart("Drawdown (%)",
                             list(frame["drawdown"] * 100), colour="#dc2626",
                             fill=True, baseline=0)
        charts += _svg_chart("Daily returns (%)",
                             list(frame["daily_return"].fillna(0) * 100),
                             colour="#0f766e", baseline=0)

        benchmarks = result.benchmarks or {}

        if benchmarks.get("tedpix", {}).get("return_pct") is not None:
            strategy = list(frame["cumulative_return"] * 100)
            charts += _svg_chart(
                "Strategy cumulative return (%%) — شاخص کل returned %.2f%% "
                "over the same window"
                % benchmarks["tedpix"]["return_pct"],
                strategy, colour="#7c3aed", baseline=0)

    verdict = payload.get("verdict") or {}

    rows = "".join(
        "<tr><td>%s</td><td class='num'>%s</td><td class='num'>%s %s</td>"
        "<td class='%s'>%s</td></tr>"
        % (c["name"], _fmt(c["actual"]), c["compare"], c["required"],
           {True: "pass", False: "fail", None: "unknown"}[c["passed"]],
           {True: "PASS", False: "FAIL", None: "NOT MEASURED"}[c["passed"]])
        for c in verdict.get("checks", [])
    )

    insufficient = ""

    if result.insufficient:
        insufficient = "<div class='warn'><h2>INSUFFICIENT DATA</h2><ul>%s</ul></div>" % (
            "".join("<li>%s</li>" % line for line in result.insufficient)
        )

    return """<!doctype html>
<html lang="fa" dir="rtl"><head><meta charset="utf-8">
<title>SIRAT-AI Backtest — %(variant)s</title>
<style>
 body{font-family:Tahoma,system-ui,sans-serif;margin:0;padding:24px;
      background:#f8fafc;color:#0f172a;line-height:1.6}
 main{max-width:960px;margin:0 auto}
 h1{font-size:22px;margin:0 0 4px}
 h2{font-size:16px;margin:28px 0 8px;border-bottom:1px solid #e2e8f0;
    padding-bottom:4px}
 table{width:100%%;border-collapse:collapse;font-size:13px;direction:ltr}
 td,th{padding:6px 8px;border-bottom:1px solid #e2e8f0;text-align:left}
 .num{text-align:right;font-variant-numeric:tabular-nums}
 .pass{color:#15803d;font-weight:600}.fail{color:#b91c1c;font-weight:600}
 .unknown{color:#a16207;font-weight:600}
 .verdict{font-size:20px;font-weight:700;padding:12px;border-radius:8px;
          background:#fff;border:2px solid #cbd5e1;text-align:center}
 .warn{background:#fef2f2;border:1px solid #fecaca;padding:12px;
       border-radius:8px;margin:16px 0}
 figure{margin:16px 0;background:#fff;border:1px solid #e2e8f0;
        border-radius:8px;padding:8px}
 figcaption{font-size:12px;color:#475569;margin-bottom:4px;direction:ltr}
 .note{font-size:12px;color:#64748b;direction:ltr}
 pre{background:#fff;border:1px solid #e2e8f0;padding:12px;overflow:auto;
     font-size:12px;direction:ltr}
</style></head><body><main>
<h1>SIRAT-AI Backtest Report</h1>
<p class="note">%(start)s .. %(end)s &nbsp;|&nbsp; variant: %(variant)s
 &nbsp;|&nbsp; round trip %(cost).3f%%</p>

%(insufficient)s

<div class="verdict">FINAL VERDICT: %(verdict)s</div>

<h2>Assumptions</h2>
<p class="note">Queues (صف) are an APPROXIMATION inferred from daily bars — this
database stores no order book. The universe is instruments listed today, so
delisted companies are absent from every historical window and every figure
below is optimistic by an unknown margin.</p>

<h2>Headline</h2>
<table>
<tr><td>Initial capital</td><td class="num">%(initial)s</td></tr>
<tr><td>Final capital</td><td class="num">%(final)s</td></tr>
<tr><td>Total return</td><td class="num">%(total)s%%</td></tr>
<tr><td>CAGR</td><td class="num">%(cagr)s%%</td></tr>
<tr><td>Max drawdown</td><td class="num">%(dd)s%%</td></tr>
<tr><td>Sharpe / Sortino / Calmar</td><td class="num">%(sharpe)s / %(sortino)s / %(calmar)s</td></tr>
<tr><td>Trades / win rate / profit factor</td>
    <td class="num">%(trades)s / %(win)s%% / %(pf)s</td></tr>
</table>

<h2>Charts</h2>
%(charts)s

<h2>PASS / FAIL</h2>
<table><tr><th>criterion</th><th class="num">actual</th>
<th class="num">required</th><th>result</th></tr>%(rows)s</table>

<h2>Full payload</h2>
<pre>%(json)s</pre>
</main></body></html>""" % {
        "variant": result.variant,
        "start": result.start,
        "end": result.end,
        "cost": result.cfg.round_trip_pct(),
        "insufficient": insufficient,
        "verdict": verdict.get("verdict", "—"),
        "initial": _rials(m.get("initial_capital")),
        "final": _rials(m.get("final_capital")),
        "total": _fmt(m.get("total_return_pct")),
        "cagr": _fmt(m.get("cagr_pct")),
        "dd": _fmt((m.get("drawdown") or {}).get("max_drawdown_pct")),
        "sharpe": _fmt(m.get("sharpe")),
        "sortino": _fmt(m.get("sortino")),
        "calmar": _fmt(m.get("calmar")),
        "trades": trades.get("trades", "—"),
        "win": _fmt(trades.get("win_rate_pct")),
        "pf": _fmt(trades.get("profit_factor")),
        "charts": charts,
        "rows": rows,
        "json": json.dumps(payload, ensure_ascii=False, indent=2,
                           default=str).replace("<", "&lt;"),
    }
