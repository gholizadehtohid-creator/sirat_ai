"""Populate the local database: universe, snapshot, price history.

    python seed_import.py --universe        # all TSE instruments (needs GitHub)
    python seed_import.py --snapshot        # whole-market snapshot (needs TSETMC)
    python seed_import.py --history N       # daily bars for N screened symbols
    python seed_import.py --flow N          # حقیقی/حقوقی history for N symbols
    python seed_import.py --status          # what is stored right now

On a network that filters Iranian domains, --universe still works: the
reference list is mirrored on GitHub. --snapshot and --history need a route to
tsetmc, i.e. SIRAT_PROXY_URL.

Licensing note: the reference list is fetched at runtime from the pytse-client
repository (GPLv3) into YOUR database. It is deliberately not vendored into
this project, so SIRAT-AI itself carries no GPL obligation.
"""

import argparse
import sys
import time

import config
import database
import settings
import universe
from providers import get_provider


def _reporter(progress):
    """Wrap the caller's progress callback so call sites need no None checks.

    The contract is `progress(done, total, label)`, with `total=None` where a
    step genuinely cannot know its size before it runs -- a caller that draws a
    bar should show an indeterminate one rather than guess a denominator.
    """

    def report(done, total, label=None):
        if progress:
            progress(done, total, label)

    return report


def import_universe(url=None, verbose=True, progress=None):
    """Download and store the instrument universe."""

    report = _reporter(progress)

    if verbose:
        print(f"Fetching instrument universe from {url or settings.UNIVERSE_URL}")

    report(0, 1, "downloading the reference list")

    try:
        records = universe.fetch_reference_list(url=url)
    except Exception as exc:
        print(f"FAILED: {type(exc).__name__}: {exc}")
        print()
        print("The universe list is hosted on GitHub. If that is also blocked,")
        print("set SIRAT_PROXY_URL, or point SIRAT_UNIVERSE_URL at a local copy.")
        return None

    stats = universe.summarise(records)

    report(0, 1, f"storing {len(records)} instruments")

    written = database.save_instruments(records)

    report(1, 1, f"{written} instruments stored")

    if verbose:
        print(f"Stored {written} instruments.")
        print(f"  equities : {stats['equities']}")
        print(f"  by kind  : {stats['by_kind']}")
        print(f"  by board : {stats['by_board']}")

    return stats


def import_snapshot(verbose=True, progress=None):
    """Pull a whole-market snapshot (the fundamental screen's input)."""

    provider = get_provider()

    report = _reporter(progress)

    if verbose:
        print(f"Requesting market snapshot via '{provider.name}'...")

    report(0, 1, f"requesting the market snapshot via {provider.name}")

    result = provider.get_market_snapshot()

    if not result.ok:
        print(f"FAILED: {result.error}")
        for warning in result.warnings:
            print(f"  - {warning}")
        print()
        print("A snapshot needs a live route to tsetmc. Without one the")
        print("fundamental screen can only use fields derivable from cached")
        print("price history (liquidity), not EPS/PE/market cap.")
        return None

    report(0, 1, "storing the snapshot")

    written = database.save_snapshot(result.data)

    report(1, 1, f"{written} rows stored")

    if verbose:
        print(f"Stored snapshot for {written} instruments "
              f"(source: {result.source}, {result.provenance}).")

    return written


def import_history(limit=None, ins_codes=None, count=300, verbose=True,
                   progress=None):
    """Fetch and store daily bars for instruments."""

    provider = get_provider()

    report = _reporter(progress)

    if ins_codes is None:
        rows = database.load_instruments(equities_only=True)
        ins_codes = [r["ins_code"] for r in rows]
        if limit:
            ins_codes = ins_codes[:limit]

    if not ins_codes:
        print("No instruments to fetch. Run --universe first.")
        return 0

    if verbose:
        print(f"Fetching {len(ins_codes)} histories via '{provider.name}' "
              f"({settings.MAX_WORKERS} workers)...")

    report(0, len(ins_codes), f"fetching bars via {provider.name}")

    def on_item(done, total, _item):
        if verbose and (done % 25 == 0 or done == total):
            print(f"  {done}/{total}")
        report(done, total, "fetching bars")

    results = provider.get_histories(ins_codes, count=count, progress=on_item)

    stored = 0
    failed = 0

    # Storing hundreds of frames is itself slow enough to look like a stall, so
    # it gets its own reported phase rather than sitting silently at 100%.
    report(0, len(results), "storing bars")

    for done, (ins_code, result) in enumerate(results.items(), start=1):
        if result.ok:
            stored += database.save_history(ins_code, result.data) > 0
        else:
            failed += 1
        report(done, len(results), "storing bars")

    if verbose:
        print(f"Stored history for {stored} instruments; {failed} failed.")

    return stored


