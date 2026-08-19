"""News: the architecture, and the honest admission that it has no data.

WHY THIS FILE EXISTS AT ALL WHEN IT RETURNS NOTHING
---------------------------------------------------
The specification asks for a news module and, in the same breath, forbids
inventing news to fill it. Those are not in tension -- they describe exactly
this file. The interface a news-aware strategy needs is defined here in full:
what a news item is, how sentiment and impact are aggregated to a date, what
"severity" means, and how a provider announces that it cannot answer. What is
absent is a provider with historical Iranian market news in it, because this
project has never collected any.

The alternative -- generating plausible sentiment from price moves, or scoring
Codal announcement titles and calling the result "news" -- would produce a
model C that beat model B for a reason no live system could reproduce. Price-
derived sentiment IS the price, so the news variant would be a second helping
of the technical signal wearing a different label, and the backtest would
report the resulting improvement as evidence that news helps.

So: `available()` returns False with a reason, every variant that depends on
news reports INSUFFICIENT DATA, and the reason is printed in the report rather
than buried.

WHAT A REAL PROVIDER HAS TO SUPPLY
----------------------------------
Two properties, and the second is the one that is usually missing:

  1. the article, its symbol and its sentiment
  2. THE TIMESTAMP AT WHICH IT BECAME PUBLIC, to the hour

Without (2) a news backtest is worthless in the specific way that is hardest to
detect: news arrives in bursts around events, an article dated only "that day"
is as likely to have been published after the close as before it, and assigning
it to the session it describes hands the strategy the session's own outcome.
That is the same error the fundamental side already guards against in
backtesting/pit.py, and it is worse here, because news moves prices in hours
rather than in quarters.

A provider that cannot supply (2) should return items with `published_at` set
to None. They are then dropped rather than dated by guesswork.
"""

import math
from datetime import datetime


# ==============================
# VOCABULARY
# ==============================

POSITIVE = "POSITIVE"
NEGATIVE = "NEGATIVE"
NEUTRAL = "NEUTRAL"

# Severity bands for a single event. These are the labels a provider assigns;
# nothing here infers severity from a price move, which would be circular.
SEVERITY_CRITICAL = "CRITICAL"     # suspension, default, fraud, licence loss
SEVERITY_MAJOR = "MAJOR"           # earnings surprise, large contract, M&A
SEVERITY_MINOR = "MINOR"           # routine disclosure
SEVERITY_NONE = "NONE"

SEVERITY_ORDER = {
    SEVERITY_NONE: 0,
    SEVERITY_MINOR: 1,
    SEVERITY_MAJOR: 2,
    SEVERITY_CRITICAL: 3,
}

SCORED = "SCORED"
INSUFFICIENT = "INSUFFICIENT_DATA"

# Stated once, so every caller reports the same absence in the same words.
NO_HISTORICAL_NEWS = (
    "no historical news store exists in this project: there is no news table, "
    "no archive, and no feed with per-article publication timestamps. A "
    "news-dependent result would have to invent both the articles and the hour "
    "they appeared, and the invented hour is what would decide the result"
)

# Sessions of news that feed one reading. News decays fast; a month-old article
# is context, not a signal.
DEFAULT_WINDOW_SESSIONS = 5


def _finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


class NewsItem(object):
    """One article, as a provider must supply it.

    `sentiment` is -1..+1, `impact` is 0..1 and means "how much should this
    move a price", which is deliberately separate from sentiment: a routine
    positive disclosure and a rescue capital increase are both positive and are
    not the same news.
    """

    __slots__ = ("ins_code", "published_at", "headline", "sentiment", "impact",
                 "severity", "source", "url")

    def __init__(self, ins_code, published_at, headline=None, sentiment=None,
                 impact=None, severity=SEVERITY_NONE, source=None, url=None):

        self.ins_code = str(ins_code)
        self.published_at = published_at
        self.headline = headline
        self.sentiment = _finite(sentiment)
        self.impact = _finite(impact)
        self.severity = severity or SEVERITY_NONE
        self.source = source
        self.url = url

    @property
    def dated(self):
        """Does this item carry a usable publication moment?

        The whole point-in-time contract rests on this one property, so it is
        a property rather than a convention a caller has to remember.
        """

        return isinstance(self.published_at, datetime)

    @property
    def polarity(self):
        if self.sentiment is None:
            return NEUTRAL
        if self.sentiment > 0.15:
            return POSITIVE
        if self.sentiment < -0.15:
            return NEGATIVE
        return NEUTRAL

    def as_dict(self):
        return {
            "ins_code": self.ins_code,
            "published_at": (self.published_at.isoformat()
                             if self.dated else None),
            "headline": self.headline,
            "sentiment": self.sentiment,
            "impact": self.impact,
            "polarity": self.polarity,
            "severity": self.severity,
            "source": self.source,
        }


