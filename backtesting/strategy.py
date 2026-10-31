"""Signal generation, in four variants, all reconstructed as of the day.

WHAT A "VARIANT" IS AND WHY THE SPLIT FALLS WHERE IT DOES
---------------------------------------------------------
The question this package exists to answer is not only "does the strategy
work" but "which part of it works". So the same engine runs on four evidence
sets and the results are compared:

    technical                price, volume and client-type flow
    fundamental              filed financial statements only
    technical+fundamental    both
    technical+fundamental+news   both, plus news

CLIENT-TYPE FLOW IS COUNTED AS TECHNICAL, and that is a judgement worth
defending rather than hiding. حقیقی/حقوقی figures are tape data: they are
published by the exchange with the session, they describe who traded, and they
need no accounting statement. Grouping them with fundamentals would mean the
"fundamental" variant depended on a TSETMC feed, and the "technical" variant
would exclude the single component this project has actually measured as
carrying information. Reported here so nobody has to infer it from the code.

WHAT MAKES A READING POINT-IN-TIME
----------------------------------
Every field below is derived from `pit.PointInTimeData`, which refuses future
rows. The technical score is computed on a window ending at the decision bar,
the flow reading on client-type rows ending at that session, and the
fundamental score on the newest filing whose PUBLICATION timestamp precedes
that session's close -- with the valuation ratios recomputed at the price on
that date rather than read off disk.

The panel is deliberately NOT gated while it is built. Storing every reading,
including the ones that fail today's thresholds, is what lets the sensitivity
sweep move a gate without rebuilding hours of work -- and a sweep that silently
reuses a panel built under a different gate is a whole class of wrong answers.
"""

import math

import pandas as pd

import config
import fundamental_scoring
import moneyflow
import news as news_module
import scoring
from backtesting import fundamental as fundamental_v2, pit
from indicators import DEFAULT_PARAMS, calculate_indicators, required_bars
from ranking import rank_stocks


TECHNICAL_ONLY = "technical"
FUNDAMENTAL_ONLY = "fundamental"
TECHNICAL_FUNDAMENTAL = "technical+fundamental"
TECHNICAL_FUNDAMENTAL_NEWS = "technical+fundamental+news"

VARIANTS = (
    TECHNICAL_ONLY,
    FUNDAMENTAL_ONLY,
    TECHNICAL_FUNDAMENTAL,
    TECHNICAL_FUNDAMENTAL_NEWS,
)

# Sessions of indicator frame handed to rank_stocks(). It reads the last bar
# and looks back 11 bars for momentum; 60 is comfortably more than it needs and
# matches what backtest_combined.py has always used, so scores are comparable
# across the two.
SCORING_WINDOW = 60

BUY = "BUY"
HOLD = "HOLD"
SELL = "SELL"
NO_DATA = "NO_DATA"

# Share of the ranking score a news reading may carry, when one exists.
#
# 0.15 is the smallest weight at which news could plausibly reorder a basket,
# and it was NOT chosen by running the news variant at several values and
# keeping the best -- that variant cannot run on this database at all, so there
# was no result to fit it to. When a news archive is imported it should be
# swept like every other parameter before it is believed.
NEWS_BLEND_WEIGHT = 0.15


def _finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


class InsufficientData(Exception):
    """Raised when a variant cannot be honestly run on the data present."""