# Sessions re-requested beyond the measured gap on the per-symbol path, so a
# bar the exchange revised after it was first stored is picked up again.
TAIL_MARGIN = 5

# Names taken from the market watch that are re-read from the history
# endpoint as a check. One disagreement and the watch is not used at all.
WATCH_CHECK_SAMPLE = 12

# `py` against our stored close: equal unless the exchange re-based the
# reference price (an ex-date), which is exactly the row to leave to the
# history endpoint. Rial prices are integers, so this is rounding only.
PY_TOLERANCE = 0.0005


def _weekdays_after(date_text, until):
    """Sat..Wed calendar days after `date_text` up to and including `until`.

    An UPPER BOUND on the sessions missed -- holidays are counted -- which is
    the safe direction for sizing a request.
    """

    from datetime import date, timedelta

    day = date.fromisoformat(date_text) + timedelta(days=1)
    count = 0

    while day <= until:
        if day.weekday() in (5, 6, 0, 1, 2):
            count += 1
        day += timedelta(days=1)

    return count


def _watch_bars(snapshot, codes, newest, session):
    """Bars for `session` read off a closed market's watch, for the names
    whose reading can be trusted. Returns {ins_code: one-row frame}.

    A row is used only when BOTH hold:
      * it traded (volume > 0) -- an untraded row may be a previous session's
        figure, since the watch carries no date at all;
      * its `py` equals the close we hold for the session before. That ties
        the row to the session we think it is, and it sends every ex-date
        (where the exchange re-bases py) to the history endpoint instead.
    """

    import pandas as pd

    rows = snapshot.set_index(snapshot["ins_code"].astype(str))

    out = {}

    for code in codes:
        if code not in rows.index:
            continue

        row = rows.loc[code]

        if isinstance(row, pd.DataFrame):
            row = row.iloc[0]

        held_close = newest[code][1]

        try:
            volume = float(row["volume"] or 0)
            reference = float(row["prev_close"] or 0)
            close = float(row["close"] or 0)
        except (TypeError, ValueError):
            continue

        if volume <= 0 or close <= 0 or not held_close or reference <= 0:
            continue

        if abs(reference / float(held_close) - 1.0) > PY_TOLERANCE:
            continue

        out[code] = pd.DataFrame([{
            "date": session, "open": row["open"], "high": row["high"],
            "low": row["low"], "close": close, "volume": volume,
            "value": row["value"], "trades": row["trades"],
        }])

    return out


def _watch_agrees(provider, bars, session, sample=WATCH_CHECK_SAMPLE):
    """Re-read a sample from the history endpoint. (agrees, detail, lagging).

    `lagging` is True when the endpoint has not yet published the session at
    all -- it trails the watch by hours after the close (on 2026-10-31 it
    still ended at 10-28 at 15:10). Then nothing can be verified, and nothing
    can be fetched either.
    """

    import random

    if not bars:
        return True, "nothing taken from the watch", False

    codes = sorted(bars)
    picked = random.Random(session).sample(codes, min(sample, len(codes)))

    results = provider.get_histories(picked, count=3)

    compared = 0

    for code in picked:
        result = results.get(code)

        if result is None or not result.ok:
            continue

        frame = result.data
        match = frame[frame["date"].dt.strftime("%Y-%m-%d") == session]

        if match.empty:
            return False, ("%s: the history endpoint has no %s bar yet"
                           % (code, session)), True

        mine = bars[code].iloc[0]
        theirs = match.iloc[-1]

        for column in ("close", "volume"):
            if abs(float(theirs[column]) - float(mine[column])) > 0.5:
                return False, ("%s %s: watch %s, history %s"
                               % (code, column, mine[column],
                                  theirs[column])), False

        compared += 1

    if not compared:
        return False, "no sample name could be re-read", False

    return (True, "%d of %d sampled names agree" % (compared, len(picked)),
            False)


