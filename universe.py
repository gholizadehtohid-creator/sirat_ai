"""The tradable instrument universe.

TSETMC lists far more than shares: of the 1394 instruments in the reference
list, only ~818 are ordinary company shares. The rest are rights issues
(حق تقدم), investment funds (صندوق), participation bonds (اوراق مشارکت) and
options -- none of which a fundamental/technical equity screen should touch.

Classification is by ISIN, not by name. A name heuristic looks tempting but
gets it wrong: "بورس" (Tehran Stock Exchange Co.) and "وصندوق" (a pension
investment company) are ordinary listed companies whose names contain the
words for "exchange" and "fund". The ISIN is authoritative.

Iranian ISIN layout:  IR | O | 1 | FOLD | 0001
                       |    |   |    |      +-- issue number
                       |    |   |    +--------- company mnemonic
                       |    |   +-------------- board / market
                       |    +------------------ instrument type
                       +----------------------- country
"""

import json
import logging
import re
import unicodedata

import persian
import settings

log = logging.getLogger(__name__)

# Instrument type = ISIN char[2].
KIND_BY_TYPE_CHAR = {
    "O": "equity-or-derivative",   # needs the board digit to disambiguate
    "R": "rights",                 # حق تقدم
    "T": "fund",                   # صندوق سرمایه‌گذاری
    "B": "debt",                   # اوراق مشارکت / صکوک
    "S": "option",                 # اختیار معامله
}

# Board = ISIN char[3], for type 'O'. Only these carry ordinary shares.
# The remaining O-codes are derivatives: 9/A/B/F are options, 4 is futures
# and 6 is تسه (housing certificates) -- a naive startswith("IRO") filter
# would inject all of those into the equity screen.
EQUITY_BOARDS = {
    "1": "bourse",       # بورس اوراق بهادار تهران
    "3": "farabourse",   # فرابورس ایران
    "7": "farabourse",   # فرابورس ایران
    "5": "sme",          # بازار شرکت‌های کوچک و متوسط (inferred, few members)
}

BOARD_FA = {
    "bourse": "بورس",
    "farabourse": "فرابورس",
    "sme": "بازار پایه / SME",
}

ISIN_RE = re.compile(r"^IR([A-Z])([0-9A-Z])([A-Z0-9]{4})([0-9A-Z]{4})$")

# The issue number of a company's ordinary share. The other equity issues on
# the market watch -- 0002 and 0003, symbols spelled with a trailing 2 or 3
# (شکربن3, رانفور2) -- are the same share on the block and wholesale boards,
# not more companies: 424 such lines on the 2026-11-09 watch, against the 98
# main shares the universe lacked.
MAIN_SHARE_ISSUE = "0001"

# The kind given to an instrument whose company now trades under another
# code. A board move re-issues the ISIN (IRO1KSIM0001 -> IRO7KSIM0001) and
# with it the insCode; the old row stays, as the record of that listing, but
# is no longer an equity -- see seed_import.reconcile_universe.
SUPERSEDED = "superseded"

# The market watch spells with Arabic yeh and kaf; the reference list, and
# Codal's search, with the Persian letters.
_ARABIC_LETTERS = str.maketrans({"ي": "ی", "ى": "ی", "ك": "ک"})


def classify(isin, symbol="", name=""):
    """Return (kind, board) for an instrument.

    kind is one of: equity, rights, fund, debt, option, other.
    board is bourse/farabourse/sme for equities, otherwise None.
    """

    isin = (isin or "").strip().upper()

    match = ISIN_RE.match(isin)

    if not match:
        # No usable ISIN: fall back to the one name signal that is reliable.
        if persian.is_rights(symbol, name):
            return "rights", None
        return "other", None

    type_char, board_char = match.group(1), match.group(2)

    kind = KIND_BY_TYPE_CHAR.get(type_char, "other")

    if kind == "equity-or-derivative":
        board = EQUITY_BOARDS.get(board_char)
        if board:
            return "equity", board
        return "derivative", None

    return kind, None


