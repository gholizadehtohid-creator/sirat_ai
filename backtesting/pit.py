"""Point-in-time data access. The single door between the engine and the disk.

WHY EVERY READ GOES THROUGH HERE
--------------------------------
Look-ahead bias is not usually a bug in the strategy. It is a bug in a data
access that was innocent everywhere else: `database.load_history_by_inscode()`
returns the whole series, and the whole series contains tomorrow. One `.iloc[-1]`
in the wrong place turns a backtest into a fortune teller, and the symptom is
an excellent result -- the one symptom nobody investigates.

So the engine never touches `database` directly. It asks this module for "the
data as of DATE" and this module is the only place where a date comparison can
be wrong. That does not make the mistake impossible; it makes it findable.

THE THREE CLOCKS
----------------
Three different notions of "when" meet in this project and they do not agree:

  prices        Gregorian session dates, no time. A bar dated 2026-08-11 is
                everything that happened up to that session's close.
  filings       Jalali timestamps with a clock: "1405/05/21 17:53:31". The
                exchange closes at 12:30, so that filing was NOT available to
                a decision made from 1405/05/21's closing prices.
  client type   published after the session it describes. Available for a
                decision on the next session, never on its own.

The rule this module applies: a decision taken on session D sees prices through
D's close, client-type figures through D (because the decision is taken after
the close), and filings published at or before D's close time. Everything else
is the future.

WHAT IT REFUSES TO DO
---------------------
If a filing's publication timestamp cannot be recovered, this module does not
guess. Under the default policy the filing is unusable -- not "usable from its
period end", which would be wrong by a median of 36 days in this database and
wrong in the direction that flatters the strategy.
"""

import hashlib
import math
import os
import pickle
from datetime import datetime

import pandas as pd

import backtest_config
import config
import database
import jalali
import news as news_module
from backtest import back_adjust


# Kept as a module constant because callers and tests import it by name. The
# authoritative wording now lives in news.py, beside the interface it describes.
NEWS_UNAVAILABLE = news_module.NO_HISTORICAL_NEWS


def _finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