# ==============================
# AGGREGATION
# ==============================

def aggregate(items):
    """Many articles -> one reading, or INSUFFICIENT_DATA.

    Sentiment is weighted by impact rather than averaged flat: five routine
    positives and one critical negative is a negative day, and a flat mean says
    the opposite. Items without an impact figure are given the mean impact of
    those that have one, so a provider that supplies sentiment and no impact
    degrades to a flat mean instead of returning nothing.
    """

    usable = [i for i in items or [] if i.dated and i.sentiment is not None]

    if not usable:
        return {
            "verdict": INSUFFICIENT,
            "reason": "no dated article with a sentiment",
            "items": 0,
            "sentiment": None,
            "impact": None,
            "polarity": None,
            "severity": None,
            "positive": 0,
            "negative": 0,
            "neutral": 0,
        }

    impacts = [i.impact for i in usable if i.impact is not None]

    default_impact = sum(impacts) / len(impacts) if impacts else 1.0

    weights = [i.impact if i.impact is not None else default_impact
               for i in usable]

    total_weight = sum(weights)

    sentiment = (
        sum(i.sentiment * w for i, w in zip(usable, weights)) / total_weight
        if total_weight > 0
        else sum(i.sentiment for i in usable) / len(usable)
    )

    severity = max((i.severity for i in usable),
                   key=lambda s: SEVERITY_ORDER.get(s, 0))

    polarity = (POSITIVE if sentiment > 0.15
                else NEGATIVE if sentiment < -0.15 else NEUTRAL)

    return {
        "verdict": SCORED,
        "reason": None,
        "items": len(usable),
        "dropped_undated": len([i for i in items or [] if not i.dated]),
        "sentiment": round(sentiment, 4),
        "impact": round(total_weight / len(usable), 4),
        "polarity": polarity,
        "severity": severity,
        "positive": sum(1 for i in usable if i.polarity == POSITIVE),
        "negative": sum(1 for i in usable if i.polarity == NEGATIVE),
        "neutral": sum(1 for i in usable if i.polarity == NEUTRAL),
    }


def to_score(reading):
    """A news reading -> 0-100, on the same scale as the other component scores.

    50 is neutral, so a name with no news is not penalised against one with
    balanced news. Returns None when the reading is INSUFFICIENT, which is what
    keeps an absent provider out of the blend instead of dragging every name to
    the middle.
    """

    if not reading or reading.get("verdict") != SCORED:
        return None

    sentiment = _finite(reading.get("sentiment"))

    if sentiment is None:
        return None

    return int(round(max(0.0, min(100.0, 50.0 + sentiment * 50.0))))


# ==============================
# PROVIDERS
# ==============================

class NewsProvider(object):
    """What a source of historical news must implement. Four methods."""

    name = "abstract"

    def available(self):
        """(bool, reason). False must always carry a reason a reader can act on."""

        raise NotImplementedError

    def items_until(self, ins_code, date, sessions=DEFAULT_WINDOW_SESSIONS):
        """Articles for one instrument published strictly before `date`'s close.

        A provider that returns an article published after that moment has
        broken the only rule this interface has.
        """

        raise NotImplementedError

    def reading(self, ins_code, date, sessions=DEFAULT_WINDOW_SESSIONS):
        """The aggregated news reading for one instrument on one date."""

        return aggregate(self.items_until(ins_code, date, sessions=sessions))

    def coverage(self, dates, ins_codes):
        """Share of (date, instrument) pairs this provider can actually answer.

        The figure the report prints. A provider holding news for six large
        names is not a news feed for an 818-name universe, and the only way to
        say so is to measure it.
        """

        pairs = 0
        answered = 0

        for date in dates or []:
            for code in ins_codes or []:
                pairs += 1
                if self.reading(code, date).get("verdict") == SCORED:
                    answered += 1

        return (answered / float(pairs)) if pairs else 0.0


class NullNewsProvider(NewsProvider):
    """The provider this project actually has: none.

    It is a real object rather than a None sentinel so that every caller
    exercises the same code path, and so the news variant of the strategy
    exists and reports INSUFFICIENT DATA -- rather than silently not existing
    while the report implies news was tested.
    """

    name = "none"

    def __init__(self, reason=NO_HISTORICAL_NEWS):
        self.reason = reason

    def available(self):
        return False, self.reason

    def items_until(self, ins_code, date, sessions=DEFAULT_WINDOW_SESSIONS):
        return []

    def coverage(self, dates, ins_codes):
        return 0.0


