"""SIRAT-AI Backtest V2 — one command, every comparison, one verdict.

WHAT V2 IS FOR
--------------
V1 answered "does the technical model make money" and answered it FAIL. V2 asks
a bigger question and is built so that the answer can still be no:

    does Technical + Fundamental + News + Risk Management + Realistic
    Execution do meaningfully better than Technical alone?

Everything here is arranged so that question is answered with evidence rather
than with a parameter search. In particular, and this is the rule the whole
file is organised around: NOTHING IN V2 TUNES A PARAMETER ON THE RESULT. No
value in backtest_config.py was chosen by running the backtest at several
settings and keeping the best one. The sensitivity sweep and the walk-forward
exist to price the parameters that already exist, not to pick new ones.

WHAT IT RUNS, IN ORDER
----------------------
    0. DATA READINESS   point-in-time fundamental coverage, news availability,
                        survivorship exposure. Printed BEFORE any return, so a
                        reader knows what the numbers rest on before seeing them.
    1. MODELS           A technical / B + fundamental / C + news
    2. EXECUTION        0% / 0.25% / 0.50% slippage, as whole backtests
    3. SIZING           risk-based / equal / fixed-fractional / volatility
    4. VALIDATION       out-of-sample, walk-forward, Monte Carlo, sensitivity
    5. VERDICT          PASS criteria, overfitting band, and one of the four
                        recommendations

USAGE
-----
    python run_v2.py                          the full suite
    python run_v2.py --quick                  models only, no validation
    python run_v2.py --limit 80               a fast smoke test
    python run_v2.py --start 2024-09-01 --end 2026-08-11
    python run_v2.py --stages models execution
    python run_v2.py --report                 re-print the last written report
"""

import argparse
import json
import os
import sys
import time

import pandas as pd

import backtest_config
import config
import news as news_module
from backtesting import (
    engine,
    report as report_module,
    scenarios,
    survivorship,
    validation,
)
from backtesting.pit import PointInTimeData
from backtesting.strategy import TECHNICAL_ONLY


ALL_STAGES = ("models", "execution", "sizing", "oos", "walkforward",
              "montecarlo", "sensitivity")


# ==============================
# THE V1 BASELINE
# ==============================
#
# Read from the artefact V1 actually wrote, so the comparison is against a run
# rather than against a memory of one. The figures below are the fallback, and
# they are the ones stated in the V2 request; they are used ONLY when no V1
# report is on disk, and the report says which source it used.

V1_REPORTED = {
    "source": "as stated in the V2 request (no backtest_report.json on disk)",
    "total_return_pct": 8.95,
    "cagr_pct": 4.51,
    "max_drawdown_pct": -13.87,
    "sharpe": -1.69,
    "sortino": -2.41,
    "profit_factor": 1.14,
    "win_rate_pct": 50.4,
    "trades": 137,
    "average_holding_days": 16.9,
    "average_cash_pct": 58.6,
    "average_positions": 3.1,
    "final_capital": 1_089_500_000,
    "overfitting": "MEDIUM",
    "overfitting_score": 43.3,
    "verdict": "FAIL",
    "fundamental_coverage": 0.127,
}


def v1_baseline(path=None):
    """V1's numbers, preferring the report it wrote to the ones we were told."""

    path = path or os.path.join(backtest_config.OUTPUT_DIR,
                                "backtest_report.json")

    if not os.path.exists(path):
        return dict(V1_REPORTED)

    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception:
        return dict(V1_REPORTED)

    metrics = payload.get("metrics") or {}

    if not metrics:
        return dict(V1_REPORTED)

    trades = metrics.get("trades") or {}
    drawdown = metrics.get("drawdown") or {}
    exposure = metrics.get("exposure") or {}
    diagnostics = payload.get("diagnostics") or {}

    return {
        "source": path,
        "total_return_pct": metrics.get("total_return_pct"),
        "cagr_pct": metrics.get("cagr_pct"),
        "max_drawdown_pct": drawdown.get("max_drawdown_pct"),
        "sharpe": metrics.get("sharpe"),
        "sortino": metrics.get("sortino"),
        "calmar": metrics.get("calmar"),
        "profit_factor": trades.get("profit_factor"),
        "win_rate_pct": trades.get("win_rate_pct"),
        "trades": trades.get("trades"),
        "average_holding_days": trades.get("average_holding_days"),
        "average_cash_pct": exposure.get("average_cash_pct"),
        "average_positions": exposure.get("average_positions"),
        "final_capital": metrics.get("final_capital"),
        "overfitting": (payload.get("overfitting") or {}).get("risk"),
        "overfitting_score": (payload.get("overfitting") or {}).get("score"),
        "verdict": (payload.get("verdict") or {}).get("verdict"),
        "fundamental_coverage": diagnostics.get("fundamental_coverage"),
    }