def update_history(full_count=300, verbose=True, progress=None):
    """Bring every equity up to the last closed session, fetching only that.

    The stored series is kept and only the sessions it lacks are requested.

        nothing missing         one call (the overview) and done
        only the last session   ONE market-watch call for every name that
                                traded and whose py checks out, verified on a
                                sample against the history endpoint
        anything else           per symbol, but `gap + TAIL_MARGIN` bars,
                                not `full_count`
        no bars at all          per symbol, `full_count`

    It used to be the last case for every equity on every run: 818 requests
    for 300 bars each, about five minutes, to add one row per name.

    Returns a dict of counts; `ok` is False only when nothing could be stored
    and something was owed.
    """

    import market_session
    from datetime import date

    provider = get_provider()

    report = _reporter(progress)

    rows = database.load_instruments(equities_only=True)
    codes = [str(r["ins_code"]) for r in rows]

    stats = {"watch": 0, "tail": 0, "full": 0, "failed": 0, "current": 0,
             "session": None, "notes": []}

    def note(message):
        stats["notes"].append(message)
        if verbose:
            print("  " + message)

    if not codes:
        print("No instruments to fetch. Run --universe first.")
        stats["ok"] = False
        return stats

    newest = database.newest_bars(codes)

    # One call decides whether this run costs ~13 requests or ~800, so a
    # transient failure on it (an HTTPError was seen 2026-10-31, gone on the
    # next try) is retried before falling back.
    session_int, state = None, None

    for attempt in range(3):
        try:
            session_int, state = market_session.exchange_session(provider)
            break
        except Exception as exc:                                # noqa: BLE001
            if attempt == 2:
                note("market overview unreadable (%s) -- per-symbol tail "
                     "fetch" % type(exc).__name__)
            else:
                time.sleep(3 * (attempt + 1))

    session = None

    if session_int:
        text = str(session_int)
        session = "%s-%s-%s" % (text[:4], text[4:6], text[6:])

    stats["session"] = session

    closed = state in market_session.MARKET_STATES_SHUT

    # ---- what each instrument is owed --------------------------------------

    fresh = [c for c in codes if c not in newest]
    owed = [c for c in codes if c in newest
            and (session is None or newest[c][0] < session)]
    stats["current"] = len(codes) - len(fresh) - len(owed)

    from_watch = {}
    deferred = set()

    if owed and session and closed:
        tedpix = config.INDICES["TEDPIX"]["insCode"]

        def index_dates():
            index = database.load_index_history(tedpix)
            return ([] if index is None or index.empty
                    else index["date"].dt.strftime("%Y-%m-%d").tolist())

        dates = index_dates()

        if session not in dates:
            # The index is the calendar: it is what says which session came
            # before this one. One call refreshes the whole series.
            result = provider.get_index_history(tedpix)
            if result.ok:
                database.save_index_history(tedpix, result.data)
                dates = index_dates()

        previous = None

        if session in dates and dates.index(session) > 0:
            previous = dates[dates.index(session) - 1]
        elif dates and session > dates[-1]:
            # The index endpoint lags the close as the history one does (on
            # 2026-10-31 neither had the day by 15:30). Freshly refreshed and
            # still behind, its newest date is the session before this one.
            previous = dates[-1]

        if previous:
            one_behind = [c for c in owed if newest[c][0] == previous]

            report(0, 1, "reading the market watch")
            snapshot = provider.get_market_snapshot()

            if snapshot.ok:
                from_watch = _watch_bars(snapshot.data, one_behind, newest,
                                         session)
                agrees, detail, lagging = _watch_agrees(provider, from_watch,
                                                        session)
                note("market watch: %d of %d one-session-behind names usable; "
                     "check: %s" % (len(from_watch), len(one_behind), detail))
                if lagging:
                    # Fetching these per symbol would cost a request each and
                    # return nothing new. They are owed one bar, which the
                    # next run collects once the endpoint has it.
                    deferred = set(one_behind)
                    from_watch = {}
                    note("history endpoint has not published %s yet -- %d "
                         "names one session behind wait for the next run"
                         % (session, len(deferred)))
                elif not agrees:
                    note("market watch REJECTED -- every name goes per-symbol")
                    from_watch = {}
            else:
                note("market watch unreadable: %s" % snapshot.error)
        else:
            note("index history does not yet hold %s -- per-symbol tail fetch"
                 % session)
    elif owed and session and not closed:
        note("session %s not closed (state %s) -- per-symbol tail fetch"
             % (session, state))

    for code, bar in from_watch.items():
        stats["watch"] += database.save_history(code, bar) > 0

    # ---- per symbol, sized to the gap ---------------------------------------

    today = date.today()
    by_count = {}

    for code in owed:
        if code in from_watch or code in deferred:
            continue
        gap = _weekdays_after(newest[code][0], today)
        count = min(full_count, gap + TAIL_MARGIN)
        by_count.setdefault(count, []).append(code)

    if fresh:
        by_count.setdefault(full_count, []).extend(fresh)

    # A bar for a session that has not closed is a draft. Stored, it would
    # make its name read as current on the next run and never be fetched
    # again -- so until the exchange says "F", today's bar is not kept.
    draft = (None if closed
             else market_session.tehran_now().strftime("%Y-%m-%d"))

    pending = sum(len(v) for v in by_count.values())
    done_so_far = [0]

    def on_item(done, total, _item):
        report(done_so_far[0] + done, pending, "fetching missing bars")

    for count, group in sorted(by_count.items()):
        results = provider.get_histories(group, count=count, progress=on_item)
        done_so_far[0] += len(group)

        for code, result in results.items():
            frame = result.data if result.ok else None

            if frame is not None and draft is not None:
                frame = frame[frame["date"].dt.strftime("%Y-%m-%d") < draft]

            if frame is not None and database.save_history(code, frame) > 0:
                stats["full" if code in fresh else "tail"] += 1
            else:
                stats["failed"] += 1

    stored = stats["watch"] + stats["tail"] + stats["full"]

    stats["deferred"] = len(deferred)
    stats["ok"] = stored > 0 or (not set(owed) - deferred and not fresh)

    note("history: %d current, %d from the market watch, %d by tail fetch, "
         "%d full, %d failed (%d requests instead of %d)"
         % (stats["current"], stats["watch"], stats["tail"], stats["full"],
            stats["failed"], pending + (2 if from_watch else 1)
            + min(WATCH_CHECK_SAMPLE, len(from_watch)), len(codes)))

    return stats


