"""Tests for the market-intelligence (news) report layer.

The load-bearing guarantees here are two:
  1. It is a REPORT, never a signal -- the package must not import the trading
     path (factor_engine / swing_advisor / the PIT firewall). That is asserted
     directly, because a stray import is exactly how "information" silently
     becomes an input to a decision.
  2. Classification is honest and discriminating -- every tag traces to a
     keyword, an Iran story does not flood all three markets, and dedup collapses
     paraphrased headlines so a corroboration count means something.

The store tests run against a throwaway SQLite file, never the production DB.
"""

import os
import re
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from event_intelligence import classify, dedup, feeds, taxonomy
from event_intelligence import sources as sources_mod


# ---------------------------------------------------------------------------
# GOVERNANCE: the report must not touch the trading path
# ---------------------------------------------------------------------------
class IsolationTests(unittest.TestCase):
    """The physical no-signal guarantee: no import of the decision path."""

    FORBIDDEN = ("factor_engine", "swing_advisor", "swing_score", "decide(")

    def test_package_never_imports_the_trading_path(self):
        pkg = os.path.join(ROOT, "event_intelligence")
        offenders = []
        for fn in os.listdir(pkg):
            if not fn.endswith(".py"):
                continue
            text = open(os.path.join(pkg, fn), encoding="utf-8").read()
            for bad in ("import factor_engine", "from factor_engine",
                        "import swing_advisor", "from swing_advisor",
                        "backtesting.pit", "import pit"):
                if bad in text:
                    offenders.append(f"{fn}: {bad}")
        self.assertEqual(offenders, [],
                         f"news layer must not import the trading path: {offenders}")


# ---------------------------------------------------------------------------
# CLASSIFICATION
# ---------------------------------------------------------------------------
class ClassifyTests(unittest.TestCase):

    def test_persian_gold_and_fed_story(self):
        r = classify.classify(
            "انس طلا پس از تصمیم فدرال رزرو درباره نرخ بهره صعود کرد",
            tier=2, lang="fa")
        self.assertIn("gold", r["markets"].split(","))
        self.assertIn("monetary_policy", r["categories"].split(","))
        self.assertIn("gold_metals", r["categories"].split(","))

    def test_english_oil_story(self):
        r = classify.classify(
            "Oil prices plunge to a two-year low", tier=1, lang="en")
        self.assertIn("oil", r["markets"].split(","))
        self.assertEqual(r["tone"], "down")

    def test_economic_analysis_outranks_political_wire(self):
        econ = classify.classify(
            "Gold price forecast: bullion could reach $4,000 by year-end",
            tier=2, lang="en", focus="econ")
        pol = classify.classify(
            "Sanctions target Iran's regional network", tier=2, lang="en",
            focus="wire")
        self.assertTrue(econ["is_economic"])
        self.assertTrue(econ["is_price_forecast"])
        self.assertFalse(pol["is_economic"])
        self.assertGreater(econ["salience"], pol["salience"])

    def test_price_forecast_needs_forecast_and_price_and_market(self):
        yes = classify.classify(
            "Analysts see oil prices reaching $100 a barrel", tier=2, lang="en")
        self.assertTrue(yes["is_price_forecast"])
        # a plain price report with no forward-looking word is not a forecast
        no = classify.classify("Oil closed at $82 today", tier=2, lang="en")
        self.assertFalse(no["is_price_forecast"])

    def test_foreign_commodity_outlook_is_a_forecast(self):
        # "Oil Nears $100" -- foreign price commentary the user wants surfaced.
        r = classify.classify("Oil Nears $100 as Trump's Economic D-Day Raises",
                              tier=3, lang="en")
        self.assertTrue(r["is_price_forecast"])
        r2 = classify.classify("Gold could climb to $4,000 an ounce next year",
                               tier=2, lang="en")
        self.assertTrue(r2["is_price_forecast"])

    def test_ma_deal_and_tariffs_are_not_forecasts(self):
        # a corporate deal with a $ amount is NOT a price forecast
        deal = classify.classify("Bunker Hill to buy Silver47 in $163M deal",
                                 tier=3, lang="en")
        self.assertFalse(deal["is_price_forecast"])
        # a tariff/policy story with "$" and "expected to" is NOT a price forecast
        tariff = classify.classify(
            "What to know about Trump's 50% tariffs on Canadian goods; the "
            "dollar is expected to move", tier=2, lang="en")
        self.assertFalse(tariff["is_price_forecast"])
        # a mining-finance story: "gold nears" + a $27M SUM (not a per-unit price)
        fin = classify.classify(
            "Metals Exploration locks in $27M as La India first gold nears",
            tier=3, lang="en")
        self.assertFalse(fin["is_price_forecast"])

    def test_dollar_forecast_maps_to_fx_market(self):
        r = classify.classify(
            "Dollar outlook: greenback expected to weaken into 2027",
            tier=2, lang="en")
        self.assertIn("fx", r["markets"].split(","))
        self.assertTrue(r["is_price_forecast"])

    def test_iran_story_does_not_flood_gold(self):
        """An Iran-sanctions story maps to Tehran market + oil, not gold.

        The whole point of the discriminating market map: if every geopolitics
        story landed in gold too, the three market tabs would be identical.
        """
        r = classify.classify(
            "New US sanctions target Iran's oil exports", tier=2, lang="en")
        markets = r["markets"].split(",")
        self.assertIn("tse", markets)
        self.assertIn("oil", markets)
        self.assertNotIn("gold", markets)

    def test_salience_is_bounded(self):
        r = classify.classify("Gold oil sanctions Iran Fed inflation OPEC",
                              tier=1, lang="en")
        self.assertGreaterEqual(r["salience"], 0.0)
        self.assertLessEqual(r["salience"], 1.0)

    def test_unmatched_text_gets_no_market(self):
        # v1.1: the source-hint fallback was removed. A story that names no
        # market and matches no market-specific category has NO market -- it must
        # not be forced into a market section by its source's hint (that pollution
        # is exactly what the user flagged).
        r = classify.classify("A quiet day in local sports", tier=3,
                              lang="en", markets_hint=["oil"])
        self.assertEqual(r["markets"], "")
        self.assertEqual(r["categories"], "")

    def test_macro_story_does_not_leak_into_gold_or_stocks(self):
        # "economy/growth" alone (inflation_macro) must NOT tag gold/tse/fx now.
        r = classify.classify("UK economy shows modest growth this quarter",
                              tier=2, lang="en")
        self.assertIn("inflation_macro", r["categories"].split(","))
        self.assertEqual(r["markets"], "")            # no direct market named
        self.assertFalse(r["is_price_forecast"])


