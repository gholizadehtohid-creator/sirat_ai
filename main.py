"""CLI entry point: market scan, backtest, validation suite, paper trading.

The scan is still what `python main.py` does with no arguments, so nothing that
used to work stopped working. Everything added here is behind a flag.

    python main.py                      scan the configured universe
    python main.py --backtest           full backtest + validation suite
    python main.py --backtest --quick   backtest only, no validation stages
    python main.py --backtest-v2        the V2 suite: models A/B/C, slippage
                                        scenarios, sizing comparison, coverage,
                                        survivorship and a recommendation
    python main.py --paper              advance the paper account one session
    python main.py --paper --report     print the paper blotter without trading
    python main.py --backtest-report    re-print the last written report
"""

import argparse
import json
import os
import sys


def _scan(args):

    from config import STOCK_LIST
    from scanner import scan_market

    symbols = args.symbols or STOCK_LIST

    print("=" * 60)
    print("SIRAT AI STOCK SCANNER")
    print("=" * 60)

    results = scan_market(
        symbols,
        force_refresh=args.refresh,
        verbose=not args.quiet
    )

    print()
    print("=" * 60)
    print("RANKING")
    print("=" * 60)
    print(
        f"{'#':<4}{'SYMBOL':<10}{'SCORE':<8}{'RSI':<9}"
        f"{'TREND':<17}{'SOURCE':<13}{'STATUS'}"
    )
    print("-" * 60)

    for row in results:

        rsi = f"{row['rsi']:.1f}" if row["rsi"] is not None else "n/a"

        print(
            f"{row['rank']:<4}{row['symbol']:<10}{row['score']:<8}"
            f"{rsi:<9}{row['trend']:<17}{row['source']:<13}{row['status']}"
        )

    stale = [r for r in results if r["source"] in ("stale-cache", "unavailable")]

    if stale:
        print()
        print(
            f"NOTE: {len(stale)} symbol(s) could not reach TSETMC and were "
            "scored from cached bars. Scores may not reflect today's market."
        )

    print()
    print("=" * 60)
    print("SCAN COMPLETE")
    print("=" * 60)

    return 0


def _backtest(args):

    import backtest_config
    from backtesting import runner

    cfg = backtest_config.load(args.config)

    overrides = {}

    if args.limit:
        overrides["universe_limit"] = args.limit

    if args.rebalance:
        overrides["rebalance_sessions"] = args.rebalance

    if args.capital:
        overrides["initial_capital"] = args.capital

    if args.no_queues:
        overrides["model_queues"] = False

    if args.slippage is not None:
        overrides["slippage_pct"] = args.slippage

    if overrides:
        cfg = cfg.replace(**overrides)

    stages = None

    if args.quick:
        stages = set()
    elif args.stages:
        stages = set(args.stages)

    out = runner.full_run(
        start=args.start, end=args.end, cfg=cfg, variant=args.variant,
        stages=stages, verbose=not args.quiet, write=not args.no_write,
    )

    if out is None:
        return 1

    # A FAIL verdict is a successful run of the program: the point of the
    # exercise is to be told no. Exit non-zero only when the engine could not
    # produce a result at all, so a scheduler can tell "the strategy is bad"
    # from "the run broke".
    return 0 if out["result"].available else 2


def _backtest_v2(args):
    """The V2 suite. A thin wrapper: run_v2.py holds the orchestration.

    Kept behind its own flag rather than folded into --backtest so that the V1
    command keeps producing exactly the run it always produced. Anyone
    comparing the two is then comparing results, not a changed default.
    """

    import backtest_config
    import run_v2

    cfg = backtest_config.load(args.config)

    stages = {"models"} if args.quick else (
        set(args.v2_stages) if args.v2_stages else None)

    out = run_v2.run(start=args.start, end=args.end, cfg=cfg, stages=stages,
                     limit=args.limit, verbose=not args.quiet,
                     write=not args.no_write)

    if out is None:
        return 1

    return 0 if out.get("result") and out["result"].available else 2


def _paper(args):

    from backtesting import paper

    if args.report:
        paper.print_report(args.account)
        return 0

    trader = paper.PaperTrader(account=args.account, variant=args.variant,
                               verbose=not args.quiet, engine=args.engine)

    summary = trader.advance(until=args.until, max_sessions=args.max_sessions)

    if not summary.get("available"):
        print("paper trading unavailable: %s" % summary.get("reason"))
        return 1

    print()
    print("=" * 78)
    print("PAPER RUN — no order was sent")
    print("=" * 78)
    print("  account            %s" % summary["account"])
    print("  engine             %s" % summary.get("engine"))
    print("  sessions processed %s" % summary["processed"])

    if summary.get("reason"):
        print("  %s" % summary["reason"])

    print("  fills              %s" % summary.get("filled", 0))
    print("  exits              %s" % summary.get("exits", 0))
    print("  orders raised      %s" % summary.get("orders_raised", 0))
    print("  portfolio value    {:,.0f}".format(summary.get("portfolio_value") or 0))

    paper.print_report(args.account)

    return 0