def import_indices(verbose=True, progress=None):
    """Fetch and store the published index series (شاخص کل، هم‌وزن)."""

    provider = get_provider()

    report = _reporter(progress)

    stored = {}

    total = len(config.INDICES)

    for done, (key, spec) in enumerate(config.INDICES.items(), start=1):

        if verbose:
            print(f"Fetching {spec['name']} ({key})...")

        report(done - 1, total, spec["name"])

        result = provider.get_index_history(spec["insCode"])

        if not result.ok:
            print(f"  FAILED: {result.error}")
            report(done, total, f"{spec['name']}: failed")
            continue

        written = database.save_index_history(spec["insCode"], result.data)
        stored[key] = written

        first = result.data["date"].iloc[0].date()
        last = result.data["date"].iloc[-1].date()

        report(done, total, f"{spec['name']}: {written} sessions")

        if verbose:
            print(f"  stored {written} sessions ({first} -> {last})")

    return stored


def import_client_type(limit=None, ins_codes=None, verbose=True, progress=None):
    """Fetch and store the daily حقیقی/حقوقی split for equities.

    One request per symbol, like sectors -- there is no bulk client-type route.
    Unlike sectors, this has to be repeated: sector membership changes when a
    company relists, whereas the flow series gains a row every session.
    """

    provider = get_provider()

    report = _reporter(progress)

    if ins_codes is None:
        rows = database.load_instruments(equities_only=True)
        ins_codes = [r["ins_code"] for r in rows]
        if limit:
            ins_codes = ins_codes[:limit]

    if not ins_codes:
        print("No equities stored. Run --universe first.")
        return 0

    if verbose:
        print(f"Fetching client-type history for {len(ins_codes)} equities via "
              f"'{provider.name}' ({settings.MAX_WORKERS} workers)...")

    report(0, len(ins_codes), f"one request per share via {provider.name}")

    def on_item(done, total, _item):
        if verbose and (done % 50 == 0 or done == total):
            print(f"  {done}/{total}")
        report(done, total, "reading حقیقی/حقوقی history")

    results = provider.get_client_type_histories(ins_codes, progress=on_item)

    stored = 0
    failed = 0

    report(0, len(results), "storing flow history")

    for done, (ins_code, result) in enumerate(results.items(), start=1):
        if result.ok:
            # Only the recent tail is kept -- see config.FLOW_HISTORY_SESSIONS.
            # The exchange returns the whole series and cannot be asked for
            # less, so the trim happens here rather than at the provider.
            stored += database.save_client_type_history(
                ins_code, result.data.tail(config.FLOW_HISTORY_SESSIONS)
            ) > 0
        else:
            failed += 1
        report(done, len(results), "storing flow history")

    if verbose:
        coverage = database.client_type_coverage()
        print(f"Stored flow history for {stored} instruments; {failed} failed.")
        print(f"  coverage: {coverage['with_flow']}/{coverage['equities']} "
              f"equities, {coverage['rows']} session-rows, "
              f"newest {coverage['last_date'] or 'none'}")

    return stored


