"""Nightly backup of the stores this project cannot get back.

    python backup.py              back up now; keep the newest 7 nights
    python backup.py --list       what is there
    python backup.py --keep N --dest DIR

WHY (2026-11-23, approved by the user)
--------------------------------------
There was no backup and no git remote. sirat_market.db holds what no feed
will ever serve again -- the session watch's order books and intraday flow,
the Codal campaign's queue and state -- and the forward prediction log is
evidence whose whole value is that it was written before the outcome. One
bad repair, one test that wrote to a live store (it happened, 2026-11-11),
one disk error, and all of it is gone.

WHAT
----
The three databases, copied with SQLite's online backup API: one read
transaction, so the copy is consistent while the Codal campaign and the
watch go on writing (all three stores are WAL or rollback, and a WAL
reader blocks no writer). Each copy is switched to a single-file journal,
must pass PRAGMA quick_check, and gets its sha256 in MANIFEST.json. Then
the forward log and the small data/*.json state files, copied as they are.
The raw Codal archive (~0.5 GB, re-downloadable) is left out.

Each night is a folder named by its local date under DEST, built under a
.tmp name and renamed only when every copy has passed: a folder without a
manifest is not a backup. Only after a good night are older nights deleted,
and only folders this script made (a dated name AND a manifest) -- a
failed copy never costs the good ones behind it.

DEST is beside the project, not inside it: deleting or re-cloning the
project folder must not take its backups along. It is the same disk, so
this guards against mistakes and corruption, not a dead drive -- for that
the folder needs copying elsewhere now and then.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
DEST = os.path.join(os.path.dirname(ROOT), "SIRAT-AI-backups")
KEEP = 7

DATABASES = ("sirat_market.db", "sirat_fundamentals.db", "sirat_research.db")
FILES = ("backtest_output/forward_predictions.jsonl",
         "data/bill_quotes.json", "data/bill_yield.json",
         "data/market_valuation.json", "data/dps_backfill_last.json")

MANIFEST = "MANIFEST.json"
NIGHT = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# Room for the copy and the one it replaces, beside what the disk must keep.
SPACE_FACTOR = 2


class BackupFailed(Exception):
    """A copy could not be made or did not verify."""


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(4 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _quick_check(conn):
    return conn.execute("PRAGMA quick_check").fetchone()[0]


def _copy_database(src, dst):
    """Online copy of one store; returns its manifest entry."""

    tmp = dst + ".partial"
    if os.path.exists(tmp):
        os.unlink(tmp)
    source = sqlite3.connect("file:%s?mode=ro" % src.replace("\\", "/"),
                             uri=True, timeout=60)
    target = sqlite3.connect(tmp)
    try:
        source.backup(target)              # all pages, one read transaction
        # The copy inherits the source's WAL header; a backup is one file.
        target.execute("PRAGMA journal_mode=DELETE").fetchone()
        check = _quick_check(target)
    finally:
        target.close()
        source.close()
    if check != "ok":
        raise BackupFailed("%s: quick_check on the copy says %r -- the live "
                           "store may be damaged" % (os.path.basename(src), check))
    os.replace(tmp, dst)
    return {"name": os.path.basename(dst), "bytes": os.path.getsize(dst),
            "sha256": _sha256(dst), "quick_check": check}


def nights(dest=None):
    """The finished backups under `dest`, oldest first."""

    dest = dest or DEST
    if not os.path.isdir(dest):
        return []
    return sorted(name for name in os.listdir(dest)
                  if NIGHT.match(name)
                  and os.path.isfile(os.path.join(dest, name, MANIFEST)))


def prune(dest=None, keep=KEEP):
    """Delete all but the newest `keep` finished nights, and any .tmp folder
    a killed run left. Nothing else under `dest` is touched."""

    dest = dest or DEST
    removed = []
    done = nights(dest)
    for name in done[:max(0, len(done) - keep)]:
        shutil.rmtree(os.path.join(dest, name))
        removed.append(name)
    for name in os.listdir(dest) if os.path.isdir(dest) else []:
        if name.endswith(".tmp") and NIGHT.match(name[:-4]):
            shutil.rmtree(os.path.join(dest, name), ignore_errors=True)
            removed.append(name)
    return removed


def run(root=None, dest=None, keep=KEEP, now=None):
    """Back up now. Returns a summary; raises BackupFailed on a bad copy."""

    root = root or ROOT
    dest = dest or DEST
    started = time.monotonic()
    night = (now or datetime.now()).strftime("%Y-%m-%d")

    sources = [os.path.join(root, name) for name in DATABASES
               if os.path.isfile(os.path.join(root, name))]
    if not sources:
        raise BackupFailed("no database under %s" % root)
    needed = SPACE_FACTOR * sum(os.path.getsize(p) for p in sources)
    os.makedirs(dest, exist_ok=True)
    free = shutil.disk_usage(dest).free
    if free < needed:
        raise BackupFailed("%.1f GB free at %s, %.1f GB needed"
                           % (free / 1e9, dest, needed / 1e9))

    work = os.path.join(dest, night + ".tmp")
    if os.path.exists(work):
        shutil.rmtree(work)
    os.makedirs(work)

    entries, skipped = [], []
    for src in sources:
        entries.append(_copy_database(
            src, os.path.join(work, os.path.basename(src))))
    for name in FILES:
        src = os.path.join(root, *name.split("/"))
        if not os.path.isfile(src):
            skipped.append(name)
            continue
        dst = os.path.join(work, *name.split("/"))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        entries.append({"name": name, "bytes": os.path.getsize(dst),
                        "sha256": _sha256(dst)})

    manifest = {"night": night,
                "taken_at": datetime.now().isoformat(timespec="seconds"),
                "source": root, "files": entries, "skipped": skipped,
                "seconds": round(time.monotonic() - started, 1)}
    with open(os.path.join(work, MANIFEST), "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=1, ensure_ascii=False)

    final = os.path.join(dest, night)
    if os.path.exists(final):              # a second run the same day
        shutil.rmtree(final)
    os.replace(work, final)
    removed = prune(dest, keep)
    return dict(manifest, folder=final, removed=removed,
                bytes=sum(e["bytes"] for e in entries))


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dest", default=DEST)
    parser.add_argument("--keep", type=int, default=KEEP)
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args(argv)

    if args.list:
        for name in nights(args.dest):
            with open(os.path.join(args.dest, name, MANIFEST),
                      encoding="utf-8") as handle:
                m = json.load(handle)
            print("  %s  %d files  %.2f GB  %ss"
                  % (name, len(m["files"]),
                     sum(f["bytes"] for f in m["files"]) / 1e9, m["seconds"]))
        return 0

    try:
        summary = run(dest=args.dest, keep=max(1, args.keep))
    except (BackupFailed, OSError, sqlite3.Error) as exc:
        print("backup: FAILED -- %s; older nights left as they were" % exc,
              flush=True)
        return 1
    print("backup: %s  %d files, %.2f GB, %.0fs%s%s"
          % (summary["folder"], len(summary["files"]), summary["bytes"] / 1e9,
             summary["seconds"],
             "; removed %s" % ", ".join(summary["removed"])
             if summary["removed"] else "",
             "; not present: %s" % ", ".join(summary["skipped"])
             if summary["skipped"] else ""), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
