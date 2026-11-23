"""Nightly backup of the stores this project cannot get back.

    python backup.py              back up now; keep the newest 7 nights
    python backup.py --list       what is there
    python backup.py --verify     re-check the newest night (reads only)
    python backup.py --verify 2026-11-23
    python backup.py --keep N --dest DIR --mirror DIR

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
the forward log and the small data/*.json state files, copied as they are,
and a `git bundle` of the repository (every branch and tag, one file) when
git can be found: the code had no copy anywhere but .git itself.

Each night is a folder named by its local date under DEST, built under a
.tmp name and renamed only when every copy has passed: a folder without a
manifest is not a backup. Only after a good night are older nights deleted,
and only folders this script made (a dated name AND a manifest) -- a
failed copy never costs the good ones behind it.

THE RAW CODAL ARCHIVE (data/codal_raw) is mirrored, not copied per night:
<DEST>/codal_raw gains each document it does not hold yet. It was left out
as "re-downloadable", and it is not, in practice: 24,586 documents took
three and a half months of paced fetching through a firewall that blocks
for hours, and 47 of them answer HTTP 500 today (the review, 2026-11-23).
It is written once and never changed, so the mirror only ever adds --
nothing in it is deleted or pruned, and a night's first run copies the
whole 0.5 GB while every later one copies the day's few dozen files.

A SECOND DESTINATION: with the environment variable SIRAT_BACKUP_MIRROR
(or --mirror) set to a folder, the finished night and the archive mirror
are copied there too and the same number of nights kept. The night stands
whether or not that copy works; a mirror that is set and cannot be written
makes the exit code 1, so the evening log says so.

DEST is beside the project, not inside it: deleting or re-cloning the
project folder must not take its backups along. IT IS THE SAME DISK -- this
machine has one drive, C: -- so everything above guards against mistakes
and corruption, not against a dead or stolen laptop. A real off-disk copy
is the owner's step: point SIRAT_BACKUP_MIRROR at a USB disk or a network
share, or copy the SIRAT-AI-backups folder elsewhere now and then.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
DEST = os.path.join(os.path.dirname(ROOT), "SIRAT-AI-backups")
KEEP = 7

DATABASES = ("sirat_market.db", "sirat_fundamentals.db", "sirat_research.db")
# fx_rates.json and outlook_calibration.json since 2026-11-23: the dollar
# series is fetched from a site that serves only its recent days, and the
# calibration is what the outlook's value range was measured from.
FILES = ("backtest_output/forward_predictions.jsonl",
         "data/bill_quotes.json", "data/bill_yield.json",
         "data/market_valuation.json", "data/dps_backfill_last.json",
         "data/fx_rates.json", "data/outlook_calibration.json")

ARCHIVE = "data/codal_raw"          # mirrored into <dest>/ARCHIVE_DEST
ARCHIVE_DEST = "codal_raw"
ARCHIVE_NOTE = "MIRROR.json"        # when it was last mirrored, and how much
BUNDLE = "repository.bundle"
MIRROR_ENV = "SIRAT_BACKUP_MIRROR"

MANIFEST = "MANIFEST.json"
NIGHT = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# Room for the copy and the one it replaces, beside what the disk must keep.
SPACE_FACTOR = 2
# What a first mirror of the archive must leave free on the destination.
ARCHIVE_RESERVE = 1 << 30

# Where Git for Windows puts git.exe. The scheduled tasks' shells do not
# always have it on PATH (the git memory, 2026-08-19: a shell started
# before the installer ran never sees the new entry).
GIT_PLACES = (r"C:\Program Files\Git\cmd\git.exe",
              r"C:\Program Files\Git\bin\git.exe",
              r"C:\Program Files (x86)\Git\cmd\git.exe")


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


# ==============================
# THE REPOSITORY
# ==============================

def find_git():
    """git's path, or None. PATH first, then where the installer puts it."""

    found = shutil.which("git")
    if found:
        return found
    places = list(GIT_PLACES)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        places.append(os.path.join(local, "Programs", "Git", "cmd", "git.exe"))
    for place in places:
        if os.path.isfile(place):
            return place
    return None