# ==============================
# STAGE 0: WHAT THE DATA CAN SUPPORT
# ==============================

def data_readiness(cfg, data, start, end, dates, verbose=True):
    """Coverage, news and survivorship, measured before any return is shown.

    Deliberately first. A reader who sees "+31%" before seeing "the fundamental
    model had real data on 14% of decisions" has been handed a number they
    cannot judge, and by the time they reach the caveat they have already
    formed a view.
    """

    if verbose:
        print()
        print("=" * 78)
        print("0. WHAT THE DATA CAN SUPPORT")
        print("=" * 78, flush=True)

    codes = sorted(data.instruments())

    if cfg.universe_limit:
        codes = codes[:int(cfg.universe_limit)]

    coverage = data.fundamental_coverage_report(dates, codes)

    news = news_module.status(getattr(cfg, "news_provider", None))

    bias = survivorship.measure(data, start, end, cfg=cfg)

    if verbose:
        _print_readiness(coverage, news, bias)

    return {"fundamental_coverage": coverage, "news": news,
            "survivorship": bias}


def _print_readiness(coverage, news, bias):

    print()
    print("FUNDAMENTAL — point-in-time coverage")
    print("-" * 78)

    if coverage.get("available"):
        print("  source                 %s" % coverage.get("source"))
        print("  decisions              %s across %s dates and %s companies"
              % ("{:,}".format(coverage["decisions"]), coverage["dates"],
                 coverage["universe"]))
        print("  WITH a real filing     %s  (%.1f%%)"
              % ("{:,}".format(coverage["decisions_covered"]),
                 coverage["decision_coverage"] * 100))
        print("  companies fully covered %s   partly %s   never %s"
              % (coverage["companies_full"], coverage["companies_partial"],
                 coverage["companies_none"]))
        print()
        print("  by year:")
        for row in coverage["by_year"]:
            print("     %d   %6.1f%%   (%d decision dates)"
                  % (row["year"], row["coverage"] * 100, row["decision_dates"]))
        print()
        print("  by decision date:")
        for row in coverage["by_date"]:
            print("     %s   %6.1f%%   %d/%d"
                  % (row["date"], row["coverage"] * 100, row["covered"],
                     row["universe"]))
        filings = coverage.get("filings") or {}
        if filings:
            print()
            print("  filing store: %s rows, %s dated, %s undated, %s unplaceable"
                  % (filings.get("rows"), filings.get("dated"),
                     filings.get("undated"), filings.get("unplaceable")))
    else:
        print("  not measurable: %s" % coverage.get("reason"))

    print()
    print("NEWS")
    print("-" * 78)
    print("  provider    %s" % news.get("provider"))
    print("  available   %s" % ("yes" if news.get("available") else "NO"))
    if not news.get("available"):
        print("  NEWS = INSUFFICIENT DATA")
        for line in _wrap(news.get("reason") or "", 72):
            print("     " + line)

    print()
    print("SURVIVORSHIP")
    print("-" * 78)

    if bias.get("available"):
        print("  universe listed today    %d" % bias["universe_today"])
        print("  stopped trading in window %d (%.1f%%)"
              % (bias["stopped_trading"], (bias["stopped_share"] or 0) * 100))
        print("  entered mid-window        %d (%.1f%%)"
              % (bias["entered_mid_window"], (bias["entered_share"] or 0) * 100))
        print("  mean window return, all instruments        %s%%"
              % bias["mean_return_all_pct"])
        print("  mean window return, still-trading only     %s%%"
              % bias["mean_return_survivors_pct"])
        print("  VISIBLE bias, lower bound                  %s pp"
              % bias["visible_bias_lower_bound_pp"])
        print()
        for line in _wrap(bias["unmeasurable"], 72):
            print("     " + line)
    else:
        print("  not measurable: %s" % bias.get("reason"))


