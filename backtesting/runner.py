"""One call that runs the whole validation suite and writes every output.

The order is the order a sceptic would ask for:

    1. the backtest itself
    2. split it: in-sample, validation, out-of-sample
    3. walk forward, choosing the parameter on data it does not then measure on
    4. resample the trades thousands of times
    5. move every parameter one step and see what survives
    6. run the same window on each evidence set (technical / fundamental / both)
    7. combine 2-6 into an overfitting verdict
    8. check the whole thing against the PASS criteria

Steps 2-6 are individually skippable, because each costs real time and a quick
answer is sometimes the useful one. Nothing is skipped silently: the report
prints which stages ran, and the overfitting verdict states what share of its
model could be evaluated on the evidence that exists.
"""

import time

import pandas as pd

import backtest_config
from backtesting import engine, report as report_module, validation
from backtesting.pit import PointInTimeData
from backtesting.strategy import (
    FUNDAMENTAL_ONLY,
    TECHNICAL_FUNDAMENTAL,
    TECHNICAL_FUNDAMENTAL_NEWS,
    TECHNICAL_ONLY,
)


def resolve_window(data, start=None, end=None):
    """Fill in whichever end of the window was not given."""

    calendar = data.calendar()

    if len(calendar) == 0:
        return None, None

    start = pd.Timestamp(start or backtest_config.DEFAULT_START)

    end = pd.Timestamp(end) if end else calendar[-1]

    return start, end


def full_run(start=None, end=None, cfg=None, variant=TECHNICAL_ONLY,
             stages=None, verbose=True, write=True, prefix="backtest"):
    """Run the suite. Returns everything it computed, keyed by stage."""

    cfg = cfg or backtest_config.load()

    # `None` means "all of them"; an EMPTY set means "none of them", and the
    # difference matters -- `or` would quietly turn --quick into a full run.
    if stages is None:
        stages = {"oos", "walkforward", "montecarlo", "sensitivity", "variants"}
    else:
        stages = set(stages)

    data = PointInTimeData(cfg, verbose=verbose)

    start, end = resolve_window(data, start, end)

    if start is None:
        print("no trading calendar: the index history is empty. "
              "Run `python seed_import.py --indices` first.")
        return None

    started = time.time()

    if verbose:
        print("=" * 78)
        print("SIRAT-AI BACKTEST  %s .. %s" % (start.date(), end.date()))
        print("=" * 78)

    # ---- the shared panel ------------------------------------------------
    panel, dates = validation.master_panel(cfg, data, start, end, variant,
                                           verbose=verbose)

    result = engine.run(start, end, cfg=cfg, variant=variant, data=data,
                        panel=panel, rebalance_dates=dates, verbose=verbose)

    out = {"result": result, "start": start, "end": end, "variant": variant,
           "cfg": cfg, "stages_run": ["backtest"]}

    if not result.available:
        if verbose:
            report_module.print_report(result)
        return out

    oos = folds = monte = sweep = variants = None

    # The two stages that CHOOSE a parameter value stop before the sealed
    # holdout. They used to sweep start..end, so 2026 helped pick the gate in
    # the later walk-forward windows and set the sensitivity sweep's
    # best_value -- and the out-of-sample stage then "validated" on the same
    # sessions. The holdout is for measuring, and only out_of_sample reads it.
    tune_end = min(pd.Timestamp(end),
                   pd.Timestamp(backtest_config.OUT_OF_SAMPLE_START)
                   - pd.Timedelta(days=1))
    can_tune = tune_end > pd.Timestamp(start)
    tune_end = str(tune_end.date())

    if "oos" in stages:
        if verbose:
            print()
            print("-- out-of-sample split --")
        oos = validation.out_of_sample(cfg, data, start, end, variant=variant,
                                       panel=panel, dates=dates, verbose=verbose)
        out["stages_run"].append("out_of_sample")

    if "walkforward" in stages:
        if verbose:
            print()
            print("-- walk-forward --")
        if can_tune:
            folds = validation.walk_forward(cfg, data, start, tune_end,
                                            variant=variant, panel=panel,
                                            dates=dates, verbose=verbose)
            out["stages_run"].append("walk_forward")
        elif verbose:
            print("   skipped: the whole window is inside the holdout")

    if "montecarlo" in stages:
        if verbose:
            print()
            print("-- monte carlo --")
        monte = validation.monte_carlo(result)
        out["stages_run"].append("monte_carlo")

    if "sensitivity" in stages:
        if verbose:
            print()
            print("-- sensitivity --")
        if can_tune:
            sweep = validation.sensitivity(cfg, data, start, tune_end,
                                           variant=variant, panel=panel,
                                           dates=dates, verbose=verbose)
            out["stages_run"].append("sensitivity")
        elif verbose:
            print("   skipped: the whole window is inside the holdout")

    if "variants" in stages:
        if verbose:
            print()
            print("-- evidence-set comparison --")
        by_variant = validation.variant_comparison(cfg, data, start, end,
                                                   verbose=verbose)
        variants = [
            ("Technical only", by_variant.get(TECHNICAL_ONLY)),
            ("Fundamental only", by_variant.get(FUNDAMENTAL_ONLY)),
            ("Technical + Fundamental", by_variant.get(TECHNICAL_FUNDAMENTAL)),
            ("Technical + Fundamental + News",
             by_variant.get(TECHNICAL_FUNDAMENTAL_NEWS)),
        ]
        out["by_variant"] = by_variant
        out["stages_run"].append("variants")

    overfit = validation.overfitting_risk(result=result, oos=oos, sweep=sweep,
                                          folds=folds)

    verdict = report_module.evaluate(result, oos=oos, overfit=overfit)

    out.update({
        "oos": oos, "folds": folds, "monte": monte, "sweep": sweep,
        "variants": variants, "overfit": overfit, "verdict": verdict,
        "seconds": round(time.time() - started, 1),
    })

    if verbose:
        report_module.print_report(result, oos=oos, overfit=overfit,
                                   monte=monte, sweep=sweep, folds=folds,
                                   variants=variants, verdict=verdict)

    if write:
        written = report_module.write_all(
            result, oos=oos, overfit=overfit, monte=monte, sweep=sweep,
            folds=folds, variants=variants, verdict=verdict, prefix=prefix)

        out["files"] = written

        if verbose:
            print()
            print("-" * 78)
            print("FILES WRITTEN")
            print("-" * 78)
            for path in written:
                print("  " + path)
            print()
            print("  completed in %.1fs" % out["seconds"])

    return out


def quick_run(start=None, end=None, cfg=None, variant=TECHNICAL_ONLY,
              verbose=True, write=True):
    """Just the backtest, no validation. For iterating on the strategy."""

    return full_run(start=start, end=end, cfg=cfg, variant=variant,
                    stages=set(), verbose=verbose, write=write,
                    prefix="backtest_quick")