def is_equity(isin, symbol="", name=""):
    return classify(isin, symbol, name)[0] == "equity"


def _text(value):
    """`value` if it is a string, else "".

    A market-watch frame is not a list of strings. The CDN adapter emits None
    for a line without an insID, and pandas 3 stores a missing value in a str
    column as float NaN -- which is truthy, so `(isin or "").strip()` raised
    AttributeError on it and took the whole reconciliation down with the one
    line (found 2026-11-11).
    """

    return value if isinstance(value, str) else ""


def company_code(isin):
    """The ISIN's four-character company mnemonic (IRO1KSIM0001 -> KSIM).

    It survives a board move, which changes only the board character -- the
    one thing that ties a re-issued listing to the company it was before.
    """

    match = ISIN_RE.match(_text(isin).strip().upper())

    return match.group(3) if match else None


def exchange_spelling(symbol):
    """A ticker as the exchange spells it, in Persian letters.

    For `symbol_raw`, the spelling Codal is searched with. Only Arabic yeh and
    kaf are folded: persian.normalize also turns آ into ا, which Codal answers
    with an empty result (codal_sync.codal_symbol), so it is not used here.
    """

    text = _text(symbol).translate(persian.INVISIBLE)

    return text.translate(_ARABIC_LETTERS).replace(" ", "").strip()


# What markup is made of. A symbol is the one string the panel prints on
# every card, and three of its tables wrote it into the page as it came
# (the security review, 2026-11-23): one entry spelled <svg/onload=...> in
# the reference list -- a third party's file, re-read every week -- or on
# the market watch would have run in the panel. The renderers escape now;
# this is the door. It is not an alphabet: tickers and names carry Persian
# and Arabic letters, digits, ZWNJ, spaces, dots, dashes and Latin letters,
# and all of those pass. None of the 1,502 stored instruments carries one of
# these five characters or a control character (checked 2026-11-23).
_MARKUP = frozenset("<>\"'&")

# The text an instruments row stores as it arrived. kind and board are this
# module's own words, and ins_code is digits by the time it gets here.
_STORED_TEXT = ("symbol_fa", "symbol_raw", "name_fa", "isin")


class UnsafeText(ValueError):
    """An instrument whose symbol, name or ISIN carries markup or a control
    character. The message names the insCode and the character, never the
    text itself: it goes into a log."""


def unsafe_text(*values):
    """Why one of `values` must not be stored, or None.

    A markup character, or a control character (Unicode Cc). Meant for the
    text AS STORED: persian.normalize has by then folded a tab or a line
    break inside a name to a space, and real names have them -- one fund on
    the 2026-11-23 watch carries a tab.
    """

    for value in values:
        for ch in _text(value):
            if ch in _MARKUP:
                return "markup character %r" % ch
            if unicodedata.category(ch) == "Cc":
                return "control character U+%04X" % ord(ch)

    return None


def watch_record(ins_code, symbol, name, isin):
    """An instruments row for a market-watch line that is a main share.

    None for anything else: a fund, a bond, a derivative, a rights issue, a
    block-trade board of a listed company, or a line without a usable ISIN or
    symbol. A missing field may arrive as None or NaN (_text).

    A main share whose symbol or name carries markup or a control character
    raises UnsafeText: refused, and the caller's to count (2026-11-23).
    """

    isin = _text(isin).strip().upper()
    symbol, name = _text(symbol), _text(name)
    code = str(ins_code or "").strip()

    match = ISIN_RE.match(isin)

    if not match or match.group(4) != MAIN_SHARE_ISSUE or not code.isdigit():
        return None

    # No symbol, nothing to find it by: every lookup and the Codal search go
    # by symbol. (Uncoerced, persian.normalize would have spelled NaN "nan".)
    if not persian.normalize_symbol(symbol):
        return None

    kind, board = classify(isin, symbol, name)

    if kind != "equity":
        return None

    record = {
        "ins_code": code,
        "symbol_fa": persian.normalize_symbol(symbol),
        "symbol_raw": exchange_spelling(symbol) or None,
        "name_fa": persian.normalize(name),
        "isin": isin,
        "kind": kind,
        "board": board,
        "is_equity": 1,
    }

    why = unsafe_text(*(record[field] for field in _STORED_TEXT))

    if why:
        raise UnsafeText("instrument %s refused: %s" % (code, why))

    return record