def bundle_repository(root, dst):
    """`git bundle create dst --all` for the repository at `root`.

    Returns (manifest entry, None), or (None, why) when there is nothing to
    bundle or git is not to be found -- a night without a bundle is still a
    night. Reads the repository only; what is not committed is not in it.
    """

    if not os.path.isdir(os.path.join(root, ".git")):
        return None, "no repository"
    git = find_git()
    if git is None:
        return None, "git not found"
    try:
        done = subprocess.run([git, "-C", root, "bundle", "create", dst, "--all"],
                              capture_output=True, text=True, timeout=600,
                              stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, "git failed: %s" % type(exc).__name__
    if done.returncode != 0 or not os.path.isfile(dst):
        if os.path.exists(dst):
            os.unlink(dst)
        return None, "git bundle failed: %s" % (
            (done.stderr or "").strip().splitlines() or ["?"])[-1][:120]
    return {"name": BUNDLE, "bytes": os.path.getsize(dst),
            "sha256": _sha256(dst)}, None


# ==============================
# THE RAW ARCHIVE
# ==============================

def _sizes(folder):
    """{name: size} of the files directly in `folder`; {} if it is not there."""

    try:
        with os.scandir(folder) as entries:
            return {e.name: e.stat().st_size for e in entries if e.is_file()}
    except OSError:
        return {}


def mirror_tree(src, dst):
    """Copy into `dst` each file of `src` it does not hold at the same size.

    Additive: nothing in `dst` is deleted, and a file already there at the
    source's size is left alone -- the archive is written once per document
    and never changed. Half-written files (.tmp, .part) are not copied. A
    file that cannot be copied is counted, never raised: the rest still go.

    Returns {'files', 'bytes'} (the source's), {'copied', 'copied_bytes',
    'failed'} and 'error' when nothing could be copied at all.
    """

    stats = {"files": 0, "bytes": 0, "copied": 0, "copied_bytes": 0,
             "failed": 0, "error": None}
    todo = []
    for folder, _dirs, _names in os.walk(src):
        rel = os.path.relpath(folder, src)
        target = dst if rel == "." else os.path.join(dst, rel)
        have = _sizes(target)
        for name, size in _sizes(folder).items():
            if name.endswith((".tmp", ".part")):
                continue
            stats["files"] += 1
            stats["bytes"] += size
            if have.get(name) != size:
                todo.append((os.path.join(folder, name), target, name, size))
    if not todo:
        return stats

    need = sum(size for _s, _t, _n, size in todo)
    try:
        os.makedirs(dst, exist_ok=True)
        free = shutil.disk_usage(dst).free
    except OSError as exc:
        stats["failed"], stats["error"] = len(todo), "%s: %s" % (
            type(exc).__name__, exc)
        return stats
    if free < need + ARCHIVE_RESERVE:
        stats["failed"] = len(todo)
        stats["error"] = "%.1f GB free at %s, %.1f GB needed" % (
            free / 1e9, dst, (need + ARCHIVE_RESERVE) / 1e9)
        return stats

    made = set()
    for source, target, name, size in todo:
        try:
            if target not in made:
                os.makedirs(target, exist_ok=True)
                made.add(target)
            part = os.path.join(target, name + ".part")
            shutil.copyfile(source, part)
            os.replace(part, os.path.join(target, name))
            stats["copied"] += 1
            stats["copied_bytes"] += size
        except OSError:
            stats["failed"] += 1
    return stats


def mirror_archive(root, dest):
    """Bring <dest>/codal_raw up to date with the project's raw archive.

    None when the project has no archive. The outcome is also written to
    <dest>/codal_raw/MIRROR.json, for --verify and the health card.
    """

    src = os.path.join(root, *ARCHIVE.split("/"))
    if not os.path.isdir(src):
        return None
    target = os.path.join(dest, ARCHIVE_DEST)
    stats = mirror_tree(src, target)
    stats["mirrored_at"] = datetime.now().isoformat(timespec="seconds")
    try:
        with open(os.path.join(target, ARCHIVE_NOTE), "w",
                  encoding="utf-8") as handle:
            json.dump(stats, handle, indent=1)
    except OSError:
        pass
    return stats


# ==============================
# NIGHTS
# ==============================

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


def _same_place(one, other):
    return (os.path.normcase(os.path.abspath(one))
            == os.path.normcase(os.path.abspath(other)))


def copy_to_second(final, dest, mirror, keep=KEEP, archive=True):
    """The finished night, and the archive mirror, to a second destination.

    The night is copied under a .tmp name, re-hashed against its manifest
    and only then renamed; older nights there are pruned like the first
    destination's. Raises BackupFailed or OSError; the caller keeps the
    night it already has either way.
    """

    if _same_place(mirror, dest):
        raise BackupFailed("the second destination is the first one")
    night = os.path.basename(final)
    os.makedirs(mirror, exist_ok=True)
    work = os.path.join(mirror, night + ".tmp")
    if os.path.exists(work):
        shutil.rmtree(work)
    shutil.copytree(final, work)
    bad = [line for ok, line in _check_night(work, databases=False) if not ok]
    if bad:
        shutil.rmtree(work, ignore_errors=True)
        raise BackupFailed("the copy at %s does not match its manifest: %s"
                           % (mirror, bad[0]))
    target = os.path.join(mirror, night)
    if os.path.exists(target):
        shutil.rmtree(target)
    os.replace(work, target)
    out = {"folder": target, "removed": prune(mirror, keep), "archive": None}
    source = os.path.join(dest, ARCHIVE_DEST)
    if archive and os.path.isdir(source):
        out["archive"] = mirror_tree(source, os.path.join(mirror, ARCHIVE_DEST))
        if out["archive"]["failed"]:
            raise BackupFailed("the archive at %s is short of %d documents%s"
                               % (mirror, out["archive"]["failed"],
                                  ": " + out["archive"]["error"]
                                  if out["archive"]["error"] else ""))
    return out


def run(root=None, dest=None, keep=KEEP, now=None, mirror=None, archive=True,
        bundle=True):
    """Back up now. Returns a summary; raises BackupFailed on a bad copy.

    What can go wrong AFTER the night stands -- the archive mirror, the
    second destination -- is listed under 'problems' instead of raised.
    """

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
    if bundle:
        entry, why = bundle_repository(root, os.path.join(work, BUNDLE))
        if entry:
            entries.append(entry)
        else:
            skipped.append("%s (%s)" % (BUNDLE, why))

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
    summary = dict(manifest, folder=final, removed=removed,
                   bytes=sum(e["bytes"] for e in entries),
                   archive=None, mirror=None, problems=[])

    # ---- the night stands; what follows cannot cost it ---------------------
    if archive:
        try:
            summary["archive"] = mirror_archive(root, dest)
        except OSError as exc:
            summary["problems"].append("codal_raw mirror: %s: %s"
                                       % (type(exc).__name__, exc))
        stats = summary["archive"]
        if stats and stats["failed"]:
            summary["problems"].append(
                "codal_raw mirror: %d documents not copied%s"
                % (stats["failed"], ": " + stats["error"] if stats["error"] else ""))
    if mirror:
        try:
            summary["mirror"] = copy_to_second(final, dest, mirror, keep,
                                               archive=archive)
        except (BackupFailed, OSError, sqlite3.Error) as exc:
            summary["problems"].append("second destination %s: %s"
                                       % (mirror, exc))
    summary["seconds"] = round(time.monotonic() - started, 1)
    return summary


# ==============================
# VERIFY
# ==============================

def _check_night(folder, databases=True):
    """[(ok, line)] for every file a night's manifest names.

    Each file must be there, at the manifest's size and sha256; each
    database copy must also open read-only and pass quick_check.
    """

    try:
        with open(os.path.join(folder, MANIFEST), encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (OSError, ValueError) as exc:
        return [(False, "%s: unreadable (%s)" % (MANIFEST, type(exc).__name__))]

    out = []
    for entry in manifest.get("files") or []:
        name = entry.get("name") or "?"
        path = os.path.join(folder, *name.split("/"))
        if not os.path.isfile(path):
            out.append((False, "%s: MISSING" % name))
            continue
        if os.path.getsize(path) != entry.get("bytes"):
            out.append((False, "%s: %d bytes, the manifest says %s"
                        % (name, os.path.getsize(path), entry.get("bytes"))))
            continue
        if _sha256(path) != entry.get("sha256"):
            out.append((False, "%s: sha256 differs from the manifest" % name))
            continue
        line = "%s: %d bytes, sha256 ok" % (name, entry["bytes"])
        if databases and name.endswith(".db"):
            # immutable: not even a -shm file is made beside the copy.
            uri = "file:%s?mode=ro&immutable=1" % path.replace("\\", "/")
            try:
                conn = sqlite3.connect(uri, uri=True)
                try:
                    check = _quick_check(conn)
                finally:
                    conn.close()
            except sqlite3.Error as exc:
                check = "%s: %s" % (type(exc).__name__, exc)
            if check != "ok":
                out.append((False, "%s: quick_check says %r" % (name, check)))
                continue
            line += ", opens read-only, quick_check ok"
        out.append((True, line))
    if not out:
        out.append((False, "%s names no files" % MANIFEST))
    return out


def verify(dest=None, night=None, root=None):
    """Re-check one night (the newest by default) and the archive mirror.

    Returns (ok, lines). Reads only: nothing under `dest` is written.
    """

    dest = dest or DEST
    done = nights(dest)
    if night and night not in done:
        return False, ["%s: no such night under %s" % (night, dest)]
    if not done:
        return False, ["no backup under %s" % dest]
    night = night or done[-1]
    results = _check_night(os.path.join(dest, night))
    lines = ["night %s" % night] + [
        "  %s  %s" % ("ok  " if ok else "BAD ", line) for ok, line in results]
    good = all(ok for ok, _line in results)

    # The archive: every source document must be in the mirror at its size.
    src = os.path.join(root or ROOT, *ARCHIVE.split("/"))
    target = os.path.join(dest, ARCHIVE_DEST)
    if os.path.isdir(src):
        total = missing = 0
        for folder, _dirs, _names in os.walk(src):
            rel = os.path.relpath(folder, src)
            have = _sizes(target if rel == "." else os.path.join(target, rel))
            for name, size in _sizes(folder).items():
                if name.endswith((".tmp", ".part")):
                    continue
                total += 1
                if have.get(name) != size:
                    missing += 1
        if not os.path.isdir(target):
            lines.append("  --    codal_raw: not mirrored yet (%d documents "
                         "in the project)" % total)
        else:
            lines.append("  %s  codal_raw: %d of %d documents mirrored"
                         % ("ok  " if not missing else "--  ",
                            total - missing, total)
                         + ("" if not missing else
                            " (the rest are copied by the next backup)"))
    return good, lines


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        epilog="The default destination is on the SAME DISK as the project "
               "(this machine has one drive, C:): it guards against mistakes "
               "and corruption, not a dead or stolen laptop. A real off-disk "
               "copy is the owner's step -- set %s (or --mirror) to a folder "
               "on a USB disk or a network share, or copy the backups folder "
               "elsewhere now and then." % MIRROR_ENV)
    parser.add_argument("--dest", default=DEST)
    parser.add_argument("--keep", type=int, default=KEEP)
    parser.add_argument("--mirror", default=None,
                        help="a second destination for the finished night "
                             "and the archive (default: $%s)" % MIRROR_ENV)
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--verify", nargs="?", const="", default=None,
                        metavar="NIGHT",
                        help="re-hash a night (the newest by default) against "
                             "its manifest and quick_check each database "
                             "copy; reads only")
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

    if args.verify is not None:
        good, lines = verify(args.dest, args.verify or None)
        for line in lines:
            print(line)
        print("verify: %s" % ("ok" if good else "FAILED"), flush=True)
        return 0 if good else 1

    mirror = args.mirror or os.environ.get(MIRROR_ENV) or None
    try:
        summary = run(dest=args.dest, keep=max(1, args.keep), mirror=mirror)
    except (BackupFailed, OSError, sqlite3.Error) as exc:
        print("backup: FAILED -- %s; older nights left as they were" % exc,
              flush=True)
        return 1
    archive = summary.get("archive")
    print("backup: %s  %d files, %.2f GB, %.0fs%s%s%s%s"
          % (summary["folder"], len(summary["files"]), summary["bytes"] / 1e9,
             summary["seconds"],
             "; removed %s" % ", ".join(summary["removed"])
             if summary["removed"] else "",
             "; not present: %s" % ", ".join(summary["skipped"])
             if summary["skipped"] else "",
             "; codal_raw: %d new of %d documents"
             % (archive["copied"], archive["files"]) if archive else "",
             "; second copy at %s" % summary["mirror"]["folder"]
             if summary.get("mirror") else ""), flush=True)
    for problem in summary["problems"]:
        print("backup: PROBLEM -- %s (the night itself is good)" % problem,
              flush=True)
    return 1 if summary["problems"] else 0


if __name__ == "__main__":
    sys.exit(main())
