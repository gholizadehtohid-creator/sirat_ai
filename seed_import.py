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