# ---------------------------------------------------------------------------
# DEDUP
# ---------------------------------------------------------------------------
class DedupTests(unittest.TestCase):

    def test_signature_is_stable_across_word_order(self):
        a = dedup.title_signature("Oil prices plunge as OPEC boosts output")
        b = dedup.title_signature("OPEC boosts output, oil prices plunge")
        self.assertEqual(a, b)

    def test_signature_differs_for_different_stories(self):
        a = dedup.title_signature("Gold hits record high on Fed rate cut")
        b = dedup.title_signature("Oil prices plunge as OPEC boosts output")
        self.assertNotEqual(a, b)

    def test_group_events_merges_paraphrases_and_counts_sources(self):
        arts = [
            {"title": "Oil prices plunge as OPEC boosts output", "source": "A",
             "salience": 0.8, "published_at": "2026-08-23T10:00:00+00:00",
             "markets": "oil", "categories": "oil_energy", "tone": "down",
             "summary": "", "link": "x"},
            {"title": "OPEC boosts output, oil prices plunge", "source": "B",
             "salience": 0.7, "published_at": "2026-08-23T09:00:00+00:00",
             "markets": "oil", "categories": "oil_energy", "tone": "down",
             "summary": "", "link": "y"},
            {"title": "Gold hits record high on Fed rate cut", "source": "C",
             "salience": 0.9, "published_at": "2026-08-23T08:00:00+00:00",
             "markets": "gold", "categories": "gold_metals", "tone": "up",
             "summary": "", "link": "z"},
        ]
        events = dedup.group_events(arts, threshold=0.5)
        self.assertEqual(len(events), 2)
        oil = next(e for e in events if "oil" in e["markets"])
        self.assertEqual(oil["corroboration"], 2)
        self.assertEqual(sorted(oil["sources"]), ["A", "B"])
        self.assertEqual(oil["tone"], "down")

    def test_group_events_handles_empty(self):
        self.assertEqual(dedup.group_events([]), [])


# ---------------------------------------------------------------------------
# FEED PARSING (no network)
# ---------------------------------------------------------------------------
RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Test</title>
  <item>
    <title>Gold rises on rate cut</title>
    <link>https://example.com/a</link>
    <description>&lt;p&gt;Bullion &lt;b&gt;gains&lt;/b&gt; as the Fed acts.&lt;/p&gt;</description>
    <guid>guid-1</guid>
    <pubDate>Sat, 22 Aug 2026 19:40:52 GMT</pubDate>
  </item>
  <item>
    <title>Undated item</title>
    <link>https://example.com/b</link>
    <description>No date here.</description>
  </item>
</channel></rss>"""

ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>Crude falls sharply</title>
    <link rel="alternate" href="https://example.com/oil"/>
    <summary>Oil dropped today.</summary>
    <id>atom-1</id>
    <published>2026-08-22T19:40:52Z</published>
  </entry>
</feed>"""


