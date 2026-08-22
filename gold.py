"""Gold funds (صندوق‌های طلا) -- identification, data acquisition, and listing.

WHY A SEPARATE MODULE
---------------------
Gold ETFs are not equities. They track the price of gold and coins, not a
company's fundamentals, so the equity machinery deliberately excludes them:
`load_instruments(equities_only=True)` gates on `is_equity = 1`, and every
research/screen path uses that gate. Running the fundamental screen or the
six-factor model on a gold fund would be meaningless -- there is no balance
sheet to score.

What DOES apply to them is technical analysis, because that reads a price
series and a gold fund has one. So this module identifies the gold funds,
fetches their price history (the equity collector never does -- 0 of 164 funds
have bars), and hands the technical layer a clean list. The dashboard shows
them in their own section, apart from the equity views, because mixing an ETF
that tracks bullion into a table of stocks ranked on fundamentals would be a
category error dressed as a feature.

IDENTIFICATION
--------------
A fund is a gold fund when kind == 'fund' and its Persian name contains طلا
(gold) or سکه (coin). Verified against the universe: this catches all 14 gold
funds and excludes the two equities (حشکوه, مفاخر) that merely have طلا in
their names. Keyword matching is used rather than a hand-maintained code list
because a new gold fund lists under the same naming convention and should
appear without a code change.
"""

import database

# The words that name a gold fund. سکه (coin) is included because several funds
# are backed by coins rather than bars and carry سکه, not طلا, as the operative
# word (e.g. کهربا -- پشتوانه سکه طلا).
GOLD_WORDS = ("طلا", "سکه")


def is_gold_fund(instrument):
    """True when this instrument row is a gold ETF.

    Takes the dict shape load_instruments returns. The kind check is what keeps
    an equity with طلا in its name out; the keyword check is what keeps a
    non-gold fund out.
    """

    if (instrument.get("kind") or "") != "fund":
        return False

    name = (instrument.get("name_fa") or "")

    return any(word in name for word in GOLD_WORDS)


def gold_funds():
    """Every gold fund in the universe, as instrument dicts, symbol-sorted."""

    funds = [row for row in database.load_instruments(equities_only=False)
             if is_gold_fund(row)]

    funds.sort(key=lambda r: (r.get("symbol_fa") or ""))

    return funds


def gold_fund_codes():
    """Just the ins_codes -- what the history fetcher wants."""

    return [row["ins_code"] for row in gold_funds()]


def fetch_history(count=300, verbose=True, progress=None):
    """Fetch and store daily bars for every gold fund.

    The equity history collector never touches funds, so this is the only path
    that gives them a price_history. Reuses seed_import.import_history with an
    explicit ins_code list -- no new fetch logic, no change to the equity path.
    """

    import seed_import

    codes = gold_fund_codes()

    if not codes:
        if verbose:
            print("No gold funds in the universe. Run the universe import "
                  "first.")
        return 0

    if verbose:
        print("Fetching history for %d gold funds ..." % len(codes))

    return seed_import.import_history(
        ins_codes=codes, count=count, verbose=verbose, progress=progress)


def coverage():
    """How many gold funds currently have stored price history.

    Lets the dashboard say "10 of 14 have data, press fetch" rather than
    silently showing a short list.
    """

    funds = gold_funds()

    with_history = 0
    for row in funds:
        frame = database.load_history_by_inscode(row["ins_code"], limit=1)
        if frame is not None and not frame.empty:
            with_history += 1

    return {"total": len(funds), "with_history": with_history}