def _wrap(text, width):
    words = str(text).split()
    lines, current = [], ""
    for word in words:
        if len(current) + len(word) + 1 > width:
            lines.append(current)
            current = word
        else:
            current = (current + " " + word).strip()
    if current:
        lines.append(current)
    return lines


# ==============================
# THE RUN
# ==============================

def run(start=None, end=None, cfg=None, stages=None, limit=None, verbose=True,
        write=True):
    """The whole V2 suite. Returns everything it computed."""

    cfg = cfg or backtest_config.load()

    if limit:
        cfg = cfg.replace(universe_limit=limit)

    stages = set(ALL_STAGES if stages is None else stages)

    data = PointInTimeData(cfg, verbose=verbose)

    calendar = data.calendar()

    if len(calendar) == 0:
        print("no trading calendar: the index history is empty. "
              "Run `python seed_import.py --indices` first.")
        return None

    start = pd.Timestamp(start or backtest_config.DEFAULT_START)
    end = pd.Timestamp(end) if end else calendar[-1]

    started = time.time()

    if verbose:
        print("=" * 78)
        print("SIRAT-AI BACKTEST V2   %s .. %s" % (start.date(), end.date()))
        print("=" * 78)
        print("  universe          %d equities%s"
              % (len(data.instruments()),
                 " (capped at %d)" % cfg.universe_limit
                 if cfg.universe_limit else ""))
        print("  rebalance         every %d sessions" % cfg.rebalance_sessions)
        print("  round trip cost   %.3f%%" % cfg.round_trip_pct())
        print("  sizing            %s" % cfg.sizing_mode)
        print("  stages            %s" % ", ".join(sorted(stages)))

    dates = data.rebalance_dates(start, end, cfg.rebalance_sessions)

    out = {
        "start": start, "end": end, "cfg": cfg,
        "stages_run": [], "v1": v1_baseline(),
    }

    # ---- 0. readiness ----------------------------------------------------
    out["readiness"] = data_readiness(cfg, data, start, end, dates,
                                      verbose=verbose)

    # ---- 1. models -------------------------------------------------------
    panel = None

    if "models" in stages:
        if verbose:
            print()
            print("=" * 78)
            print("1. MODELS — what each evidence set contributes")
            print("=" * 78, flush=True)

        out["models"] = scenarios.models(cfg, data, start, end, verbose=verbose)
        out["stages_run"].append("models")

        panels = out["models"].get("panels") or {}
        panel = panels.get(TECHNICAL_ONLY)

    if panel is None:
        panel = validation.master_panel(cfg, data, start, end, TECHNICAL_ONLY,
                                        verbose=verbose)

    master, master_dates = panel

    # The headline result is the technical model, because that is the one V1
    # measured -- comparing V2's fundamental model against V1's technical one
    # would credit the fundamentals with every plumbing change as well.
    headline = (out.get("models", {}).get("results", {}).get("A")
                or engine.run(start, end, cfg=cfg, variant=TECHNICAL_ONLY,
                              data=data, panel=master,
                              rebalance_dates=master_dates, verbose=verbose))

    out["result"] = headline

    if not headline.available:
        if verbose:
            print()
            print("HEADLINE RESULT UNAVAILABLE")
            for line in headline.insufficient:
                print("  * " + line)
        return out

    # ---- 2. execution ----------------------------------------------------
    if "execution" in stages:
        if verbose:
            print()
            print("=" * 78)
            print("2. EXECUTION — the cost of being wrong about slippage")
            print("=" * 78, flush=True)

        out["execution"] = scenarios.execution_scenarios(
            cfg, data, start, end, panel=master, dates=master_dates,
            verbose=verbose)
        out["stages_run"].append("execution")

    # ---- 3. sizing -------------------------------------------------------
    if "sizing" in stages:
        if verbose:
            print()
            print("=" * 78)
            print("3. POSITION SIZING — does the sizing rule earn its keep")
            print("=" * 78, flush=True)

        out["sizing"] = scenarios.sizing_scenarios(
            cfg, data, start, end, panel=master, dates=master_dates,
            verbose=verbose)
        out["stages_run"].append("sizing")

    # ---- 4. validation ---------------------------------------------------
    oos = folds = monte = sweep = None

    if "oos" in stages:
        if verbose:
            print()
            print("-- out-of-sample --", flush=True)
        oos = validation.out_of_sample(cfg, data, start, end,
                                       variant=TECHNICAL_ONLY, panel=master,
                                       dates=master_dates, verbose=verbose)
        out["stages_run"].append("oos")

    if "walkforward" in stages:
        if verbose:
            print()
            print("-- walk-forward --", flush=True)
        folds = validation.walk_forward(cfg, data, start, end,
                                        variant=TECHNICAL_ONLY, panel=master,
                                        dates=master_dates, verbose=verbose)
        out["stages_run"].append("walkforward")

    if "montecarlo" in stages:
        if verbose:
            print()
            print("-- monte carlo --", flush=True)
        monte = validation.monte_carlo(headline)
        out["stages_run"].append("montecarlo")

    if "sensitivity" in stages:
        if verbose:
            print()
            print("-- sensitivity --", flush=True)
        sweep = validation.sensitivity(cfg, data, start, end,
                                       variant=TECHNICAL_ONLY, panel=master,
                                       dates=master_dates, verbose=verbose)
        out["stages_run"].append("sensitivity")

    overfit = validation.overfitting_risk(result=headline, oos=oos, sweep=sweep,
                                          folds=folds)

    verdict = report_module.evaluate(headline, oos=oos, overfit=overfit)

    out.update({
        "oos": oos, "folds": folds, "monte": monte, "sweep": sweep,
        "overfit": overfit, "verdict": verdict,
        "failure": scenarios.failure_analysis(headline),
        "recommendation": scenarios.recommendation(
            verdict, overfit, headline,
            coverage=out["readiness"]["fundamental_coverage"],
            news=out["readiness"]["news"],
        ),
        "seconds": round(time.time() - started, 1),
    })

    if verbose:
        report_module.print_v2(out)

    if write:
        out["files"] = report_module.write_v2(out)

        if verbose:
            print()
            print("-" * 78)
            print("FILES WRITTEN")
            print("-" * 78)
            for path in out["files"]:
                print("  " + path)
            print()
            print("  completed in %.1fs" % out["seconds"])

    return out