class FeedParseTests(unittest.TestCase):

    def test_rss_parsing_strips_html_and_parses_date(self):
        items = feeds.parse_feed(RSS)
        self.assertEqual(len(items), 2)
        first = items[0]
        self.assertEqual(first["title"], "Gold rises on rate cut")
        self.assertEqual(first["link"], "https://example.com/a")
        self.assertNotIn("<", first["summary"])
        self.assertIn("Bullion gains", first["summary"])
        self.assertEqual(first["published_at"], "2026-08-22T19:40:52+00:00")

    def test_undated_item_has_none_not_a_guess(self):
        items = feeds.parse_feed(RSS)
        self.assertIsNone(items[1]["published_at"])

    def test_atom_link_is_href_and_iso_date(self):
        items = feeds.parse_feed(ATOM)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["link"], "https://example.com/oil")
        self.assertEqual(items[0]["published_at"], "2026-08-22T19:40:52+00:00")

    def test_non_xml_body_returns_none(self):
        self.assertIsNone(feeds.parse_feed("<html>not a feed</html>"))

    def test_persian_offset_converts_to_utc(self):
        self.assertEqual(
            feeds._parse_date("23 Aug 2026 00:09:58 +0330"),
            "2026-08-22T20:39:58+00:00")

    def test_blank_and_garbage_dates_are_none(self):
        self.assertIsNone(feeds._parse_date(""))
        self.assertIsNone(feeds._parse_date("not a date at all"))


# ---------------------------------------------------------------------------
# SOURCE REGISTRY
# ---------------------------------------------------------------------------
class SourceRegistryTests(unittest.TestCase):

    def test_rss_sources_excludes_html_only(self):
        names = {s["name"] for s in sources_mod.rss_sources()}
        self.assertNotIn("بانک مرکزی", names)   # HTML-only, not a feed

    def test_every_source_has_the_required_fields(self):
        for s in sources_mod.all_sources():
            for k in ("name", "url", "tier", "bind", "lang", "markets", "kind"):
                self.assertIn(k, s, f"{s.get('name')} missing {k}")

    def test_unavailable_is_recorded_not_empty(self):
        # Absence is on record, not faked: Kitco / Reuters etc. must be listed.
        names = {n for (n, _u, _r) in sources_mod.UNAVAILABLE}
        self.assertIn("Kitco", names)


# ---------------------------------------------------------------------------
# STORE (throwaway DB)
# ---------------------------------------------------------------------------
class StoreTests(unittest.TestCase):

    def setUp(self):
        import database
        self._db = database.DATABASE_NAME
        self._tmp = tempfile.NamedTemporaryFile(
            suffix=".db", delete=False).name
        database.DATABASE_NAME = self._tmp
        from event_intelligence import store
        self.store = store
        store.ensure_schema()

    def tearDown(self):
        import database
        database.DATABASE_NAME = self._db
        try:
            os.unlink(self._tmp)
        except OSError:
            pass

    def _row(self, uid, title, pub, salience=0.5):
        return {
            "uid": uid, "source": "S", "tier": 2, "lang": "en",
            "title": title, "summary": "", "link": "http://x",
            "published_at": pub, "published_raw": "", "fetched_at": "2026-08-23T00:00:00+00:00",
            "categories": "oil_energy", "markets": "oil", "tone": "down",
            "salience": salience, "event_key": "k", "taxonomy_ver": taxonomy.VERSION,
            "is_economic": 1, "is_analysis": 0, "is_price_forecast": 0,
        }

    def test_upsert_inserts_then_updates_by_uid(self):
        r1 = self.store.upsert_articles([self._row("u1", "First", "2026-08-23T01:00:00+00:00")])
        self.assertEqual(r1["inserted"], 1)
        r2 = self.store.upsert_articles([self._row("u1", "First (edited)", "2026-08-23T01:00:00+00:00")])
        self.assertEqual(r2["updated"], 1)
        self.assertEqual(r2["inserted"], 0)
        rows = self.store.load_articles()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "First (edited)")

    def test_undated_rows_sort_last(self):
        self.store.upsert_articles([
            self._row("u1", "Dated", "2026-08-23T01:00:00+00:00"),
            self._row("u2", "Undated", None),
        ])
        rows = self.store.load_articles()
        self.assertEqual(rows[-1]["title"], "Undated")

    def test_since_filter_keeps_undated(self):
        self.store.upsert_articles([
            self._row("u1", "Old", "2026-08-01T00:00:00+00:00"),
            self._row("u2", "Undated", None),
            self._row("u3", "Recent", "2026-08-23T00:00:00+00:00"),
        ])
        rows = self.store.load_articles(since_iso="2026-08-20T00:00:00+00:00")
        titles = {r["title"] for r in rows}
        self.assertIn("Recent", titles)
        self.assertIn("Undated", titles)      # never dropped by a date filter
        self.assertNotIn("Old", titles)


if __name__ == "__main__":
    unittest.main()