def parse_reference_list(payload, refused=None):
    """Parse the reference symbol map into instrument records.

    Shape: {"<نماد>": {"index": insCode, "code": ISIN, "name": company}}

    An entry whose symbol, name or ISIN carries markup or a control character
    (unsafe_text) is refused: left out, counted in one warning, and its
    insCode appended to `refused` when a list is given. The list is a third
    party's file on GitHub, and it is re-read every week (2026-11-23).
    """

    records = []
    turned_away = []

    for raw_symbol, meta in (payload or {}).items():

        if not isinstance(meta, dict):
            continue

        ins_code = str(meta.get("index") or "").strip()

        if not ins_code.isdigit():
            continue

        isin = (meta.get("code") or "").strip().upper()
        name = meta.get("name") or ""

        kind, board = classify(isin, raw_symbol, name)

        record = {
            "ins_code": ins_code,
            "symbol_fa": persian.normalize_symbol(raw_symbol),
            "symbol_raw": raw_symbol,
            "name_fa": persian.normalize(name),
            "isin": isin or None,
            "kind": kind,
            "board": board,
            "is_equity": 1 if kind == "equity" else 0,
        }

        if unsafe_text(*(record[field] for field in _STORED_TEXT)):
            turned_away.append(ins_code)
            continue

        records.append(record)

    if turned_away:
        if refused is not None:
            refused.extend(turned_away)
        log.warning(
            "reference list: %d entries refused for markup or control "
            "characters in the symbol, name or ISIN (insCode %s%s)",
            len(turned_away), ", ".join(turned_away[:3]),
            ", ..." if len(turned_away) > 3 else "")

    # The reference list can carry duplicate insCodes across renamed symbols;
    # keep one row per insCode so the table's primary key holds.
    deduped = {}

    for record in records:
        deduped.setdefault(record["ins_code"], record)

    return list(deduped.values())


def fetch_reference_list(session=None, url=None):
    """Download the reference universe.

    Uses UNIVERSE_URL (GitHub) rather than tsetmc, deliberately: on a network
    that filters Iranian domains GitHub still resolves, so a full universe is
    obtainable with no proxy at all.
    """

    url = url or settings.UNIVERSE_URL

    close_after = session is None

    if session is None:
        from providers.transport import DeadlineSession
        session = DeadlineSession()
        session.headers["User-Agent"] = settings.USER_AGENT
        if settings.proxies():
            session.proxies.update(settings.proxies())

    try:
        response = session.get(
            url,
            timeout=settings.timeout(),
            verify=settings.VERIFY_TLS
        )
        response.raise_for_status()

        return parse_reference_list(response.json())

    finally:
        if close_after:
            session.close()


def summarise(records):
    """Counts by kind and board, for the import report."""

    by_kind = {}
    by_board = {}

    for record in records:
        by_kind[record["kind"]] = by_kind.get(record["kind"], 0) + 1
        if record["board"]:
            by_board[record["board"]] = by_board.get(record["board"], 0) + 1

    return {
        "total": len(records),
        "equities": sum(1 for r in records if r["is_equity"]),
        "by_kind": dict(sorted(by_kind.items(), key=lambda kv: -kv[1])),
        "by_board": dict(sorted(by_board.items(), key=lambda kv: -kv[1])),
    }


if __name__ == "__main__":

    import sys

    if "--fetch" in sys.argv:
        rows = fetch_reference_list()
        print(json.dumps(summarise(rows), ensure_ascii=False, indent=2))
    else:
        print("usage: python universe.py --fetch")
