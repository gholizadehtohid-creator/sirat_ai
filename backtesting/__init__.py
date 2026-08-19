"""Backtesting and validation engine for SIRAT AI.

WHAT THIS IS FOR
----------------
Everything else in this project decides what to buy. This package decides
whether that decision has ever been worth anything, and it is built to be
hostile to its own strategy rather than flattering to it.

The organising rule is one sentence: on trading day T the model may read only
what the market could read on trading day T. Every module here exists to
enforce some part of that -- pit.py for the data, execution.py for the fill,
strategy.py for the score -- because look-ahead does not announce itself. It
arrives as an excellent result.

LAYOUT
------
    pit.py          the only door to the database; refuses future rows
    strategy.py     signals, in four variants (technical / fundamental / both
                    / plus news), so the contribution of each can be measured
    portfolio.py    cash, positions, sizing, stops, targets, sector caps
    execution.py    T+1 fills, queues (صف), participation limits, partial fills
    engine.py       the session loop that drives all of the above
    metrics.py      performance, risk, and trade statistics
    validation.py   walk-forward, out-of-sample, Monte Carlo, sensitivity,
                    and an overfitting verdict with its arithmetic shown
    report.py       console, CSV, JSON and HTML output, plus PASS / FAIL
    paper.py        the same engine on live data with no order ever sent

Parameters live in backtest_config.py at the project root, never in here.
"""

from backtesting.engine import BacktestResult, run
from backtesting.strategy import (
    FUNDAMENTAL_ONLY,
    TECHNICAL_FUNDAMENTAL,
    TECHNICAL_FUNDAMENTAL_NEWS,
    TECHNICAL_ONLY,
    VARIANTS,
)

__all__ = [
    "run",
    "BacktestResult",
    "TECHNICAL_ONLY",
    "FUNDAMENTAL_ONLY",
    "TECHNICAL_FUNDAMENTAL",
    "TECHNICAL_FUNDAMENTAL_NEWS",
    "VARIANTS",
]
