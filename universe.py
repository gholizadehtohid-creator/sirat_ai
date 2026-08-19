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
import re

import persian
import settings

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


def parse_reference_list(payload):
    """Parse the reference symbol map into instrument records.

    Shape: {"<نماد>": {"index": insCode, "code": ISIN, "name": company}}
    """

    records = []

    for raw_symbol, meta in (payload or {}).items():

        if not isinstance(meta, dict):
            continue

        ins_code = str(meta.get("index") or "").strip()

        if not ins_code.isdigit():
            continue

        isin = (meta.get("code") or "").strip().upper()
        name = meta.get("name") or ""

        kind, board = classify(isin, raw_symbol, name)

        records.append({
            "ins_code": ins_code,
            "symbol_fa": persian.normalize_symbol(raw_symbol),
            "symbol_raw": raw_symbol,
            "name_fa": persian.normalize(name),
            "isin": isin or None,
            "kind": kind,
            "board": board,
            "is_equity": 1 if kind == "equity" else 0,
        })

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

    import requests

    url = url or settings.UNIVERSE_URL

    close_after = session is None

    if session is None:
        session = requests.Session()
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
