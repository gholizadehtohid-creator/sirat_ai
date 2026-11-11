"""Populate the local database: universe, snapshot, price history.

    python seed_import.py --universe        # all TSE instruments (needs GitHub)
    python seed_import.py --snapshot        # whole-market snapshot (needs TSETMC)
    python seed_import.py --history N       # daily bars for N screened symbols
    python seed_import.py --flow N          # حقیقی/حقوقی history for N symbols
    python seed_import.py --status          # what is stored right now
    python seed_import.py --repair-universe [--apply]
                                            # reconcile the universe with the
                                            # stored market watch (dry run
                                            # unless --apply)

--repair-universe --apply is the only way a board-moved code is retired (the
snapshot step only adds new companies). Its undo, in sirat_market.db:

    UPDATE instruments SET is_equity = 1, kind = 'equity'
    WHERE kind = 'superseded';

Everything else it writes is additive: the live codes it adds, and the
statement rows it copies to them. It changes nothing keyed by a retired code
-- though codal_sync's next run re-keys that code's Codal letters to the
live one (see reconcile_universe).

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
from collections import namedtuple

import config
import database
import settings
import universe
from providers import LIVE, ProviderResult, get_provider


def _live(result):
    """Whether `result` is what the exchange answered just now.

    The default provider is a chain whose floor is the offline adapter, and
    that adapter answers from this database. Taken as a fetch, a dead network
    stored the database's own rows back as new: at 21:04 on 2026-11-10, the
    minute codal.ir failed to resolve, the snapshot step "fetched" 3,818 rows
    and stamped them 17:34Z -- the 11-09 session, all 656 traded equities
    identical to that day's bars -- and on 11-02 the history step reported
    "fetched (328s)" while its newest bar stayed 10-31. Each false success
    held off a retry for hours. Acquisition keeps only live answers; the
    offline floor is for the panel's reading paths.
    """

    return result is not None and result.ok and result.provenance == LIVE


def _only_live(result):
    """`result` unchanged unless a fallback served it; then a failure."""

    if result is None or not result.ok or result.provenance == LIVE:
        return result

    cause = "; ".join(result.warnings) or "no live provider answered"

    return ProviderResult.failure(
        result.source,
        "no live answer -- %s served its stored copy (%s): %s"
        % (result.source, result.provenance, cause[:200]),
        result.warnings,
    )


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

    # The reference list is stale (see reconcile_universe) and still calls a
    # board-moved company's dead code an equity. Upserting it would make that
    # code an equity again every week, beside the live one.
    retired = {str(r["ins_code"]) for r in database.load_instruments()
               if r.get("kind") == universe.SUPERSEDED}
    if retired:
        records = [r for r in records if r["ins_code"] not in retired]

    report(0, 1, f"storing {len(records)} instruments")

    written = database.save_instruments(records)

    report(1, 1, f"{written} instruments stored")

    if verbose:
        print(f"Stored {written} instruments.")
        print(f"  equities : {stats['equities']}")
        print(f"  by kind  : {stats['by_kind']}")
        print(f"  by board : {stats['by_board']}")

    return stats


def reconcile_universe(snapshot=None, apply=False, verbose=True, retire=True):
    """Bring the instrument list in line with a live market watch.

    The universe comes from a reference list on GitHub (UNIVERSE_URL) that
    nothing checked against the exchange, and the weekly import re-serves it
    unchanged (all 1,394 rows refreshed 2026-11-05). The 2026-11-09 watch
    listed 98 traded main shares it lacks: 81 companies listed since it was
    made (آکو, داروند, شتبریز, فکور, سیمکات, احیا ...), which therefore never
    got a bar, a flow row or a screen; and 17 that changed board. A board move
    re-issues the ISIN (IRO1KSIM0001 -> IRO7KSIM0001) and the insCode with
    it, so فاسمین was followed on a code whose bars end 2026-06-10 while the
    live one traded 123.6M shares that day, its filings kept landing on the
    dead code, and the dead code was excluded as halted.

    Main-share lines (universe.watch_record) the universe lacks are added.
    A board move is RE-POINTED, NOT MIGRATED: the live code is added beside
    the old one and the old row is retired -- kind SUPERSEDED, is_equity 0.
    Its bars, flow and filings stay where they are, the record of that
    listing; rewriting ins_code through price_history, client_type_history,
    history_checked, the Codal tables and the fundamental store would splice
    two listings into one series in a dozen places, irreversibly. Every
    equity loop -- history, flow, sectors, codal_sync, the Codal feed watch
    -- walks is_equity = 1, and the symbol lookups prefer the equity row
    (database.load_instruments), so from the next run the live code is what
    gets bars and flow and what filings land on. The point-in-time and
    backtest universes still load the retired row (with_superseded): a past
    date sees the code that traded then.

    Not everything keyed by the old code stays put, and not everything should.
    codal_sync files the company's letters under the code it searched for, so
    its next run RE-KEYS the old code's codal_letters to the live code, parsed
    flag kept -- and a parsed letter is never fetched again. The statements
    parsed from them would then sit on a code no live reader asks for, so the
    rows the live readers key by ins_code are COPIED to the live code
    (_carry_over): codal_financials and codal_portfolio here, metrics and
    dividends in the fundamental store, and the old code's sector. Copied,
    never moved; only what the live code lacks; a second run copies nothing.
    What is keyed by the tracing number holds one row per filing and cannot
    be copied: codal_filings stays on the old code, where the point-in-time
    reader, which still loads it, finds it; the old monthly reports stay
    there too, and the live code's begin with its next one.

    A code is retired only on evidence: the watch lists a main share with the
    same symbol and the same company code, and the old code is neither on
    the watch nor traded in the newest stored session. A match on only one of
    the two, or an old code still alive, is held back and reported.

    `retire` False is ADD-ONLY, the snapshot step's mode: new companies are
    added, and a board move -- its live code only ever added together with
    the old code's retirement, so that one company is never two equities --
    is held back for `seed_import.py --repair-universe --apply`, the only
    path that retires (2026-11-11: run after every live snapshot, the first
    evening snapshot would have applied all 17 unreviewed). Its undo is in
    this module's docstring. The moves it holds are also listed apart
    ("awaiting"), for bootstrap to say a review is waiting.

    A pair is retired only once everything it carries over is copied
    (_carry_over). If a copy fails -- a fundamental store that cannot be
    opened, a locked file -- the pair is held whole, its live code not added
    and its old code not retired, and reported; the next run finds it
    again. Retired after a failed copy, it could not be: a retired code is
    no rival, and what the copy missed stayed on it for good.

    `snapshot` is a watch frame (default: the stored one). Nothing is written
    unless `apply`; a second run finds nothing to do. A line that cannot be
    read is skipped and counted, not allowed to stop the rest. Returns the
    plan:
    {"added": [record], "retired": [(old row, live record)],
     "held": [(record, reason)], "awaiting": [(old row, live record)],
     "copies": {table: rows}, "unreadable": n, "applied": bool} --
    `copies` what the retirements copy, or would; `awaiting` the board moves
    an add-only run holds (also in `held`).
    """

    if snapshot is None:
        snapshot = database.load_snapshot()

    plan = {"added": [], "retired": [], "held": [], "awaiting": [],
            "copies": {}, "unreadable": 0, "applied": False}

    if snapshot is None or snapshot.empty or "isin" not in snapshot.columns:
        return plan

    on_watch = set(snapshot["ins_code"].astype(str))

    lines = {}
    for row in snapshot.to_dict("records"):
        # One bad line skips itself: a NaN ISIN raised here and cost every
        # other line its place, the new listings included (2026-11-11).
        try:
            record = universe.watch_record(
                row.get("ins_code"), row.get("symbol_fa"), row.get("name_fa"),
                row.get("isin"))
        except Exception:                                       # noqa: BLE001
            plan["unreadable"] += 1
            continue
        if record is not None:
            lines.setdefault(record["ins_code"], record)

    instruments = database.load_instruments()
    known = {str(r["ins_code"]): r for r in instruments}
    equities = [r for r in instruments if r.get("is_equity")]

    by_symbol, by_company = {}, {}
    for row in equities:
        by_symbol.setdefault(row.get("symbol_fa") or "", []).append(row)
        by_company.setdefault(universe.company_code(row.get("isin")),
                              []).append(row)

    traded, market = None, ""

    for code, record in sorted(lines.items()):
        mine = known.get(code)

        if mine is not None and not mine.get("is_equity"):
            continue                       # known under another kind: left be

        company = universe.company_code(record["isin"])
        rivals = [r for r in by_symbol.get(record["symbol_fa"], [])
                  if str(r["ins_code"]) != code]

        if not rivals:
            renamed = [r for r in by_company.get(company, [])
                       if str(r["ins_code"]) != code]
            if mine is None and renamed:
                plan["held"].append((record, "company %s is listed as %s (%s)"
                                     % (company, renamed[0]["symbol_fa"],
                                        renamed[0]["ins_code"])))
            elif mine is None:
                plan["added"].append(record)
            continue

        stranger = [r for r in rivals
                    if universe.company_code(r.get("isin")) != company]
        if stranger:
            plan["held"].append((record, "symbol held by %s (%s)"
                                 % (stranger[0]["ins_code"],
                                    stranger[0].get("isin"))))
            continue

        if traded is None:
            traded = _traded_dates([r["ins_code"] for r in equities])
            market = max(traded.values(), default="")

        alive = [r for r in rivals if str(r["ins_code"]) in on_watch
                 or (market and traded.get(str(r["ins_code"]), "") >= market)]
        if alive:
            plan["held"].append((record, "%s is still listed or trading"
                                 % alive[0]["ins_code"]))
            continue

        if not retire:
            plan["held"].append((record, "board move from %s: retired only "
                                 "by --repair-universe --apply"
                                 % ", ".join(str(r["ins_code"])
                                             for r in rivals)))
            plan["awaiting"].extend((r, record) for r in rivals)
            continue

        if mine is None:
            plan["added"].append(record)
        plan["retired"].extend((r, record) for r in rivals)

    if plan["retired"]:
        plan["copies"], failed = _carry_over(plan["retired"], write=False)
        _hold_failed(plan, failed)
        sectors = _inherit_sectors(plan["retired"], write=False)
        if sectors:
            plan["copies"]["sector"] = sectors

    if apply and (plan["added"] or plan["retired"]):
        # What a board move carries over is copied first, then the live rows
        # are stored, then the sector -- a column of the live row -- and only
        # then is the old code retired. A pair whose copy failed is held
        # whole. Interrupted anywhere, the next run finds the old code beside
        # the live one, stored or not, and finishes: each step fills only
        # what is missing. Copied after the retirement, an interruption
        # between the two would leave the pair unfindable and the copy never
        # made.
        if plan["retired"]:
            plan["copies"], failed = _carry_over(plan["retired"], write=True)
            _hold_failed(plan, failed)
        database.save_instruments(plan["added"])
        if plan["retired"]:
            sectors = _inherit_sectors(plan["retired"], write=True)
            if sectors:
                plan["copies"]["sector"] = sectors
            _retire_instruments([r["ins_code"] for r, _ in plan["retired"]])
        plan["applied"] = bool(plan["added"] or plan["retired"])

    if verbose or plan["applied"]:
        # Unasked (the snapshot step), only the ASCII count line: a Persian
        # symbol printed to a redirected cp1252 stream raises (depth.py).
        _print_reconciliation(plan, apply, examples=8 if verbose else 0)

    return plan


# What a retirement carries to the live code: the tables a LIVE reader asks
# by ins_code -- the panel's statements and the outlook's metrics, NAV and
# dividends (fundamental_outlook.analyse) -- with the columns that, beside
# ins_code, identify a row. Keyed so, a copy is a second row, not a move.
CARRIED_MARKET = (("codal_financials", ("period_end", "period_months")),
                  ("codal_portfolio", ("period_end",)))
CARRIED_FUNDAMENTAL = (("metrics", ("period_end", "period_months")),
                       ("dividends", ("fiscal_year_end",)))

# Keyed by the tracing number, one row per filing: a copy cannot exist.
# Reported, so a reader of the dry run knows they stay on the old code.
STAYING_MARKET = ("codal_filings",)
STAYING_FUNDAMENTAL = ("monthly_reports",)


def _carry_over(pairs, write=False):
    """Copy to each live code what its retired code holds and it lacks.

    `pairs` is the plan's [(old row, live record)]. Rows already under the
    live code are kept, NULL key columns included (`IS`, not the primary
    key, which lets a NULL through twice), and nothing under the old code
    is changed. The fundamental store first, then this database, each pair
    its own transaction in each: a pair whose copy fails is rolled back
    there and named, and the others go on.

    Returns ({table: rows copied}, {old code: why its copy failed}) -- with
    `write` False the rows a write would copy, plus "<table> (stays)" for
    what cannot be copied, counted over the pairs that did not fail. An
    absent store holds nothing to copy. One that is there and cannot be
    read fails every pair: as first written an unopenable store returned
    early, the pairs were retired anyway, and its metrics and dividends were
    never copied -- a retired code is no longer found to copy from (review
    of 2026-11-11).
    """

    import os

    import fundamental_db

    tallies, failed = {}, {}

    def add(old, key, n):
        tally = tallies.setdefault(old, {})
        tally[key] = tally.get(key, 0) + int(n or 0)

    def columns(conn, table):
        return [c[1] for c in conn.execute("PRAGMA table_info(%s)" % table)]

    def copy(conn, table, keys, old, live):
        cols = columns(conn, table)
        if not cols:
            return                                     # no such table here
        others = [c for c in cols if c != "ins_code"]
        where = ("o.ins_code = ? AND NOT EXISTS (SELECT 1 FROM %s n "
                 "WHERE n.ins_code = ? AND %s)"
                 % (table, " AND ".join("n.%s IS o.%s" % (k, k)
                                        for k in keys)))
        if write:
            n = conn.execute(
                "INSERT OR IGNORE INTO %s (ins_code, %s) SELECT ?, %s "
                "FROM %s o WHERE %s"
                % (table, ", ".join(others),
                   ", ".join("o." + c for c in others), table, where),
                (live, old, live)).rowcount
        else:
            n = conn.execute("SELECT COUNT(*) FROM %s o WHERE %s"
                             % (table, where), (old, live)).fetchone()[0]
        add(old, table, n)

    def staying(conn, table, old):
        if columns(conn, table):
            add(old, table + " (stays)", conn.execute(
                "SELECT COUNT(*) FROM %s WHERE ins_code = ?" % table,
                (old,)).fetchone()[0])

    def why(store, exc):
        return "%s: %s: %s" % (store, type(exc).__name__, str(exc)[:160])

    codes = [(str(old["ins_code"]), str(new["ins_code"]))
             for old, new in pairs]

    def into(store, connect, carried, staying_tables):
        try:
            conn = connect()
        except Exception as exc:                                # noqa: BLE001
            for old, _ in codes:
                failed.setdefault(old, why(store, exc))
            return
        try:
            for old, live in codes:
                if old in failed:
                    continue
                try:
                    for table, keys in carried:
                        copy(conn, table, keys, old, live)
                    for table in staying_tables:
                        staying(conn, table, old)
                    if write:
                        conn.commit()
                except Exception as exc:                        # noqa: BLE001
                    conn.rollback()
                    failed[old] = why(store, exc)
        finally:
            conn.close()

    # Absent is nothing at the path at all: a directory there is a store
    # that cannot be opened, not a missing one (fundamental_db.exists() asks
    # only for a file).
    if os.path.exists(fundamental_db.path()):
        into("fundamental store", fundamental_db._connect,
             CARRIED_FUNDAMENTAL, STAYING_FUNDAMENTAL)

    database.create_database()
    into("market database", database._connect, CARRIED_MARKET,
         STAYING_MARKET)

    counts = {}
    for old, tally in tallies.items():
        if old not in failed:
            for key, n in tally.items():
                counts[key] = counts.get(key, 0) + n

    return counts, failed


def _hold_failed(plan, failed):
    """Hold, whole, the board moves whose copy failed (_carry_over).

    Neither the live code is added nor the old one retired: one company is
    never two equities, and the next run finds the pair again and copies
    what is still missing.
    """

    if not failed:
        return

    stuck = {}
    for old, new in plan["retired"]:
        reason = failed.get(str(old["ins_code"]))
        if reason:
            stuck.setdefault(str(new["ins_code"]), (new, reason))

    plan["retired"] = [(old, new) for old, new in plan["retired"]
                       if str(new["ins_code"]) not in stuck]
    plan["added"] = [r for r in plan["added"]
                     if str(r["ins_code"]) not in stuck]
    plan["held"].extend(
        (new, "copy to the live code failed, neither added nor retired -- %s"
         % reason) for new, reason in stuck.values())


def _inherit_sectors(pairs, write=False):
    """Give each live code its retired code's sector, where it has none.

    The sector is the company's, and a board move keeps it. Only into a live
    row without one: the exchange's own word wins. A column of the live row,
    so written once that row is stored (reconcile_universe). Returns how
    many live codes take one, or would with `write` False.
    """

    database.create_database()

    taken = 0

    conn = database._connect()
    try:
        for old, new in pairs:
            if not old.get("sector_name"):
                continue
            live = str(new["ins_code"])
            held = conn.execute(
                "SELECT sector_name FROM instruments WHERE ins_code = ?",
                (live,)).fetchone()
            if held is not None and held[0] is not None:
                continue
            taken += 1
            if write:
                conn.execute(
                    "UPDATE instruments SET sector_code = ?, sector_name = ? "
                    "WHERE ins_code = ? AND sector_name IS NULL",
                    (old.get("sector_code"), old["sector_name"], live))
        if write:
            conn.commit()
    finally:
        conn.close()

    return taken


def _retire_instruments(ins_codes):
    """Stop treating these codes as equities (universe.SUPERSEDED)."""

    if not ins_codes:
        return

    from datetime import datetime, timezone

    database.create_database()

    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")

    conn = database._connect()
    try:
        conn.executemany(
            "UPDATE instruments SET is_equity = 0, kind = ?, updated_at = ? "
            "WHERE ins_code = ?",
            [(universe.SUPERSEDED, stamp, str(c)) for c in ins_codes])
        conn.commit()
    finally:
        conn.close()


# The undo of a retirement. Every retired row was an equity: the board-move
# test (reconcile_universe) only ever pairs equity rows.
UNDO_RETIREMENT = ("UPDATE instruments SET is_equity = 1, kind = 'equity' "
                   "WHERE kind = 'superseded';")


def _print_reconciliation(plan, apply, examples=8):
    added, retired, held = plan["added"], plan["retired"], plan["held"]

    print(f"Universe against the market watch: {len(added)} main shares to "
          f"add, {len(retired)} board-moved codes to retire, "
          f"{len(held)} held back.")

    for record in added[:examples]:
        print(f"  + {record['symbol_fa']:<12} {record['ins_code']:>18}  "
              f"{record['isin']}  {record['board']}")
    if examples and len(added) > examples:
        print(f"  ... and {len(added) - examples} more")

    for old, new in retired[:examples]:
        print(f"  ~ {old['symbol_fa']:<12} {old['ins_code']:>18} "
              f"{old.get('isin')} -> {new['ins_code']} {new['isin']}")
    if examples and len(retired) > examples:
        print(f"  ... and {len(retired) - examples} more")

    for record, reason in held[:examples]:
        print(f"  ? {record['symbol_fa']:<12} {record['ins_code']:>18}  "
              f"{reason}")
    if examples and len(held) > examples:
        print(f"  ... and {len(held) - examples} more")

    if plan.get("unreadable"):
        print(f"  {plan['unreadable']} watch lines could not be read and "
              f"were skipped.")

    copies = plan.get("copies") or {}
    carried = {k: v for k, v in copies.items() if not k.endswith("(stays)")}
    stays = {k: v for k, v in copies.items() if k.endswith("(stays)")}
    if retired:
        verb = "Copied" if plan["applied"] else "To copy"
        print(f"{verb} from the retired codes to the live ones (never moved): "
              + (", ".join(f"{k} {v}" for k, v in sorted(carried.items()))
                 or "nothing"))
        if stays:
            print("Staying on the retired codes, one row per filing: "
                  + ", ".join(f"{k[:-len(' (stays)')]} {v}"
                              for k, v in sorted(stays.items())))

    if not apply and (added or retired):
        print("Dry run: nothing written. Re-run with --apply to write.")
    elif plan["applied"]:
        print("Written.")

    if retired:
        print(f"Undo the retirements with: {UNDO_RETIREMENT}")


def import_snapshot(verbose=True, progress=None, return_plan=False):
    """Pull a whole-market snapshot (the fundamental screen's input).

    Returns the rows stored, None when no live snapshot came. With
    `return_plan`, (rows, plan): the plan of the add-only reconciliation run
    on it (reconcile_universe), None when that did not run. Its "awaiting"
    board moves are held for a review that, unreported, nobody knew was
    waiting: bootstrap says so on every run (2026-11-11).
    """

    provider = get_provider()

    report = _reporter(progress)

    if verbose:
        print(f"Requesting market snapshot via '{provider.name}'...")

    report(0, 1, f"requesting the market snapshot via {provider.name}")

    result = _only_live(provider.get_market_snapshot())

    if not result.ok:
        print(f"FAILED: {result.error}")
        for warning in result.warnings:
            print(f"  - {warning}")
        print()
        print("A snapshot needs a live route to tsetmc. Without one the")
        print("fundamental screen can only use fields derivable from cached")
        print("price history (liquidity), not EPS/PE/market cap.")
        return (None, None) if return_plan else None

    report(0, 1, "storing the snapshot")

    written = database.save_snapshot(result.data)

    report(1, 1, f"{written} rows stored")

    if verbose:
        print(f"Stored snapshot for {written} instruments "
              f"(source: {result.source}, {result.provenance}).")

    # The live watch is the only list here the exchange itself vouches for.
    # A failure to reconcile must not cost the snapshot just stored. ADD-ONLY
    # (retire=False): a retirement is reviewed before it is made, through
    # --repair-universe --apply, never by an unattended evening snapshot.
    plan = None
    try:
        plan = reconcile_universe(result.data, apply=True, verbose=verbose,
                                  retire=False)
    except Exception as exc:                                    # noqa: BLE001
        print(f"universe reconciliation failed: {type(exc).__name__}: {exc}")

    return (written, plan) if return_plan else written


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
        if _live(result):
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

# ...but only a re-read that came back LIVE is a check. Fewer live answers
# than half the sample, and fewer than this, settle nothing either way: the
# watch is UNVERIFIABLE. As first written (2026-11-11) the check accepted
# the watch on one live answer in twelve and rejected it on none -- which
# sent every name per symbol (~660 requests) at an endpoint that had just
# failed twelve, and again after each cooldown.
WATCH_CHECK_MIN_LIVE = 6

# Why the sample check could not decide (_watch_agrees). Both defer the
# names one session behind; only LAGGING is a success.
LAGGING = "lagging"            # answered live, without the session's bar yet
UNVERIFIABLE = "unverifiable"  # too few sampled names answered live at all

# The first history requests of a run are a canary, the sample's included:
# until they meet the sample's bar (_live_needed), no more than this many
# are made. A dead or refusing endpoint is then ~15 requests a retry, not
# 818.
HISTORY_CANARY = 12

# `py` against our stored close: equal unless the exchange re-based the
# reference price (an ex-date), which is exactly the row to leave to the
# history endpoint. Rial prices are integers, so this is rounding only.
PY_TOLERANCE = 0.0005

# A name the market watch does not list at all is delisted or suspended: on
# 2026-11-05 all 73 equities behind the session were absent, newest bars from
# 2025-07 to 2026-09. Once its own bars agree -- it did not trade in the
# newest session -- it is marked (history_checked) and re-read this often,
# not every run. A name that reopens comes back onto the watch and is fetched
# at once, and a mark the name has traded since no longer stands
# (_still_absent).
#
# What the skip costs, and why it stays. The history endpoint publishes a bar
# for every session a name is LISTED, so a halted name gained a zero-volume
# bar at its frozen close every session: 745 equity bars a session until
# 2026-11-07 and about 670 since, traded counts unchanged, because the ~72
# halted names marked on 11-08 are skipped until 11-15. The re-read's tail
# request spans the gap (gap + TAIL_MARGIN bars), so those rows arrive late
# rather than never, and meanwhile nothing that decides reads them:
# halted_codes, newest_flow_owed, fundamentals' stale_days and ranking's
# untraded-bar check all go by volume > 0, and the name's own newest bar is a
# zero-volume one either way. Keeping them current would cost ~70 requests a
# run for rows that record no trade.
ABSENT_RECHECK_DAYS = 7

# Fewer watch rows than this cannot be a whole market.
WATCH_MIN_ROWS = 300

# ...and a watch listing fewer than this share of the names that traded in
# the newest stored session is a partial answer, whatever its size: absence
# from it proves nothing. The row floor was no test of that. On 10-31 the
# feed alternated between 1,022 and 3,451 rows in one afternoon, and a
# 2,524-row answer read about 07:00 on 11-08 marked 128 equities absent, 38
# of which traded that day (ملت 58.4M shares). Over every capture in
# depth_manifest since 10-15, answers of 592-1,038 rows listed 78-81% of the
# previous session's traders, pre-open and opening ones of 2,452-2,956 rows
# 94.4-96.6%, whole ones 98.2-99.1%; a few in-session answers sat at
# 97.5-97.8%, which is why a name is also never marked while its own bars
# show it trading (update_history).
WATCH_MIN_SHARE = 0.97


def _traded_dates(ins_codes):
    """{ins_code: newest date it traded}: a bar with volume, not any bar."""

    database.create_database()

    wanted = {str(c) for c in ins_codes}

    conn = database._connect()
    try:
        rows = conn.execute(
            "SELECT ins_code, MAX(date) FROM price_history "
            "WHERE volume > 0 GROUP BY ins_code").fetchall()
    finally:
        conn.close()

    return {str(code): str(day)[:10] for code, day in rows
            if day and str(code) in wanted}


def _newest_trade(frame):
    """The newest date in a bar frame with volume, 'YYYY-MM-DD', or None."""

    import pandas as pd

    if frame is None or frame.empty or "volume" not in frame.columns:
        return None

    volume = pd.to_numeric(frame["volume"], errors="coerce").fillna(0)
    dates = pd.to_datetime(frame.loc[volume > 0, "date"],
                           errors="coerce").dropna()

    return None if dates.empty else dates.max().strftime("%Y-%m-%d")


def _still_absent(ins_codes, traded, grace_days=0):
    """The names whose off-the-watch mark still stands.

    Marked in the last ABSENT_RECHECK_DAYS and not traded on or after the day
    of the mark. A trade since says the mark was wrong -- the watch it came
    from was partial -- or that the name has reopened; either way it is
    fetched like any other name. Until 2026-11-11 a mark stood for the whole
    week regardless, and the flow step skipped every marked name: 72 that
    traded since 11-08 had no flow for their last session, and traded bars
    without a flow row went from none on 11-01..11-04 to 40, 45, 57 and 60
    on 11-07..11-10.

    `grace_days` also voids a mark whose name traded up to that many days
    BEFORE it -- for the flow step, where the question is whether the
    endpoint still has a session to answer for, not whether the name trades
    again: 11 of those 72 traded until the session before their mark.
    """

    from datetime import date, timedelta

    checked = database.history_checked_dates()
    cutoff = (date.today() - timedelta(days=ABSENT_RECHECK_DAYS)).isoformat()

    out = set()

    for code in (str(c) for c in ins_codes):
        mark = checked.get(code, "")
        if mark <= cutoff:
            continue
        since = (date.fromisoformat(mark[:10])
                 - timedelta(days=grace_days)).isoformat()
        if traded.get(code, "") < since:
            out.add(code)

    return out


def _watch_listing(frame, traded):
    """(codes, listed, traders) for a market-watch answer.

    `codes` is the set the watch lists when it is a whole market and None
    when it is partial (WATCH_MIN_ROWS, WATCH_MIN_SHARE). `listed` of the
    `traders` -- the names that traded in the newest stored session -- are
    on it. With no such names there is nothing to judge it by, and it is
    treated as partial.
    """

    codes = (set(frame["ins_code"].astype(str))
             if "ins_code" in frame.columns else set())

    newest = max(traded.values(), default=None)
    traders = {code for code, day in traded.items() if day == newest}
    listed = len(traders & codes)

    whole = (len(frame) >= WATCH_MIN_ROWS and bool(traders)
             and listed >= len(traders) * WATCH_MIN_SHARE)

    return (codes if whole else None), listed, len(traders)


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


# What _watch_agrees found: whether the watch can be used, why, what stops a
# decision (None, LAGGING or UNVERIFIABLE), and the history requests it made
# (`asked`) and how many came back live -- the run's canary counts them.
SampleCheck = namedtuple("SampleCheck", "agrees detail wait asked live")


def _live_needed(asked):
    """Live answers `asked` history requests need before they say anything.

    Half of them, and no more than WATCH_CHECK_MIN_LIVE: one bar for the
    verification sample and for the canary, which it is the start of.
    """

    return min(WATCH_CHECK_MIN_LIVE, (asked + 1) // 2)


def _watch_agrees(provider, bars, session, sample=WATCH_CHECK_SAMPLE):
    """Re-read a sample from the history endpoint (a SampleCheck).

    `wait` is LAGGING when the endpoint answered without the session -- it
    trails the watch by hours after the close (on 2026-10-31 it still ended
    at 10-28 at 15:10) -- and UNVERIFIABLE when too few of the sample came
    back live to say anything (WATCH_CHECK_MIN_LIVE). Either way nothing can
    be verified, and fetching per symbol would buy nothing: the names are
    deferred. Only a decided check carries `wait` None: agreement, which
    needs that many live comparisons, or a live disagreement.
    """

    import random

    if not bars:
        return SampleCheck(True, "nothing taken from the watch", None, 0, 0)

    codes = sorted(bars)
    picked = random.Random(session).sample(codes, min(sample, len(codes)))

    results = provider.get_histories(picked, count=3)

    # A stored copy served by the offline floor has no new bar, and would read
    # as "the endpoint has not published yet"; a failure says nothing at all.
    # Neither is a check, and too few real ones cannot vouch for -- or
    # condemn -- a watch of six hundred names.
    live = [code for code in picked if _live(results.get(code))]
    need = _live_needed(len(picked))

    if len(live) < need:
        return SampleCheck(False, "%d of %d sampled names came back live, %d "
                           "needed" % (len(live), len(picked), need),
                           UNVERIFIABLE, len(picked), len(live))

    for code in live:
        frame = results[code].data
        match = frame[frame["date"].dt.strftime("%Y-%m-%d") == session]

        if match.empty:
            return SampleCheck(False, "%s: the history endpoint has no %s bar "
                               "yet" % (code, session), LAGGING, len(picked),
                               len(live))

        mine = bars[code].iloc[0]
        theirs = match.iloc[-1]

        for column in ("close", "volume"):
            if abs(float(theirs[column]) - float(mine[column])) > 0.5:
                return SampleCheck(False, "%s %s: watch %s, history %s"
                                   % (code, column, mine[column],
                                      theirs[column]), None, len(picked),
                                   len(live))

    return SampleCheck(True, "%d of %d sampled names agree"
                       % (len(live), len(picked)), None, len(picked),
                       len(live))


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

    A host that does not answer costs a handful of requests, not a sweep:
    with neither the overview nor the watch the run stops before any history
    request; a sample check that could not be made (UNVERIFIABLE) is the
    only history request of its run; and otherwise the first history
    requests are a canary (HISTORY_CANARY) the rest wait on.

    Returns a dict of counts; `ok` is False when something was owed and
    nothing could be stored, and whenever the sample or the canary answered
    too little to vouch for the endpoint -- so bootstrap retries after its
    cooldown instead of reading the deferral as a fetch. `unasked` counts
    the names such a run left for the next attempt.
    """

    import market_session
    from datetime import date

    provider = get_provider()

    report = _reporter(progress)

    rows = database.load_instruments(equities_only=True)
    codes = [str(r["ins_code"]) for r in rows]

    stats = {"watch": 0, "tail": 0, "full": 0, "failed": 0, "current": 0,
             "unasked": 0, "session": None, "notes": []}

    def note(message):
        stats["notes"].append(message)
        if verbose:
            print("  " + message)

    if not codes:
        print("No instruments to fetch. Run --universe first.")
        stats["ok"] = False
        return stats

    newest = database.newest_bars(codes)

    # The newest session each name TRADED in, before this run stores
    # anything: what the watch's completeness and every off-the-watch mark
    # are judged by. Updated below as bars are stored.
    traded = _traded_dates(codes)

    # Requests this run made: the overview, watch and index calls, and the
    # history requests with how many of them came back live (the canary).
    calls = [0]
    probe = {"asked": 0, "live": 0}

    # One call decides whether this run costs ~13 requests or ~800, so a
    # transient failure on it (an HTTPError was seen 2026-10-31, gone on the
    # next try) is retried before falling back.
    session_int, state = None, None

    for attempt in range(3):
        calls[0] += 1
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
    snapshot = None
    # The history endpoint answered too little of the sample, or of the
    # canary, to be vouched for: whatever was stored, the run reports
    # failure, so it is retried after the cooldown.
    unverified = False

    def finish(stored_ok):
        stats["deferred"] = len(deferred)
        stats["absent"] = len(absent)
        stats["ok"] = stored_ok and not unverified
        note("history: %d current, %d from the market watch, %d by tail "
             "fetch, %d full, %d failed, %d not asked (%d requests instead "
             "of %d)"
             % (stats["current"], stats["watch"], stats["tail"],
                stats["full"], stats["failed"], stats["unasked"],
                calls[0] + probe["asked"], len(codes)))
        return stats

    if owed:
        report(0, 1, "reading the market watch")
        calls[0] += 1
        snapshot = _only_live(provider.get_market_snapshot())
        if not snapshot.ok:
            note("market watch unreadable: %s" % snapshot.error)

    listed = None

    if snapshot is not None and snapshot.ok:
        listed, seen, traders = _watch_listing(snapshot.data, traded)
        if listed is None:
            note("market watch (%d rows) lists %d of the %d names that "
                 "traded last -- partial; absence from it proves nothing"
                 % (len(snapshot.data), seen, traders))

    absent = set()

    if listed is not None:
        unlisted = [c for c in owed if c not in listed]
        absent = _still_absent(unlisted, traded)
        if unlisted:
            note("%d names behind are not on the market watch; %d marked in "
                 "the last %d days and not traded since are skipped"
                 % (len(unlisted), len(absent), ABSENT_RECHECK_DAYS))

    # ---- an exchange that does not answer -----------------------------------
    # No overview and no watch: nothing says what is owed, and a host that
    # refused both refuses a request per name as well. Until 2026-11-11 this
    # went on to ask every name per symbol -- ~818 requests, each with its
    # transport retries -- and, a stored copy no longer counting as a fetch,
    # did it again after every RETRY_COOLDOWN.
    if session is None and snapshot is not None and not snapshot.ok:
        stats["unasked"] = len(owed) + len(fresh)
        note("neither the market overview nor the market watch answered -- "
             "no history requested; %d names wait for the next attempt"
             % stats["unasked"])
        return finish(False)

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
            calls[0] += 1
            result = provider.get_index_history(tedpix)
            if _live(result):
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

            if snapshot.ok:
                from_watch = _watch_bars(snapshot.data, one_behind, newest,
                                         session)
                check = _watch_agrees(provider, from_watch, session)
                probe["asked"] += check.asked
                probe["live"] += check.live
                note("market watch: %d of %d one-session-behind names usable; "
                     "check: %s" % (len(from_watch), len(one_behind),
                                    check.detail))
                if check.wait:
                    # Fetching these per symbol would cost a request each and
                    # return nothing new -- or, the sample unanswered, ask
                    # ~660 times an endpoint that has just failed it. They
                    # are owed one bar, which the next run collects.
                    deferred = set(one_behind)
                    from_watch = {}
                    if check.wait == UNVERIFIABLE:
                        unverified = True
                        note("history endpoint answered too little of the "
                             "sample to check the watch -- %d names one "
                             "session behind wait; the step reports failure "
                             "and is retried after its cooldown"
                             % len(deferred))
                    else:
                        note("history endpoint has not published %s yet -- "
                             "%d names one session behind wait for the next "
                             "run" % (session, len(deferred)))
                elif not check.agrees:
                    note("market watch REJECTED -- every name goes per-symbol")
                    from_watch = {}
        else:
            note("index history does not yet hold %s -- per-symbol tail fetch"
                 % session)
    elif owed and session and not closed:
        note("session %s not closed (state %s) -- per-symbol tail fetch"
             % (session, state))

    for code, bar in from_watch.items():
        if database.save_history(code, bar) > 0:
            stats["watch"] += 1
            # Taken only from rows that traded (_watch_bars).
            traded[code] = max(traded.get(code, ""), session)

    # ---- per symbol, sized to the gap ---------------------------------------

    today = date.today()
    work = []                                   # (code, bars to request)

    for code in owed:
        if code in from_watch or code in deferred or code in absent:
            continue
        gap = _weekdays_after(newest[code][0], today)
        work.append((code, min(full_count, gap + TAIL_MARGIN)))

    work.extend((code, full_count) for code in fresh)

    # A bar for a session that has not closed is a draft. Stored, it would
    # make its name read as current on the next run and never be fetched
    # again -- so until the exchange says "F", today's bar is not kept.
    draft = (None if closed
             else market_session.tehran_now().strftime("%Y-%m-%d"))

    pending = len(work)
    done_so_far = [0]
    swept = []

    def on_item(done, total, _item):
        report(done_so_far[0] + done, pending, "fetching missing bars")

    def fetch(batch):
        by_count = {}
        for code, count in batch:
            by_count.setdefault(count, []).append(code)

        for count, group in sorted(by_count.items()):
            results = provider.get_histories(group, count=count,
                                             progress=on_item)
            done_so_far[0] += len(group)
            probe["asked"] += len(group)

            for code, result in results.items():
                live = _live(result)
                probe["live"] += live
                frame = result.data if live else None

                if frame is not None and draft is not None:
                    frame = frame[frame["date"].dt.strftime("%Y-%m-%d")
                                  < draft]

                if frame is not None and database.save_history(code,
                                                               frame) > 0:
                    stats["full" if code in fresh else "tail"] += 1
                    day = _newest_trade(frame)
                    if day and day > traded.get(code, ""):
                        traded[code] = day
                    if listed is not None and code not in listed:
                        swept.append(code)
                else:
                    stats["failed"] += 1

    # A sample that could not be checked has already shown the endpoint
    # failing, and fails the run, which is retried after the cooldown -- so
    # what is asked now would be asked again on every retry. Until
    # 2026-11-11 it was: with 3 of the 12 sampled names live the canary saw
    # a live answer and stood aside, and every name not deferred -- those
    # two sessions behind, new listings -- went per symbol each time, the
    # ones that failed included (72 requests a retry in the review's
    # scenario; up to ~245 every ten minutes on the live universe). None of
    # them is asked: a retry costs the sample.
    if work and unverified:
        stats["unasked"] += len(work)
        note("%d more names not asked -- the next attempt starts again with "
             "the sample" % len(work))
        work = []

    def answering():
        asked, live = probe["asked"], probe["live"]
        return asked > 0 and live >= _live_needed(asked)

    # The canary. Until this run's history requests -- the sample's count --
    # have met the sample's own bar (_live_needed), at most HISTORY_CANARY
    # are made, the names likeliest to answer, the ones that traded last,
    # first. A refusing endpoint then costs a dozen requests a retry, not
    # one per name. As first written one live answer released the rest: a
    # host shedding load that let one request in twelve through got the
    # whole sweep.
    # Judged only once the run has asked HISTORY_CANARY names: a sample that
    # decided on one or two names (an evening when only those were a session
    # behind) passed the bar on a single live answer and released ~800
    # failing requests at a host letting one in twelve through (third review,
    # 2026-11-11). Topping up to the canary costs a healthy host nothing:
    # those names are fetched anyway.
    if work and (probe["asked"] < HISTORY_CANARY or not answering()):
        work.sort(key=lambda item: traded.get(item[0], ""), reverse=True)
        room = max(0, HISTORY_CANARY - probe["asked"])
        fetch(work[:room])
        work = work[room:]
        if work and not answering():
            unverified = True
            stats["unasked"] += len(work)
            note("%d of the %d history requests made came back live, %d "
                 "needed -- %d names not asked; the step reports failure "
                 "and the next attempt starts again"
                 % (probe["live"], probe["asked"],
                    _live_needed(probe["asked"]), len(work)))
            work = []

    fetch(work)

    if swept:
        # Off the watch is not proof of not trading, so a name is marked only
        # when its own bars agree: no trade in the newest session the market
        # traded in. ملت was marked on 11-08 with a traded 11-07 bar in hand.
        market = max(traded.values(), default="")
        trading = [c for c in swept if traded.get(c, "") >= market]
        swept = [c for c in swept if traded.get(c, "") < market]
        if trading:
            note("%d names off the market watch traded on %s -- not marked "
                 "absent" % (len(trading), market))
        if swept:
            database.mark_history_checked(swept, date.today().isoformat())

    stored = stats["watch"] + stats["tail"] + stats["full"]

    return finish(stored > 0 or (not set(owed) - deferred - absent
                                 and not fresh))


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

        result = _only_live(provider.get_index_history(spec["insCode"]))

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


def import_client_type(limit=None, ins_codes=None, verbose=True, progress=None,
                       incremental=False):
    """Fetch and store the daily حقیقی/حقوقی split for equities.

    One request per symbol, like sectors -- there is no bulk client-type route.
    Unlike sectors, this has to be repeated: sector membership changes when a
    company relists, whereas the flow series gains a row every session.

    `incremental` asks only the names that traded since their flow series
    ends (database.newest_flow_owed). The endpoint returns a name's whole
    series and cannot be asked for less, so the saving is in names, not rows.
    GetClientTypeAll is one call for the market but carries no rial values,
    which moneyflow and the E21 log both read, so it cannot stand in.
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

    if incremental:
        owed = database.newest_flow_owed(ins_codes)
        # A name the history step marked off the market watch whose last
        # trade is long past gains no flow, and its endpoint answers nothing
        # at all. One that traded since its mark, or in the days before it,
        # is owed like any other: skipping every marked name for the week
        # left 72 equities without flow for their last session
        # (_still_absent).
        owed -= _still_absent(owed, _traded_dates(owed),
                              grace_days=ABSENT_RECHECK_DAYS)
        skipped = len(ins_codes) - len(owed)
        ins_codes = [c for c in ins_codes if str(c) in owed]
        if verbose:
            print(f"Flow: {skipped} equities already hold their last traded "
                  f"session; {len(ins_codes)} owed.")
        if not ins_codes:
            # Nothing owed is success, not failure: bootstrap reads a 0 as
            # "the step failed".
            return True

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
        if _live(result):
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


# The first instrument-info requests of a sector run. If none of them comes
# back live, the rest are not asked: a dead or refusing host costs this many
# requests an attempt, not one per equity.
SECTOR_CANARY = 3


def import_sectors(limit=None, verbose=True, progress=None,
                   missing_only=False):
    """Attach a sector to every equity, one instrument-info call each.

    This is the expensive import -- one request per symbol rather than one for
    the whole market -- but there is no bulk route to sector membership, and
    without it there is no such thing as a sector view.

    `missing_only` asks only the equities without one (sector_name NULL):
    bootstrap's mode. A company's sector does not move, so a new listing is
    one request, not a refetch of every equity: adding 98 names on
    2026-11-11 would have cost ~900 requests once coverage fell under the
    90% line, and a listing that left it above never got a sector at all.
    Nothing missing, no request.

    Returns the equities with a sector, and 0 when names were asked and not
    one answer came back live -- a failure, not a fetch. It used to return
    the stored coverage whatever happened, so bootstrap recorded a run whose
    every request failed as fetched, and a new listing whose first request
    failed waited MAX_AGE, 30 days, for its next (2026-11-11). Such a run
    stops after the first SECTOR_CANARY requests, so retrying it after the
    cooldown stays cheap.
    """

    provider = get_provider()

    report = _reporter(progress)

    rows = database.load_instruments(equities_only=True)

    if missing_only and rows:
        rows = [r for r in rows if r.get("sector_name") is None]
        if not rows:
            if verbose:
                print("Every equity has a sector; nothing to fetch.")
            return database.sector_coverage()["with_sector"]

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

    total = len(ins_codes)
    asked = [0]

    def on_item(done, _batch, _item):
        done += asked[0]
        if verbose and (done % 50 == 0 or done == total):
            print(f"  {done}/{total}")
        report(done, total, "reading instrument info")

    # The canary first; the rest only once one of it has come back live.
    results = provider.get_instrument_infos(ins_codes[:SECTOR_CANARY],
                                            progress=on_item)
    rest = ins_codes[SECTOR_CANARY:]
    unasked = 0

    if rest and not any(_live(r) for r in results.values()):
        unasked = len(rest)
    elif rest:
        asked[0] = len(results)
        results.update(provider.get_instrument_infos(rest, progress=on_item))

    records = []
    failed = 0

    for ins_code, result in results.items():
        if _live(result):
            records.append(result.data)
        else:
            failed += 1

    if not records:
        if verbose:
            print(f"No instrument info came back live: {failed} failed, "
                  f"{unasked} not asked.")
        return 0

    report(len(results), len(results), f"storing {len(records)} sectors")

    database.save_instrument_sectors(records)
    database.save_fundamentals(records, source=provider.name)

    coverage = database.sector_coverage()

    if verbose:
        print(f"Sector attached to {coverage['with_sector']} of "
              f"{coverage['equities']} equities across "
              f"{coverage['sectors']} sectors; {failed} failed.")

    # A live answer is a fetch even when it carried no sector (the exchange
    # has none for the name yet): 0 would fail the step, and every name
    # would be asked again after each cooldown.
    return coverage["with_sector"] or len(records)


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
    parser.add_argument("--repair-universe", action="store_true",
                        help="reconcile the universe with the stored market "
                             "watch: add the main shares it lists and the "
                             "universe lacks, retire board-moved codes "
                             "(a dry run unless --apply)")
    parser.add_argument("--apply", action="store_true",
                        help="with --repair-universe: write the changes")

    args = parser.parse_args()

    if args.apply and not args.repair_universe:
        parser.error("--apply goes with --repair-universe")

    if not any([args.universe, args.snapshot, args.history, args.indices,
                args.sectors is not None, args.flow is not None, args.status,
                args.repair_universe]):
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

    if args.repair_universe:
        info = database.snapshot_info()
        print(f"Stored market watch: {info['rows']} rows, captured "
              f"{info['captured_at'] or 'never'}.")
        reconcile_universe(apply=args.apply, verbose=True)

    if args.status:
        show_status()

    return 0


if __name__ == "__main__":
    sys.exit(main())
