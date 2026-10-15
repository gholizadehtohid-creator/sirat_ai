"""Political & Economic Intelligence -- an analytical REPORT layer, not a signal.

WHAT THIS IS, AND THE ONE LINE THAT DEFINES ITS SCOPE
-----------------------------------------------------
This module collects political and economic news that bears on gold, oil and
the Tehran market, classifies it, and turns it into a dashboard REPORT. It is
explicitly NOT a trading signal:

    News never reaches the Decision Engine. factor_engine, swing_advisor and
    the point-in-time firewall are not touched. There is no BUY/SELL from news,
    no catalyst wiring, no E_NEWS experiment, no edge claim.

That was the user's instruction (2026-08-23), and it is also what the original
brief called the honest outcome: "Information Value = YES, Trading Alpha = NO".
Keeping news out of the decision path removes the entire look-ahead / data-
snooping / edge-claim risk surface -- a report that no strategy consumes cannot
leak into a backtest.

HONESTY RULES THAT STILL APPLY (to the report itself)
-----------------------------------------------------
- Sources are the ones actually reachable from this machine, verified before
  building. Unreachable sources are recorded, not faked (DATA_UNAVAILABLE).
- There is no ML/LLM here. "Analysis" is rule-based (keyword -> category ->
  market), and any directional lean is labelled a HEURISTIC READING, never a
  signal or a forecast.
- An item with no publication date is kept but flagged undated; it never gets a
  guessed timestamp.
"""