def _backtest_report(args):

    import backtest_config

    path = args.path or os.path.join(
        backtest_config.OUTPUT_DIR, "backtest_report.json")

    if not os.path.exists(path):
        print("no report at %s -- run `python main.py --backtest` first" % path)
        return 1

    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)

    print()
    print("=" * 78)
    print("SIRAT-AI BACKTEST REPORT (from %s)" % path)
    print("=" * 78)
    print("  %s .. %s   variant %s"
          % (payload.get("start"), payload.get("end"), payload.get("variant")))

    if payload.get("insufficient_data"):
        print()
        print("  INSUFFICIENT DATA")
        for line in payload["insufficient_data"]:
            print("    * " + line)

    metrics = payload.get("metrics") or {}
    trades = metrics.get("trades") or {}

    for label, value in (
        ("total return %", metrics.get("total_return_pct")),
        ("CAGR %", metrics.get("cagr_pct")),
        ("max drawdown %", (metrics.get("drawdown") or {}).get("max_drawdown_pct")),
        ("sharpe", metrics.get("sharpe")),
        ("sortino", metrics.get("sortino")),
        ("calmar", metrics.get("calmar")),
        ("trades", trades.get("trades")),
        ("win rate %", trades.get("win_rate_pct")),
        ("profit factor", trades.get("profit_factor")),
    ):
        print("  %-20s %s" % (label, value))

    overfit = payload.get("overfitting") or {}
    verdict = payload.get("verdict") or {}

    print()
    print("  OVERFITTING RISK: %s" % overfit.get("risk", "—"))
    print("  FINAL VERDICT:    %s" % verdict.get("verdict", "—"))

    html = os.path.join(os.path.dirname(path), "backtest_report.html")

    if os.path.exists(html):
        print()
        print("  full report: %s" % html)

    return 0


def main(argv=None):

    parser = argparse.ArgumentParser(
        description="SIRAT AI — scanner, backtester and paper trader"
    )

    mode = parser.add_argument_group("mode")
    mode.add_argument("--backtest", action="store_true",
                      help="run the backtest and validation suite")
    mode.add_argument("--backtest-v2", action="store_true", dest="backtest_v2",
                      help="run the V2 suite (models A/B/C, slippage "
                           "scenarios, sizing comparison, coverage report)")
    mode.add_argument("--paper", action="store_true",
                      help="advance the paper-trading account")
    mode.add_argument("--backtest-report", action="store_true",
                      dest="backtest_report",
                      help="print the last written backtest report")

    scan = parser.add_argument_group("scan")
    scan.add_argument("--symbols", nargs="+", default=None,
                      help="symbols to scan (default: the configured universe)")
    scan.add_argument("--refresh", action="store_true",
                      help="force a network refresh instead of cached bars")

    back = parser.add_argument_group("backtest")
    back.add_argument("--start", default=None, help="first session (YYYY-MM-DD)")
    back.add_argument("--end", default=None, help="last session (YYYY-MM-DD)")
    back.add_argument("--variant", default="technical",
                      choices=["technical", "fundamental",
                               "technical+fundamental",
                               "technical+fundamental+news"],
                      help="which evidence set the strategy may use")
    back.add_argument("--quick", action="store_true",
                      help="backtest only; skip every validation stage")
    back.add_argument("--stages", nargs="*", default=None,
                      choices=["oos", "walkforward", "montecarlo",
                               "sensitivity", "variants"],
                      help="run only these validation stages")
    back.add_argument("--limit", type=int, default=None,
                      help="cap the universe (for a fast smoke test)")
    back.add_argument("--rebalance", type=int, default=None,
                      help="sessions between rebalances")
    back.add_argument("--capital", type=float, default=None,
                      help="initial capital in rials")
    back.add_argument("--slippage", type=float, default=None,
                      help="price concession per side, in percent")
    back.add_argument("--no-queues", action="store_true", dest="no_queues",
                      help="disable the صف model (makes results optimistic)")
    back.add_argument("--config", default=None,
                      help="path to a backtest_config.json")
    back.add_argument("--no-write", action="store_true", dest="no_write",
                      help="do not write CSV/JSON/HTML output")
    back.add_argument("--path", default=None,
                      help="report file to print with --backtest-report")
    back.add_argument("--v2-stages", nargs="*", default=None, dest="v2_stages",
                      choices=["models", "execution", "sizing", "oos",
                               "walkforward", "montecarlo", "sensitivity"],
                      help="run only these V2 stages")

    trade = parser.add_argument_group("paper")
    trade.add_argument("--account", default="default",
                       help="paper account name")
    trade.add_argument("--report", action="store_true",
                       help="print the blotter without advancing")
    trade.add_argument("--until", default=None,
                       help="advance no further than this session")
    trade.add_argument("--max-sessions", type=int, default=20,
                       dest="max_sessions",
                       help="most sessions to process in one run")
    trade.add_argument("--engine", default=None, choices=["v1", "v2"],
                       help="which decision engine raises the orders "
                            "(default: config.DECISION_ENGINE)")

    parser.add_argument("--quiet", action="store_true",
                        help="suppress progress output")

    args = parser.parse_args(argv)

    if args.backtest_v2:
        return _backtest_v2(args)

    if args.backtest:
        return _backtest(args)

    if args.paper:
        return _paper(args)

    if args.backtest_report:
        return _backtest_report(args)

    return _scan(args)


if __name__ == "__main__":
    sys.exit(main())