class DatabaseNewsProvider(NewsProvider):
    """Reads a `news_items` table, if one is ever created and filled.

    Nothing creates this table today, and that is the point: the moment a real
    archive is imported into it -- with per-article publication timestamps --
    model C becomes measurable with no change to the engine, the strategy or
    the report. Until then `available()` returns False for the specific reason
    it found, which is "the table is missing" or "the table is empty", not a
    generic shrug.

    Expected schema:

        news_items(id, ins_code, published_at TEXT ISO-8601, headline,
                   sentiment REAL -1..1, impact REAL 0..1, severity TEXT,
                   source TEXT, url TEXT)
    """

    name = "database"

    TABLE = "news_items"

    def __init__(self, table=None):
        self.table = table or self.TABLE
        self._cache = {}
        self._checked = None

    # ---- availability ----------------------------------------------------

    def available(self):

        if self._checked is not None:
            return self._checked

        import database

        try:
            conn = database._connect()
        except Exception as exc:
            self._checked = (False, "database unreachable: %s" % exc)
            return self._checked

        try:
            exists = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (self.table,)
            ).fetchone()

            if not exists:
                self._checked = (
                    False,
                    "table %r does not exist -- %s" % (self.table,
                                                       NO_HISTORICAL_NEWS),
                )
                return self._checked

            total, dated = conn.execute(
                "SELECT COUNT(*), SUM(CASE WHEN published_at IS NOT NULL "
                "THEN 1 ELSE 0 END) FROM %s" % self.table
            ).fetchone()

        except Exception as exc:
            self._checked = (False, "table %r unreadable: %s" % (self.table, exc))
            return self._checked
        finally:
            conn.close()

        if not total:
            self._checked = (False, "table %r is empty" % self.table)
        elif not dated:
            self._checked = (
                False,
                "all %d rows in %r lack a publication timestamp, so none of "
                "them can be placed before or after a session's close"
                % (total, self.table),
            )
        else:
            self._checked = (True, None)

        return self._checked

    # ---- reads -----------------------------------------------------------

    def _load(self, ins_code):

        key = str(ins_code)

        if key in self._cache:
            return self._cache[key]

        ok, _reason = self.available()

        if not ok:
            self._cache[key] = []
            return []

        import database

        conn = database._connect()

        try:
            rows = conn.execute(
                "SELECT ins_code, published_at, headline, sentiment, impact, "
                "severity, source, url FROM %s WHERE ins_code = ? "
                "ORDER BY published_at" % self.table, (key,)
            ).fetchall()
        except Exception:
            rows = []
        finally:
            conn.close()

        items = []

        for row in rows:

            stamp = _parse_stamp(row[1])

            items.append(NewsItem(
                ins_code=row[0], published_at=stamp, headline=row[2],
                sentiment=row[3], impact=row[4],
                severity=row[5] or SEVERITY_NONE, source=row[6], url=row[7],
            ))

        self._cache[key] = items

        return items

    def items_until(self, ins_code, date, sessions=DEFAULT_WINDOW_SESSIONS):
        """Articles published before `date`, within the lookback window.

        The cut is on the raw timestamp, and the caller passes a date that
        already carries the market-close rule -- the same division of labour
        backtesting/pit.py uses for filings, so the close time is defined in one
        place rather than two.
        """

        import pandas as pd

        cut = pd.Timestamp(date)

        floor = cut - pd.Timedelta(days=int(sessions) * 2)

        return [
            item for item in self._load(ins_code)
            if item.dated and floor <= pd.Timestamp(item.published_at) <= cut
        ]


def _parse_stamp(value):

    if value is None:
        return None

    if isinstance(value, datetime):
        return value

    text = str(value).strip()

    if not text:
        return None

    for parse in (datetime.fromisoformat,):
        try:
            return parse(text)
        except ValueError:
            pass

    # Jalali is accepted too, because an Iranian archive will arrive in it.
    try:
        import jalali
        return jalali.parse(text)
    except Exception:
        return None


# ==============================
# REGISTRY
# ==============================

_PROVIDERS = {
    "none": NullNewsProvider,
    "database": DatabaseNewsProvider,
}


def register(name, factory):
    """Add a provider. Exists so a real feed can be plugged in from outside."""

    _PROVIDERS[str(name)] = factory


def providers():
    return sorted(_PROVIDERS)


def get_provider(name=None):
    """The configured provider, falling back to the null one.

    An unknown name returns the null provider rather than raising, and says so
    through `available()`: a typo in a config file should degrade the news
    variant to INSUFFICIENT DATA, not stop a backtest that does not use news.
    """

    if not name:
        name = "database"

    factory = _PROVIDERS.get(str(name))

    if factory is None:
        return NullNewsProvider(
            reason="no news provider named %r is registered (have: %s)"
                   % (name, ", ".join(providers()))
        )

    try:
        return factory()
    except Exception as exc:
        return NullNewsProvider(
            reason="news provider %r could not be constructed: %s" % (name, exc)
        )


def status(name=None):
    """One dict describing what news this project can and cannot supply."""

    provider = get_provider(name)

    ok, reason = provider.available()

    return {
        "provider": provider.name,
        "available": bool(ok),
        "reason": reason,
        "registered": providers(),
    }