# ==============================
# CLI
# ==============================

def main(argv=None):

    parser = argparse.ArgumentParser(description="SIRAT-AI Backtest V2")

    parser.add_argument("--start", default=None, help="first session (YYYY-MM-DD)")
    parser.add_argument("--end", default=None, help="last session (YYYY-MM-DD)")
    parser.add_argument("--limit", type=int, default=None,
                        help="cap the universe (for a fast smoke test)")
    parser.add_argument("--quick", action="store_true",
                        help="models only; skip every validation stage")
    parser.add_argument("--stages", nargs="*", default=None, choices=ALL_STAGES,
                        help="run only these stages")
    parser.add_argument("--config", default=None,
                        help="path to a backtest_config.json")
    parser.add_argument("--restrict-fundamental-universe", action="store_true",
                        dest="restrict",
                        help="score only companies that have filed at all. "
                             "Makes a fundamental model measurable when "
                             "universe-wide coverage is too thin to report -- "
                             "but the covered subset skews large and "
                             "well-followed, so the result describes that "
                             "subset and NOT the market")
    parser.add_argument("--no-write", action="store_true", dest="no_write")
    parser.add_argument("--report", action="store_true",
                        help="re-print the last written V2 report")
    parser.add_argument("--quiet", action="store_true")

    args = parser.parse_args(argv)

    if args.report:
        return report_module.print_v2_from_disk()

    cfg = backtest_config.load(args.config)

    if args.restrict:
        cfg = cfg.replace(restrict_to_fundamental_universe=True)

    stages = set(args.stages) if args.stages else None

    if args.quick:
        stages = {"models"}

    out = run(start=args.start, end=args.end, cfg=cfg, stages=stages,
              limit=args.limit, verbose=not args.quiet,
              write=not args.no_write)

    if out is None:
        return 1

    # A FAIL verdict is a successful run of the program. Exit non-zero only
    # when the engine could not produce a result at all, so a scheduler can
    # tell "the strategy is bad" from "the run broke".
    return 0 if out.get("result") and out["result"].available else 2


if __name__ == "__main__":
    sys.exit(main())