class PointInTimeData(object):
    """Everything the engine may know, indexed by when it became knowable."""

    def __init__(self, cfg=None, verbose=True):

        self.cfg = cfg or backtest_config.BacktestConfig()
        self.verbose = verbose

        self._bars = {}
        self._flow = {}
        self._instruments = None
        self._filings = None
        self._calendar = None
        self._index = {}
        self._news = None

        # Counts what the firewall actually stopped, so a run can report that
        # it enforced the rule rather than merely claiming to.
        self.blocked_future_reads = 0

    # ==============================
    # UNIVERSE AND CALENDAR
    # ==============================

    def instruments(self):
        """{ins_code: {symbol, sector}} for every equity, loaded once."""

        if self._instruments is None:

            rows = database.load_instruments(equities_only=True)

            self._instruments = {
                str(row["ins_code"]): {
                    "symbol": row.get("symbol_fa"),
                    "sector": (row.get("sector_name") or "").strip(),
                }
                for row in rows
            }

        return self._instruments

    def calendar(self):
        """Session dates of the market, taken from the index series.

        The index trades on every session and nothing else in the database
        does -- a single share can be suspended for months, so building the
        calendar from shares would produce a calendar with holes that are not
        holidays.
        """

        if self._calendar is None:

            frame = self.index_series(backtest_config.BENCHMARK_TEDPIX)

            if frame is None or frame.empty:
                self._calendar = pd.DatetimeIndex([])
            else:
                self._calendar = pd.DatetimeIndex(frame["date"]).sort_values()

        return self._calendar

    def sessions(self, start=None, end=None):
        """Session dates within a window, inclusive of both ends."""

        days = self.calendar()

        if start is not None:
            days = days[days >= pd.Timestamp(start)]

        if end is not None:
            days = days[days <= pd.Timestamp(end)]

        return list(days)

    def rebalance_dates(self, start, end, every):
        """Every `every`-th session in the window. The decision dates."""

        window = self.sessions(start, end)

        if not window:
            return []

        return window[::max(1, int(every))]

    def index_series(self, ins_code):
        """Index history as a frame with datetime dates, cached."""

        key = str(ins_code)

        if key not in self._index:

            frame = database.load_index_history(key)

            if frame is None or frame.empty:
                self._index[key] = None
            else:
                frame = frame.dropna(subset=["close"]).copy()
                frame["date"] = pd.to_datetime(frame["date"])
                self._index[key] = frame.sort_values("date").reset_index(drop=True)

        return self._index[key]

    # ==============================
    # PRICES
    # ==============================

    def bars(self, ins_code):
        """Back-adjusted daily bars for one instrument, cached.

        Back-adjustment happens HERE rather than in the engine because it must
        happen exactly once. A capital increase left unadjusted is a 40%
        overnight "loss" that never occurred, and it corrupts the indicators,
        the trade return and -- worst -- the trailing stop, which would exit on
        a low that was a re-pricing rather than a move.
        """

        key = str(ins_code)

        if key not in self._bars:

            frame = database.load_history_by_inscode(key)

            if frame is None or frame.empty:
                self._bars[key] = None
            else:
                frame = frame.copy()
                # Kept BEFORE adjustment, for the one reading that needs the
                # price as it was printed: a valuation multiple. See
                # valuation_price().
                frame["raw_close"] = frame["close"].astype(float)
                frame, _ = back_adjust(frame)
                frame = frame.copy()
                frame["date"] = pd.to_datetime(frame["date"])
                frame = frame.sort_values("date").reset_index(drop=True)
                self._bars[key] = frame

        return self._bars[key]

    def position(self, ins_code, date):
        """Index of the last bar on or before `date`, or -1 if there is none.

        This is the function the whole module exists for. `searchsorted(...,
        "right") - 1` is the only correct form: "left" would exclude the
        session itself, and forgetting the -1 would return the first FUTURE
        bar, which is the classic one-day look-ahead.
        """

        frame = self.bars(ins_code)

        if frame is None or frame.empty:
            return -1

        return int(frame["date"].searchsorted(pd.Timestamp(date), side="right")) - 1

    def valuation_price(self, ins_code, date, fallback=None):
        """The close as PRINTED on `date`, for pricing a filing.

        The adjusted close is the right input for returns and indicators and
        the wrong one for P/E, P/S and P/B. Back-adjustment is anchored at the
        newest bar, so every price before a later capital increase is scaled
        down by it -- and a filing's EPS and share count are not. A company
        that went on to issue bonus shares therefore looked cheap in its own
        past, by exactly its later growth: look-ahead with a sign.
        """

        frame = self.bars(ins_code)

        index = self.position(ins_code, date)

        if frame is None or index < 0 or "raw_close" not in frame.columns:
            return fallback

        value = frame["raw_close"].iloc[index]

        try:
            value = float(value)
        except (TypeError, ValueError):
            return fallback

        return value if value > 0 else fallback

    def history_until(self, ins_code, date, bars=None):
        """Bars up to and including `date`. Never one row further.

        `bars` trims from the left, which is a performance choice and not a
        correctness one -- the right-hand cut is what matters.
        """

        frame = self.bars(ins_code)

        end = self.position(ins_code, date)

        if frame is None or end < 0:
            return None

        start = 0 if not bars else max(0, end - int(bars) + 1)

        return frame.iloc[start:end + 1]

    def bar_on(self, ins_code, date):
        """The bar for exactly this session, or None if it did not trade."""

        frame = self.bars(ins_code)

        index = self.position(ins_code, date)

        if frame is None or index < 0:
            return None

        row = frame.iloc[index]

        if pd.Timestamp(row["date"]) != pd.Timestamp(date):
            return None

        return row

    def bar_after(self, ins_code, date, offset=1):
        """The bar `offset` sessions after `date`, or None past the end.

        Returning None rather than the last available bar is deliberate: an
        order that cannot be filled must go unfilled, not be filled at an
        invented price on a session that never happened.
        """

        frame = self.bars(ins_code)

        index = self.position(ins_code, date)

        if frame is None or index < 0:
            return None

        target = index + int(offset)

        if target >= len(frame):
            return None

        return frame.iloc[target]

    def stale_sessions(self, ins_code, date):
        """Sessions between the instrument's last bar and `date`.

        A name whose last print is three weeks old is suspended, and a
        backtest that keeps trading it is trading a fiction.
        """

        frame = self.bars(ins_code)

        index = self.position(ins_code, date)

        if frame is None or index < 0:
            return None

        last = pd.Timestamp(frame["date"].iloc[index])

        calendar = self.calendar()

        between = calendar[(calendar > last) & (calendar <= pd.Timestamp(date))]

        return len(between)

    def average_value(self, ins_code, date, sessions=20):
        """Mean traded value over the sessions ending at `date`, in rials."""

        window = self.history_until(ins_code, date, bars=sessions)

        if window is None or window.empty or "value" not in window.columns:
            return None

        return _finite(window["value"].mean())

    def median_volume(self, ins_code, date, sessions=20):
        """Median traded volume, the baseline the queue test compares against."""

        window = self.history_until(ins_code, date, bars=sessions)

        if window is None or window.empty or "volume" not in window.columns:
            return None

        return _finite(window["volume"].median())

    # ==============================
    # CLIENT TYPE (حقیقی / حقوقی)
    # ==============================

    def flow(self, ins_code):
        """Client-type history for one instrument, cached."""

        key = str(ins_code)

        if key not in self._flow:

            frame = database.load_client_type_history(key)

            if frame is None or frame.empty:
                self._flow[key] = None
            else:
                frame = frame.copy()
                frame["date"] = pd.to_datetime(frame["date"])
                self._flow[key] = frame.sort_values("date").reset_index(drop=True)

        return self._flow[key]

    def flow_until(self, ins_code, date, sessions=None):
        """Client-type rows up to and including `date`.

        Including `date` itself is correct and is worth stating, because the
        opposite is also defensible. The figures for session D are published
        after D closes; the decision this feeds is also taken after D closes,
        and it is filled at D+1's open. So the data exists when the decision is
        made. What must never happen is filling at D's own close, and that is
        prevented in execution.py rather than here.
        """

        frame = self.flow(ins_code)

        if frame is None or frame.empty:
            return None

        cut = int(frame["date"].searchsorted(pd.Timestamp(date), side="right"))

        if cut <= 0:
            return None

        start = 0 if not sessions else max(0, cut - int(sessions))

        return frame.iloc[start:cut]

    # ==============================
    # FUNDAMENTALS (کدال)
    # ==============================

    def _filing_rows(self):
        """Raw filing rows, from the per-filing store when it has any.

        TWO STORES, AND WHY THE NEWER ONE WINS
        --------------------------------------
        codal_financials is keyed on (company, period, months), so a period
        filed twice -- unaudited in خرداد, audited in تیر -- survives as ONE
        row carrying whichever tracing number was written last. Read
        point-in-time, that row claims the surviving filing's publication date
        for figures that were already public weeks earlier, or (worse, if the
        unaudited row won) claims the earlier date for the audited figures.

        codal_filings is keyed on the tracing number, so both releases survive
        with their own dates and the question "what was public on 1403/09/14"
        has an exact answer.

        The old join is kept as the fallback rather than deleted: a database
        that predates codal_backfill.py still backtests, with the accuracy it
        always had, instead of reporting zero fundamental coverage.
        """

        rows = database.load_codal_filings()

        if rows:
            self.filing_source = "codal_filings"
            return [
                (r["ins_code"], r["period_end"], r["period_months"],
                 r["tracing_no"], r["title"], r["audited"], r["consolidated"],
                 r["items"], r["ratios"], r["published_at"])
                for r in rows
            ]

        self.filing_source = "codal_financials+codal_letters"

        conn = database._connect()

        try:
            raw = conn.execute("""
                SELECT f.ins_code, f.period_end, f.period_months, f.tracing_no,
                       f.title, f.audited, f.consolidated, f.items, f.ratios,
                       l.published_at
                FROM codal_financials f
                LEFT JOIN codal_letters l ON f.tracing_no = l.tracing_no
            """).fetchall()
        finally:
            conn.close()

        return [
            (r[0], r[1], r[2], r[3], r[4],
             None if r[5] is None else bool(r[5]), bool(r[6]),
             database._loads(r[7]), database._loads(r[8]), r[9])
            for r in raw
        ]

    def filings(self):
        """{ins_code: [filing, ...]} sorted by the date each became public.

        Built in one query rather than per symbol: the join is thousands of
        rows and doing it 818 times inside a loop was the difference between a
        second and a minute.

        Each filing carries `visible_from`, a SESSION date. That is the field
        the engine compares against, and it already contains the market-close
        rule, the publication timestamp and the configured lag -- so no caller
        has to re-derive any of them, and no caller can get them subtly wrong.
        """

        if self._filings is not None:
            return self._filings

        rows = self._filing_rows()

        calendar = self.calendar()

        policy = self.cfg.undated_filing_policy
        lag = int(self.cfg.fundamental_lag_sessions or 0)

        out = {}

        self.filing_stats = {
            "rows": len(rows),
            "dated": 0,
            "undated": 0,
            "unplaceable": 0,
            "source": getattr(self, "filing_source", None),
        }

        for row in rows:

            (ins_code, period_end, period_months, tracing_no, title,
             audited, consolidated, items, ratios, published_at) = row

            published = jalali.parse(published_at) if published_at else None

            if published is not None:
                self.filing_stats["dated"] += 1
                visible_from = self._first_session_after(calendar, published, lag)
            else:
                self.filing_stats["undated"] += 1

                if policy != "next_day":
                    continue

                # The measured cost of this assumption: in this database the
                # median filing appears 36 days after its period end, so
                # treating it as public the next day hands the strategy five
                # weeks of hindsight. Available only to price that error.
                period_date = jalali.parse(period_end)

                visible_from = (
                    self._first_session_after(calendar, period_date, lag + 1)
                    if period_date else None
                )

            if visible_from is None:
                self.filing_stats["unplaceable"] += 1
                continue

            out.setdefault(str(ins_code), []).append({
                "period_end": period_end,
                "period_months": period_months,
                "tracing_no": tracing_no,
                "title": title,
                "audited": None if audited is None else bool(audited),
                "consolidated": bool(consolidated),
                "items": items if isinstance(items, dict)
                else database._loads(items),
                "ratios": ratios if isinstance(ratios, dict)
                else database._loads(ratios),
                "published_at": published_at,
                "published": published,
                "visible_from": visible_from,
            })

        for records in out.values():
            records.sort(key=lambda r: (r["visible_from"], r["period_end"]))

        self._filings = out

        return out

    def _first_session_after(self, calendar, stamp, lag_sessions=0):
        """First session whose CLOSE is at or after `stamp`, plus a lag.

        A filing published at 17:53 was not available to a decision taken from
        that session's 12:30 close, so the session it becomes usable on is the
        next one. This is where that hour is applied.
        """

        if stamp is None or len(calendar) == 0:
            return None

        close = pd.Timestamp(stamp).normalize() + pd.Timedelta(
            hours=self.cfg.market_close_hour, minutes=self.cfg.market_close_minute
        )

        # Published after this day's close -> the day itself does not count.
        if pd.Timestamp(stamp) > close:
            reference = pd.Timestamp(stamp).normalize() + pd.Timedelta(days=1)
        else:
            reference = pd.Timestamp(stamp).normalize()

        index = int(calendar.searchsorted(reference, side="left")) + int(lag_sessions)

        if index >= len(calendar):
            return None

        return calendar[index]

    def fundamental_as_of(self, ins_code, date):
        """Newest filing this company had made public by session `date`."""

        records = self.filings().get(str(ins_code))

        if not records:
            return None

        stamp = pd.Timestamp(date)

        visible = None

        for record in records:
            if record["visible_from"] <= stamp:
                visible = record
            else:
                self.blocked_future_reads += 1
                break

        return visible

    def fundamental_history(self, ins_code, date, periods=4):
        """The last `periods` DISTINCT reporting periods public by `date`.

        What a single filing cannot say: whether a margin is widening or
        collapsing, whether growth is accelerating, whether the last quarter
        broke a pattern. The specification asks for multi-period trend, and
        trend needs the series rather than the newest point.

        Distinct by (period_end, period_months) and keeping the LATEST visible
        release of each: when both the unaudited and the audited version of a
        period are public by `date`, the audited figures are the ones the
        market had. When only the unaudited one is public yet, that is what it
        had, and the audited version is future data that must not appear here.

        Returned oldest first, which is the order a trend is read in.
        """

        records = self.filings().get(str(ins_code))

        if not records:
            return []

        stamp = pd.Timestamp(date)

        newest = {}

        for record in records:

            if record["visible_from"] > stamp:
                self.blocked_future_reads += 1
                break

            key = (record["period_end"], record["period_months"])

            newest[key] = record

        if not newest:
            return []

        ordered = sorted(
            newest.values(),
            key=lambda r: (str(r["period_end"]), r["period_months"] or 0),
        )

        return ordered[-int(periods):] if periods else ordered

    def fundamental_coverage(self, date, ins_codes=None):
        """Share of the universe with any point-in-time filing on `date`."""

        codes = list(ins_codes or self.instruments())

        if not codes:
            return 0.0

        have = sum(1 for code in codes
                   if self.fundamental_as_of(code, date) is not None)

        return have / float(len(codes))

    def fundamental_coverage_report(self, dates, ins_codes=None):
        """Point-in-time fundamental coverage, broken down every way asked for.

        By decision date, by calendar year, by company, and as the single
        headline figure: the share of (date, company) decisions that had a real
        filing behind them. That last number is the one the specification is
        actually about -- a universe-wide "91% of companies have filed
        something" is true and useless, because almost all of it was filed
        after the window it would have to inform.
        """

        codes = sorted(ins_codes or self.instruments())

        dates = [pd.Timestamp(d) for d in dates]

        if not codes or not dates:
            return {"available": False, "reason": "no dates or no universe"}

        by_date = []
        by_company = {}

        decisions = covered = 0

        for date in dates:

            have = 0

            for code in codes:

                decisions += 1

                if self.fundamental_as_of(code, date) is not None:
                    have += 1
                    covered += 1
                    by_company[code] = by_company.get(code, 0) + 1
                else:
                    by_company.setdefault(code, 0)

            by_date.append({
                "date": str(date.date()),
                "year": int(date.year),
                "covered": have,
                "universe": len(codes),
                "coverage": round(have / float(len(codes)), 4),
            })

        by_year = {}

        for row in by_date:
            entry = by_year.setdefault(row["year"], {"covered": 0, "of": 0,
                                                     "dates": 0})
            entry["covered"] += row["covered"]
            entry["of"] += row["universe"]
            entry["dates"] += 1

        # A company is "fully covered" when every decision date in the window
        # had a filing for it. Partial coverage is reported separately because
        # the two describe different problems: a company covered on half the
        # dates was listed or started filing mid-window, while one covered on
        # none is simply absent from Codal's answers.
        full = sum(1 for n in by_company.values() if n == len(dates))
        partial = sum(1 for n in by_company.values() if 0 < n < len(dates))
        none = sum(1 for n in by_company.values() if n == 0)

        return {
            "available": True,
            "source": self.filing_stats.get("source") if hasattr(
                self, "filing_stats") else None,
            "decisions": decisions,
            "decisions_covered": covered,
            "decision_coverage": round(covered / float(decisions), 4)
            if decisions else 0.0,
            "dates": len(dates),
            "universe": len(codes),
            "by_date": by_date,
            "by_year": [
                {"year": year, "coverage": round(v["covered"] / float(v["of"]), 4),
                 "decision_dates": v["dates"]}
                for year, v in sorted(by_year.items())
            ],
            "companies_full": full,
            "companies_partial": partial,
            "companies_none": none,
            "company_detail": by_company,
            "filings": self.filing_stats if hasattr(self, "filing_stats") else {},
        }

    def market_ratios(self, record, price, sector=None):
        """Valuation ratios recomputed at a past price. Never the stored ones.

        The `pe`, `ps` and `pb` fields saved in codal_financials are None
        throughout this database, because the parser had no market data beside
        the filing. That absence is lucky: had they been populated, they would
        carry TODAY's price against a filing from two years ago, and every
        historical valuation score would have been hindsight.

        Recomputing them from the filing's own share count and the price on the
        decision date is the only construction that is point-in-time, and it is
        cheap -- three divisions.

        The arithmetic moved to fundamental_engine.market_ratios() unchanged,
        because the live panel needs exactly this and was doing something else.
        This stays as the point-in-time caller's name for it.
        """

        import fundamental_engine

        return fundamental_engine.market_ratios(record, price, sector=sector)

    # ==============================
    # NEWS
    # ==============================

    def news_provider(self):
        """The configured provider, built once.

        Which one is a config field rather than a hard-coded choice, so a real
        archive can be plugged in without touching this file. What it is TODAY
        is the null provider, because nothing has been imported.
        """

        if self._news is None:
            self._news = news_module.get_provider(
                getattr(self.cfg, "news_provider", None)
            )

        return self._news

    def news_as_of(self, ins_code, date):
        """The news reading for one instrument as of one session, or None.

        Returns None -- not a neutral 50 -- when there is nothing to read. A
        neutral score would enter the blend and pull every name toward the
        middle, which is a signal made out of an absence.
        """

        provider = self.news_provider()

        available, _reason = provider.available()

        if not available:
            return None

        reading = provider.reading(str(ins_code), pd.Timestamp(date))

        if not reading or reading.get("verdict") != news_module.SCORED:
            return None

        return reading

    def news_available(self):
        """(bool, reason). The reason is printed, so it must be worth printing."""

        return self.news_provider().available()

    def news_coverage(self, dates, ins_codes=None):
        """Share of decisions a news provider could actually answer.

        Zero here is not a bug and not an error -- it is the measurement, and
        the report states it as one.
        """

        provider = self.news_provider()

        available, reason = provider.available()

        if not available:
            return {"available": False, "reason": reason, "coverage": 0.0,
                    "provider": provider.name}

        codes = sorted(ins_codes or self.instruments())

        return {
            "available": True,
            "provider": provider.name,
            "coverage": round(provider.coverage(dates, codes), 4),
            "reason": None,
        }

    # ==============================
    # CACHE KEYS
    # ==============================

    def fingerprint(self, extra=None):
        """Identity of the data behind a cached panel.

        Includes the newest bar date and the row counts, so a cache built
        before a data refresh is not silently reused afterwards.
        """

        conn = database._connect()

        try:
            bars, last = conn.execute(
                "SELECT COUNT(*), MAX(date) FROM price_history").fetchone()
            flow = conn.execute(
                "SELECT COUNT(*) FROM client_type_history").fetchone()[0]
            filings = conn.execute(
                "SELECT COUNT(*) FROM codal_financials").fetchone()[0]
        finally:
            conn.close()

        payload = "|".join(str(x) for x in
                           (bars, last, flow, filings, extra or ""))

        return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


# ==============================
# DISK CACHE FOR EXPENSIVE PANELS
# ==============================

def cache_path(name):
    os.makedirs(backtest_config.CACHE_DIR, exist_ok=True)
    return os.path.join(backtest_config.CACHE_DIR, name + ".pkl")


def cache_load(name):
    path = cache_path(name)

    if not os.path.exists(path):
        return None

    try:
        with open(path, "rb") as handle:
            return pickle.load(handle)
    except Exception:
        # A corrupt cache must cost a recomputation, never a run.
        return None


def cache_save(name, payload):
    try:
        with open(cache_path(name), "wb") as handle:
            pickle.dump(payload, handle, protocol=pickle.HIGHEST_PROTOCOL)
    except Exception:
        pass