def import_sectors(limit=None, verbose=True, progress=None):
    """Attach a sector to every equity, one instrument-info call each.

    This is the expensive import -- one request per symbol rather than one for
    the whole market -- but there is no bulk route to sector membership, and
    without it there is no such thing as a sector view.
    """

    provider = get_provider()

    report = _reporter(progress)

    rows = database.load_instruments(equities_only=True)
    ins_codes = [r["ins_code"] for r in rows]

    if limit:
        ins_codes = ins_codes[:limit]

    if not ins_codes:
        print("No equities stored. Run --universe first.")
        return 0

    if verbose:
        print(f"Fetching sector for {len(ins_codes)} equities via "
              f"'{provider.name}' ({settings.MAX_WORKERS} workers)...")

    report(0, len(ins_codes), f"one request per share via {provider.name}")

    def on_item(done, total, _item):
        if verbose and (done % 50 == 0 or done == total):
            print(f"  {done}/{total}")
        report(done, total, "reading instrument info")

    results = provider.get_instrument_infos(ins_codes, progress=on_item)

    records = []
    failed = 0

    for ins_code, result in results.items():
        if result.ok:
            records.append(result.data)
        else:
            failed += 1

    report(len(results), len(results), f"storing {len(records)} sectors")

    database.save_instrument_sectors(records)
    database.save_fundamentals(records, source=provider.name)

    coverage = database.sector_coverage()

    if verbose:
        print(f"Sector attached to {coverage['with_sector']} of "
              f"{coverage['equities']} equities across "
              f"{coverage['sectors']} sectors; {failed} failed.")

    return coverage["with_sector"]


def show_status():
    counts = database.instrument_counts()
    snapshot = database.snapshot_info()

    print("=" * 62)
    print("SIRAT AI - LOCAL DATA STATUS")
    print("=" * 62)
    print(f"Instruments stored : {counts['total']}")
    print(f"  equities         : {counts['equities']}")
    print(f"  by board         : {counts['by_board']}")
    print(f"  by kind          : {counts['by_kind']}")
    print(f"With price history : {counts['with_history']}")
    print(f"Snapshot rows      : {snapshot['rows']}"
          f"  (captured {snapshot['captured_at'] or 'never'})")

    sectors = database.sector_coverage()
    print(f"Sector coverage    : {sectors['with_sector']}/{sectors['equities']} "
          f"equities across {sectors['sectors']} sectors")

    flow = database.client_type_coverage()
    print(f"Flow coverage      : {flow['with_flow']}/{flow['equities']} "
          f"equities, {flow['rows']} session-rows "
          f"(newest {flow['last_date'] or 'never'})")

    indices = database.index_coverage()
    print("Indices stored     :", len(indices))
    for key, spec in config.INDICES.items():
        stored = indices.get(spec["insCode"])
        if stored:
            print(f"  {spec['name']:<18} {stored['bars']} sessions "
                  f"({stored['first_date']} -> {stored['last_date']})")
        else:
            print(f"  {spec['name']:<18} not imported")

    print()
    print("Provider settings:")
    for key, value in settings.describe().items():
        print(f"  {key:<24} {value}")


def main():
    parser = argparse.ArgumentParser(description="SIRAT AI data importer")
    parser.add_argument("--universe", action="store_true",
                        help="import the full instrument list")
    parser.add_argument("--snapshot", action="store_true",
                        help="import a whole-market snapshot")
    parser.add_argument("--history", type=int, nargs="?", const=50,
                        help="import daily bars for N equities (default 50)")
    parser.add_argument("--bars", type=int, default=300,
                        help="bars per symbol (default 300)")
    parser.add_argument("--indices", action="store_true",
                        help="import the شاخص کل and شاخص هم‌وزن series")
    parser.add_argument("--sectors", type=int, nargs="?", const=0,
                        help="attach sectors to equities (optionally first N)")
    parser.add_argument("--flow", type=int, nargs="?", const=0,
                        help="import حقیقی/حقوقی history (optionally first N)")
    parser.add_argument("--status", action="store_true",
                        help="show what is stored locally")
    parser.add_argument("--url", help="override the universe URL")

    args = parser.parse_args()

    if not any([args.universe, args.snapshot, args.history, args.indices,
                args.sectors is not None, args.flow is not None, args.status]):
        parser.print_help()
        return 1

    if args.universe:
        if import_universe(url=args.url) is None:
            return 1

    if args.snapshot:
        import_snapshot()

    if args.history:
        import_history(limit=args.history, count=args.bars)

    if args.indices:
        import_indices()

    if args.sectors is not None:
        import_sectors(limit=args.sectors or None)

    if args.flow is not None:
        import_client_type(limit=args.flow or None)

    if args.status:
        show_status()

    return 0


if __name__ == "__main__":
    sys.exit(main())