class Strategy(object):
    """Turns a date into a ranked list of names the model wants to own."""

    def __init__(self, cfg, data, variant=TECHNICAL_ONLY, params=None,
                 verbose=True):

        if variant not in VARIANTS:
            raise ValueError("unknown strategy variant: %r" % variant)

        self.cfg = cfg
        self.data = data
        self.variant = variant
        self.params = params or DEFAULT_PARAMS
        self.verbose = verbose

        self.uses_technical = variant != FUNDAMENTAL_ONLY
        self.uses_fundamental = variant in (
            FUNDAMENTAL_ONLY, TECHNICAL_FUNDAMENTAL, TECHNICAL_FUNDAMENTAL_NEWS
        )
        self.uses_news = variant == TECHNICAL_FUNDAMENTAL_NEWS

        self.notes = []

        # WHY THE REJECTIONS ARE COUNTED BY CATEGORY
        #
        # The V1 report could say the book averaged 58.6% cash and could not
        # say why. "Nothing passed the gate" and "plenty passed and the sector
        # cap threw them away" produce the same cash figure and call for
        # opposite fixes, and without a count there is no way to tell them
        # apart except by guessing -- which is how a parameter gets tuned to
        # a symptom.
        self.rejections = {}
        self.candidates_seen = 0
        self.candidates_passed = 0

    # ==============================
    # THE PANEL
    # ==============================

    def build_panel(self, dates, use_cache=True):
        """{date: {ins_code: reading}} for every scoreable name on every date.

        One pass per symbol, not one per (symbol, date). The indicator frame
        over a full history costs about as much as a single date's does, so
        computing it once and reading it at each date is the difference between
        a minute and an afternoon.
        """

        dates = [pd.Timestamp(d) for d in dates]

        instruments = self.data.instruments()

        codes = sorted(instruments)

        if self.cfg.universe_limit:
            codes = codes[:int(self.cfg.universe_limit)]

        # ---- optional: only companies that ever filed ---------------------
        #
        # Off by default and never on for a headline result. It exists so that
        # a fundamental model can be MEASURED when universe-wide coverage is
        # too thin to report -- the alternative being no measurement at all,
        # which teaches nothing.
        #
        # The restricted universe is NOT a random sample of the market. It is
        # the companies Codal has answered for, which skews large and
        # well-followed, and a return measured on it is a return on that
        # subset. The engine records the restriction in its diagnostics so the
        # report cannot present the figure as a market-wide one.
        self.universe_restricted = None

        if (getattr(self.cfg, "restrict_to_fundamental_universe", False)
                and self.uses_fundamental):

            filed = set(self.data.filings())

            kept = [code for code in codes if code in filed]

            if kept:
                self.universe_restricted = {
                    "from": len(codes),
                    "to": len(kept),
                    "share": round(len(kept) / float(len(codes)), 4),
                    "note": "restricted to companies with at least one filing "
                            "anywhere in the store; this subset skews large "
                            "and well-followed, so the result describes it "
                            "rather than the market",
                }
                codes = kept

        cache_key = None

        if use_cache:
            cache_key = "panel_%s_%s" % (
                self.data.fingerprint(extra="%s|%s|%s|%s" % (
                    self.variant, len(codes),
                    dates[0].date() if dates else "-",
                    dates[-1].date() if dates else "-",
                )),
                len(dates),
            )

            cached = pit.cache_load(cache_key)

            if cached is not None:
                if self.verbose:
                    print("  panel: reusing cache (%d dates)" % len(cached))
                return cached

        panel = {date: {} for date in dates}

        warmup = max(required_bars(self.params), SCORING_WINDOW)

        scanned = 0
        skipped = 0

        for number, ins_code in enumerate(codes, start=1):

            meta = instruments[ins_code]

            bars = self.data.bars(ins_code)

            if bars is None or len(bars) < warmup + 5:
                skipped += 1
                continue

            try:
                enriched = calculate_indicators(bars, self.params)
            except Exception:
                skipped += 1
                continue

            scanned += 1

            if self.verbose and scanned % 100 == 0:
                print("  panel: %d/%d symbols" % (number, len(codes)), flush=True)

            self._score_symbol(panel, dates, ins_code, meta, bars, enriched,
                               warmup)

        if self.verbose:
            print("  panel: %d symbols scored, %d skipped for thin history"
                  % (scanned, skipped), flush=True)

        if cache_key:
            pit.cache_save(cache_key, panel)

        return panel

    def _score_symbol(self, panel, dates, ins_code, meta, bars, enriched,
                      warmup):
        """Write one symbol's reading into every date of the panel."""

        bar_dates = bars["date"]
        highs = bars["high"] if "high" in bars.columns else bars["close"]

        flow_rows = self._flow_frame(ins_code)

        headroom_window = getattr(config, "HEADROOM_WINDOW", 120)

        for date in dates:

            index = int(bar_dates.searchsorted(date, side="right")) - 1

            if index < warmup:
                continue

            stale = self.data.stale_sessions(ins_code, date)

            if stale is not None and stale > self.cfg.max_stale_sessions:
                continue

            if index + 1 < self.cfg.min_history_bars:
                continue

            row = enriched.iloc[index]

            close = _finite(row.get("close"))

            if not close or close <= 0:
                continue

            reading = {
                "ins_code": ins_code,
                "symbol": meta["symbol"],
                "sector": meta["sector"] or "?",
                "close": close,
                "atr": _finite(row.get("ATR")),
                "atr_pct": _finite(row.get("ATR_PCT")),
                "technical": None,
                "fundamental": None,
                "news": None,
                "flow": None,
                "relative_flow": None,
                "headroom": None,
                "risk": None,
                "adv": None,
                "signal": NO_DATA,
            }

            # ---- technical ------------------------------------------------
            if self.uses_technical:
                window = enriched.iloc[max(0, index - SCORING_WINDOW + 1):index + 1]

                try:
                    technical = rank_stocks(window)
                except Exception:
                    technical = None

                if technical and technical.get("scored"):
                    reading["technical"] = technical.get("score")

            # ---- liquidity, always: it gates and it sizes -----------------
            if "value" in bars.columns:
                tail = bars["value"].iloc[max(0, index - 19):index + 1]
                reading["adv"] = _finite(tail.mean())

            # ---- headroom to the ceiling the market remembers -------------
            if index >= headroom_window:
                prior = highs.iloc[index - headroom_window:index]
                ceiling = _finite(prior.max())
                if ceiling is not None:
                    reading["headroom"] = (ceiling - close) / close * 100

            # ---- flow -----------------------------------------------------
            if self.uses_technical and flow_rows is not None:
                self._add_flow(reading, flow_rows, date)

            # ---- risk, as a sizing input rather than a prediction ---------
            closes = enriched["close"].iloc[max(0, index - 60):index + 1]

            volatility = None

            if len(closes) > 5:
                deviation = closes.pct_change().std()
                volatility = _finite(deviation * math.sqrt(config.TRADING_DAYS_PER_YEAR) * 100)

            reading["risk"] = scoring.risk_score({
                "volatility_60": volatility,
                "atr_pct": reading["atr_pct"],
                "avg_value_20": reading["adv"],
            }).get("score")

            # ---- fundamental ----------------------------------------------
            if self.uses_fundamental:
                self._add_fundamental(reading, ins_code, date)

            # ---- news ------------------------------------------------------
            if self.uses_news:
                self._add_news(reading, ins_code, date)

            panel[date][ins_code] = reading

    def _flow_frame(self, ins_code):
        """Client-type rows with net retail flow as a share of turnover."""

        frame = self.data.flow(ins_code)

        if frame is None or frame.empty:
            return None

        frame = frame.copy()

        buy = frame["individual_buy_value"].astype(float)
        sell = frame["individual_sell_value"].astype(float)
        institutional = frame["institutional_buy_value"].astype(float)

        turnover = (buy + institutional).replace(0, float("nan"))

        frame["net_pct"] = (buy - sell) / turnover * 100

        return frame

    def _add_flow(self, reading, flow_rows, date):
        """Absolute flow score and the same signal standardised per symbol."""

        cut = int(flow_rows["date"].searchsorted(date, side="right"))

        if cut <= 0:
            return

        window = flow_rows.iloc[max(0, cut - config.FLOW_BASELINE_WINDOW):cut]

        if len(window) >= config.FLOW_MIN_SESSIONS:
            try:
                verdict = moneyflow.analyse(window)
            except Exception:
                verdict = None

            if verdict and verdict.get("verdict") == moneyflow.SCORED:
                reading["flow"] = verdict.get("score")

        baseline_window = getattr(config, "FLOW_RELATIVE_BASELINE", 120)

        net = flow_rows["net_pct"].iloc[:cut].dropna()

        if len(net) >= baseline_window:
            baseline = net.tail(baseline_window)
            sigma = float(baseline.std())
            if sigma and sigma > 0:
                recent = float(net.tail(config.FLOW_WINDOW).mean())
                reading["relative_flow"] = (recent - float(baseline.mean())) / sigma

    def _add_fundamental(self, reading, ins_code, date):
        """Score every filing that was public before this session closed.

        The series, not just the newest filing. Two companies whose latest
        statement is identical are not the same investment when one has widened
        its margin for four quarters and the other has halved it, and that
        difference is invisible to a scorer handed one period.

        Valuation is recomputed at THIS date's price. The multiples stored on
        disk are None throughout this database, which is fortunate: a stored
        P/E would carry today's price against a filing from two years ago, and
        every historical valuation score would be hindsight.
        """

        periods = int(getattr(self.cfg, "fundamental_trend_periods", 4) or 4)

        history = self.data.fundamental_history(ins_code, date, periods=periods)

        if not history:
            return

        record = history[-1]

        price = self.data.valuation_price(ins_code, date,
                                          fallback=reading["close"])

        ratios = self.data.market_ratios(record, price,
                                         sector=reading["sector"])

        result = fundamental_v2.evaluate(
            history, ratios,
            trend_weight=getattr(self.cfg, "fundamental_trend_weight", None),
            price=price, sector=reading["sector"],
        )

        # Kept even when INSUFFICIENT, because "a filing existed and the model
        # could not run on it" is a different fact from "no filing existed",
        # and the coverage report distinguishes them.
        reading["fundamental_verdict"] = result.get("verdict")
        reading["fundamental_periods"] = result.get("periods")
        reading["fundamental_period"] = record["period_end"]
        reading["fundamental_visible_from"] = record["visible_from"]
        reading["fundamental_published"] = record["published_at"]
        reading["fundamental_audit"] = result.get("audit")
        reading["fundamental_breakdown"] = result.get("breakdown")
        reading["fundamental_trend"] = result.get("trend")

        if result.get("verdict") != fundamental_v2.SCORED:
            reading["fundamental_reason"] = result.get("reason")
            return

        reading["fundamental"] = result.get("score")

    def _add_news(self, reading, ins_code, date):
        """Attach the news reading, if any provider can supply one.

        Leaves `news` as None when there is nothing, rather than writing a
        neutral 50. A neutral score built out of an absence would enter the
        blend and pull every name toward the middle -- a signal made of missing
        data, which is the failure this whole package exists to prevent.
        """

        reading_news = self.data.news_as_of(ins_code, date)

        if not reading_news:
            return

        reading["news"] = news_module.to_score(reading_news)
        reading["news_sentiment"] = reading_news.get("sentiment")
        reading["news_impact"] = reading_news.get("impact")
        reading["news_polarity"] = reading_news.get("polarity")
        reading["news_severity"] = reading_news.get("severity")
        reading["news_items"] = reading_news.get("items")

    # ==============================
    # SELECTION
    # ==============================

    def signals(self, readings):
        """Mark every reading BUY / HOLD / NO_DATA under this variant's rules.

        Mutates the readings in place and returns the BUY set. Separated from
        ranking so the report can count how many names passed the entry test
        before the portfolio's own caps threw most of them away -- a basket of
        four names because only four passed is a different fact from a basket
        of four because the sector cap bound.
        """

        buys = []

        gate = self.cfg.technical_gate
        floor = self.cfg.min_headroom_pct

        for reading in readings.values():

            technical = reading.get("technical")
            fundamental = reading.get("fundamental")

            passed = True
            reason = None
            category = None

            if self.uses_technical:
                if technical is None:
                    passed, reason = False, "no technical score"
                    category = "no_technical_score"
                elif technical < gate:
                    passed, reason = False, "technical %d < gate %d" % (technical, gate)
                    category = "technical_gate"

            if passed and self.uses_fundamental:
                if fundamental is None:
                    # Two very different absences, counted apart: a company
                    # that had filed nothing by this date, and one whose filing
                    # was too thin for the model to run on. The first is a data
                    # problem and the second is a scoring one.
                    if reading.get("fundamental_verdict"):
                        passed, reason = False, "fundamental model could not run"
                        category = "fundamental_insufficient"
                    else:
                        passed, reason = False, "no filing public on this date"
                        category = "no_fundamental_filing"
                elif fundamental < config.FUND_APPROVE_SCORE:
                    passed, reason = False, "fundamental %d < %d" % (
                        fundamental, config.FUND_APPROVE_SCORE)
                    category = "fundamental_gate"

            if passed and self.uses_news:
                if reading.get("news") is None:
                    passed, reason = False, "no news reading on this date"
                    category = "no_news"

            # A share pressed against its own multi-month ceiling returned
            # +2.4% over 20 sessions against +7.4% for one with room. Unknown
            # headroom is not a failure: a young listing has no 120-session
            # high and is judged on everything else.
            if passed and floor is not None and self.uses_technical:
                room = reading.get("headroom")
                if room is not None and room < floor:
                    passed, reason = False, "headroom %.1f%% < %.1f%%" % (room, floor)
                    category = "headroom"

            if passed and reading.get("adv") is not None:
                if reading["adv"] < config.MIN_DAILY_VALUE:
                    passed, reason = False, "illiquid"
                    category = "illiquid"

            self.candidates_seen += 1

            if passed:
                self.candidates_passed += 1
                reading["signal"] = BUY
                # Cleared, not left behind: the sensitivity sweep runs this over
                # the SAME panel objects with a different gate, and a stale
                # rejection sitting on a name that now passes would be read from
                # the report as the reason it was not bought.
                reading["reject_reason"] = None
                reading["reject_category"] = None
                buys.append(reading)
            else:
                reading["signal"] = (
                    HOLD if (technical is not None or fundamental is not None)
                    else NO_DATA
                )
                reading["reject_reason"] = reason
                reading["reject_category"] = category
                self.rejections[category] = self.rejections.get(category, 0) + 1

        return buys

    def rejection_summary(self):
        """Why candidates did not become signals, in order of how much it cost.

        The first half of the cash answer. The second half -- how many signals
        the portfolio's own caps then discarded -- is counted in engine.py,
        because only the book knows whether it was full.
        """

        total = sum(self.rejections.values())

        rows = [
            {
                "reason": reason or "unknown",
                "count": count,
                "share": round(count / float(total), 4) if total else 0.0,
            }
            for reason, count in sorted(self.rejections.items(),
                                        key=lambda kv: -kv[1])
        ]

        return {
            "candidates_seen": self.candidates_seen,
            "candidates_passed": self.candidates_passed,
            "pass_rate": (round(self.candidates_passed
                                / float(self.candidates_seen), 4)
                          if self.candidates_seen else None),
            "rejected": total,
            "by_reason": rows,
        }

    def rank(self, candidates):
        """Order the BUY set. Highest conviction first.

        Which number does the ordering depends on the variant, and on
        config.SELECTION_MODE for the technical side. The live system ranks by
        flow above a technical gate, because measurement showed the technical
        score does not sort by outcome above its own threshold while the flow
        score does. Reproducing that here rather than substituting something
        simpler is the point: a backtest of different logic than the one that
        runs measures something nobody uses.
        """

        mode = self.cfg.selection_mode

        if self.variant == FUNDAMENTAL_ONLY:
            keyed = [(r, r.get("fundamental")) for r in candidates]

        elif self.variant == TECHNICAL_ONLY:
            keyed = [(r, self._technical_rank(r, mode)) for r in candidates]

        else:
            # Both evidence sets present: rank on the project's own blend so
            # the weights in config.py are what is being tested.
            keyed = []
            for reading in candidates:
                blended = scoring.combine(
                    technical=reading.get("technical"),
                    fundamental=reading.get("fundamental"),
                    risk=reading.get("risk"),
                    flow=reading.get("flow"),
                )

                value = blended.get("score")

                # News is blended AFTER the project's own scorer rather than
                # inside it, deliberately. scoring.combine() is what the live
                # panel runs, and adding a fifth component to it would change
                # every score the live system produces in order to measure a
                # variant that this database cannot even run. The weight is
                # the news pillar's share of a five-component model, applied
                # only when a reading exists.
                news = reading.get("news")

                if value is not None and news is not None:
                    weight = NEWS_BLEND_WEIGHT
                    value = (1 - weight) * value + weight * news
                    reading["news_blended"] = True

                reading["combined"] = value
                keyed.append((reading, value))

        ordered = [(reading, value) for reading, value in keyed
                   if value is not None]

        # Flow scores saturate -- dozens of names sit at exactly 100 -- so the
        # tiebreak is not a detail, it chooses the basket. Liquidity is used
        # because it is measured rather than modelled.
        ordered.sort(
            key=lambda pair: (pair[1], pair[0].get("adv") or 0.0),
            reverse=True,
        )

        return [reading for reading, _ in ordered]

    def _technical_rank(self, reading, mode):

        if mode == "gate_then_flow":
            value = reading.get("flow")
            return value if value and value > 0 else None

        if mode == "gate_then_relative":
            return reading.get("relative_flow")

        if mode == "combined":
            # Needs both inputs; a name scored on one of them would sit on a
            # different scale from every other candidate.
            flow = reading.get("relative_flow")
            room = reading.get("headroom")
            if flow is None or room is None:
                return None
            return (config.COMBINED_WEIGHT_FLOW * flow
                    + config.COMBINED_WEIGHT_HEADROOM * room)

        return reading.get("technical")

    # ==============================
    # DATA HONESTY
    # ==============================

    def check_data(self, dates, panel):
        """Refuse to report a variant the data cannot support.

        This is rule 26 of the specification made executable: a backtest that
        silently runs a fundamental strategy over dates where no filing was
        public is not measuring the fundamental model, it is measuring an empty
        basket -- and it will report that as a result.
        """

        problems = []

        if self.uses_news:

            available, reason = self.data.news_available()

            self.news_coverage = 0.0

            if not available:
                problems.append("NEWS: " + reason)
            else:
                covered = []

                for date in dates:
                    readings = panel.get(pd.Timestamp(date)) or {}
                    if not readings:
                        continue
                    have = sum(1 for r in readings.values()
                               if r.get("news") is not None)
                    covered.append(have / float(len(readings)))

                self.news_coverage = (sum(covered) / len(covered)
                                      if covered else 0.0)

                if self.news_coverage < self.cfg.min_news_coverage:
                    problems.append(
                        "NEWS: a provider answered, but only %.1f%% of "
                        "decisions had a dated article behind them (need "
                        "%.0f%%). A news variant measured on that share is "
                        "measuring whichever names happened to be covered."
                        % (self.news_coverage * 100,
                           self.cfg.min_news_coverage * 100)
                    )

        if self.uses_fundamental:

            covered = []
            had_filing = []

            for date in dates:
                readings = panel.get(pd.Timestamp(date)) or {}
                if not readings:
                    continue
                have = sum(1 for r in readings.values()
                           if r.get("fundamental") is not None)
                filed = sum(1 for r in readings.values()
                            if r.get("fundamental_period") is not None)
                covered.append(have / float(len(readings)))
                had_filing.append(filed / float(len(readings)))

            mean_coverage = sum(covered) / len(covered) if covered else 0.0

            self.fundamental_coverage = mean_coverage
            self.fundamental_filing_coverage = (
                sum(had_filing) / len(had_filing) if had_filing else 0.0
            )

            if mean_coverage < self.cfg.min_fundamental_coverage:
                problems.append(
                    "FUNDAMENTALS: only %.1f%% of the universe had a SCOREABLE "
                    "filing published before the average decision date (need "
                    "%.0f%%); %.1f%% had any filing at all. Run "
                    "`python codal_backfill.py --hours N` to widen the "
                    "historical store -- coverage is limited by how far back "
                    "codal_filings reaches, not by the model."
                    % (mean_coverage * 100,
                       self.cfg.min_fundamental_coverage * 100,
                       self.fundamental_filing_coverage * 100)
                )

        return problems
