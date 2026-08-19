"""The research ledger: the permanent record of what was decided and why.

WHY A LEDGER WHEN THERE IS ALREADY AN APPEND-ONLY DATABASE
------------------------------------------------------------
`store.py`'s triggers stop a row being edited or deleted. They cannot stop a
table being dropped and rebuilt, a database being restored from an older
backup, or a row being quietly left out when someone regenerates the file --
and none of those leave a trace, because no UPDATE ever runs.

So the ledger chains. Each entry hashes the entry before it:

    entry_hash = sha256(prev_hash + canonical(entry))

Editing an entry changes its hash and breaks every link after it. Deleting one
breaks the link across the gap. Reordering breaks both. `verify()` walks the
chain and names the first entry where the history stops adding up, which is a
guarantee triggers cannot give.

WHAT AN ENTRY CONTAINS
----------------------
Twelve fields, and an experiment cannot be closed without all of them:

    experiment_id · date · hypothesis · author · code_version · git_commit
    config_hash · dataset_fingerprint · universe_snapshot · preregistration_hash
    validation_evidence · final_verdict

`validation_evidence` is the full stage record -- every check, its verdict, and
the numbers behind it -- rather than a summary. A ledger that records "REJECTED
at stage 2" and not why is a ledger nobody can audit.

APPENDING IS NOT OPTIONAL
-------------------------
`Manager.decide()` appends here automatically. Leaving it to the researcher
would reproduce exactly the failure this platform was built to end: fifteen
experiments whose record was a markdown table somebody remembered to update.
"""

import hashlib
import json
import os

import pandas as pd

from sirat.experiments import store


GENESIS = "0" * 64


class LedgerError(Exception):
    """The chain does not add up, or an entry cannot be written."""


def _canonical(payload):
    """One byte-for-byte representation of an entry, whatever built it."""

    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=str)


def _hash(prev_hash, payload):
    digest = hashlib.sha256()
    digest.update(prev_hash.encode("utf-8"))
    digest.update(_canonical(payload).encode("utf-8"))
    return digest.hexdigest()


def build_entry(record):
    """The twelve fields, from an experiment's full record.

    Raises rather than writing a partial entry. A ledger row missing its
    provenance is the thing the ledger exists to make impossible.
    """

    decision = record.get("decision")

    if not decision:
        raise LedgerError(
            "experiment %s has not reached a decision; there is nothing to "
            "enter in the ledger" % record["experiment_id"])

    provenance = record["provenance"]

    entry = {
        "experiment_id": record["experiment_id"],
        "date": decision["decided_at"],
        "hypothesis": record["hypothesis"],
        "author": record["researcher"],
        "code_version": {
            "strategy_version": record["strategy_version"],
            "code_hash": provenance["code_hash"],
        },
        "git_commit": provenance["git_commit"],
        "git_dirty": provenance["git_dirty"],
        "config_hash": provenance["config_hash"],
        "dataset_fingerprint": provenance["dataset_snapshot"],
        "universe_snapshot": provenance["universe_snapshot"],
        "preregistration_hash": provenance["prereg_hash"],
        "reproducible": provenance["reproducible"],
        "registered_at": record["registered_at"],
        "expected_failure": record["expected_failure"],
        "acceptance_criteria": record["acceptance_criteria"],
        "validation_evidence": [
            {
                "stage": s["stage"],
                "stage_name": s["stage_name"],
                "status": s["status"],
                "checks": s["metrics"].get("checks", {}),
                "detail": s["metrics"].get("detail", {}),
                "recorded_at": s["recorded_at"],
            }
            for s in record["stages"]
        ],
        "final_verdict": {
            "decision": decision["decision"],
            "rationale": decision["rationale"],
            "rejected_at_stage": decision["rejected_at_stage"],
            "decided_by": decision["decided_by"],
        },
    }

    missing = [field for field in REQUIRED_FIELDS
               if entry.get(field) in (None, "", [], {})]

    # `git_dirty` may legitimately be None (git absent) and
    # `rejected_at_stage` may legitimately be None (nothing failed), so neither
    # is in REQUIRED_FIELDS. Everything else must carry something.
    if missing:
        raise LedgerError(
            "cannot enter %s in the ledger: missing %s"
            % (record["experiment_id"], ", ".join(missing)))

    return entry


REQUIRED_FIELDS = (
    "experiment_id", "date", "hypothesis", "author", "code_version",
    "git_commit", "config_hash", "dataset_fingerprint", "universe_snapshot",
    "preregistration_hash", "validation_evidence", "final_verdict",
)


def head(path=None):
    """The hash of the most recent entry, or the genesis value."""

    store.initialise(path)

    conn = store.connect(path)

    try:
        row = conn.execute(
            "SELECT entry_hash FROM ledger ORDER BY seq DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()

    return row["entry_hash"] if row else GENESIS


def append(record, path=None):
    """Add one closed experiment to the permanent record."""

    entry = build_entry(record)

    store.initialise(path)

    conn = store.connect(path)

    try:
        previous = conn.execute(
            "SELECT entry_hash FROM ledger ORDER BY seq DESC LIMIT 1"
        ).fetchone()

        prev_hash = previous["entry_hash"] if previous else GENESIS

        entry_hash = _hash(prev_hash, entry)

        conn.execute(
            "INSERT INTO ledger (experiment_id, appended_at, prev_hash, "
            "entry_hash, payload) VALUES (?,?,?,?,?)",
            (entry["experiment_id"], pd.Timestamp.now().isoformat(),
             prev_hash, entry_hash, _canonical(entry)))

        conn.commit()
    finally:
        conn.close()

    return {"experiment_id": entry["experiment_id"], "entry_hash": entry_hash,
            "prev_hash": prev_hash}


def entries(path=None):
    """Every ledger entry, oldest first."""

    store.initialise(path)

    conn = store.connect(path)

    try:
        rows = conn.execute(
            "SELECT seq, experiment_id, appended_at, prev_hash, entry_hash, "
            "payload FROM ledger ORDER BY seq").fetchall()
    finally:
        conn.close()

    return [
        {"seq": r["seq"], "experiment_id": r["experiment_id"],
         "appended_at": r["appended_at"], "prev_hash": r["prev_hash"],
         "entry_hash": r["entry_hash"], "entry": json.loads(r["payload"])}
        for r in rows
    ]


def verify(path=None):
    """Walk the chain. Report the first place the history stops adding up."""

    rows = entries(path)

    if not rows:
        return {"entries": 0, "intact": True, "head": GENESIS,
                "note": "the ledger is empty"}

    expected_prev = GENESIS

    for row in rows:

        if row["prev_hash"] != expected_prev:
            return {
                "entries": len(rows), "intact": False,
                "broken_at": row["seq"],
                "experiment_id": row["experiment_id"],
                "reason": ("entry %d expects to follow %s but the chain is at "
                           "%s -- an entry has been removed or reordered"
                           % (row["seq"], row["prev_hash"][:16],
                              expected_prev[:16])),
            }

        recomputed = _hash(row["prev_hash"], row["entry"])

        if recomputed != row["entry_hash"]:
            return {
                "entries": len(rows), "intact": False,
                "broken_at": row["seq"],
                "experiment_id": row["experiment_id"],
                "reason": ("entry %d (%s) no longer hashes to its recorded "
                           "value: %s != %s -- its content has been altered"
                           % (row["seq"], row["experiment_id"],
                              recomputed[:16], row["entry_hash"][:16])),
            }

        expected_prev = row["entry_hash"]

    return {"entries": len(rows), "intact": True, "head": expected_prev,
            "note": "every entry hashes to its recorded value and every link "
                    "matches"}


# ==============================
# EXPORT
# ==============================

def export_json(path=None, out=None):
    out = out or os.path.join(os.path.dirname(store.DB_PATH),
                              "backtest_output", "research_ledger.json")

    os.makedirs(os.path.dirname(out), exist_ok=True)

    payload = {
        "ledger": entries(path),
        "chain": verify(path),
        "exported_at": pd.Timestamp.now().isoformat(),
    }

    with open(out, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, default=str)

    return out


def export_markdown(path=None, out=None):
    """The human-readable ledger.

    Generated, never hand-edited -- which is the whole difference between this
    and `experiment_registry.md`, the markdown table that governed Phase 1.
    """

    out = out or os.path.join(os.path.dirname(store.DB_PATH),
                              "RESEARCH_LEDGER.md")

    rows = entries(path)

    chain = verify(path)

    lines = [
        "# SIRAT-AI — RESEARCH LEDGER",
        "",
        "**Append-only. Generated from `sirat_research.db`; never edited by "
        "hand.**",
        "",
        "Each entry hashes the one before it. Editing, deleting or reordering "
        "any entry",
        "breaks every link after it and `ledger.verify()` names the first "
        "entry that fails.",
        "",
        "| | |",
        "|---|---|",
        "| entries | %d |" % chain["entries"],
        "| chain intact | **%s** |" % chain["intact"],
        "| head | `%s` |" % chain.get("head", "-"),
        "| exported | %s |" % pd.Timestamp.now().isoformat()[:19],
        "",
        "---",
        "",
    ]

    if not rows:
        lines.append("*No experiment has been closed yet.*")
    else:
        lines += [
            "## Summary",
            "",
            "| # | Experiment | Verdict | Stage | Reproducible | Author |",
            "|---|---|---|---|---|---|",
        ]

        for row in rows:
            entry = row["entry"]
            verdict = entry["final_verdict"]
            lines.append(
                "| %d | `%s` | **%s** | %s | %s | %s |"
                % (row["seq"], entry["experiment_id"], verdict["decision"],
                   verdict["rejected_at_stage"] or "—",
                   "yes" if entry["reproducible"] else "no", entry["author"]))

        lines += ["", "---", ""]

        for row in rows:
            lines += _entry_markdown(row)

    with open(out, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")

    return out


def _entry_markdown(row):
    entry = row["entry"]

    verdict = entry["final_verdict"]

    lines = [
        "## %d. `%s`" % (row["seq"], entry["experiment_id"]),
        "",
        "> %s" % entry["hypothesis"],
        "",
        "| Field | Value |",
        "|---|---|",
        "| date | %s |" % str(entry["date"])[:19],
        "| author | %s |" % entry["author"],
        "| strategy version | %s |" % entry["code_version"]["strategy_version"],
        "| code hash | `%s` |" % entry["code_version"]["code_hash"][:24],
        "| git commit | `%s` |" % str(entry["git_commit"])[:24],
        "| config hash | `%s` |" % entry["config_hash"][:24],
        "| dataset fingerprint | `%s` |" % entry["dataset_fingerprint"],
        "| universe snapshot | `%s` |" % entry["universe_snapshot"],
        "| preregistration hash | `%s` |" % entry["preregistration_hash"][:24],
        "| reproducible | %s |" % entry["reproducible"],
        "| **verdict** | **%s** |" % verdict["decision"],
        "| entry hash | `%s` |" % row["entry_hash"][:24],
        "",
        "**Rationale.** %s" % verdict["rationale"],
        "",
    ]

    if entry["validation_evidence"]:
        lines += ["| Stage | Status | Checks |", "|---|---|---|"]

        for stage in entry["validation_evidence"]:
            checks = stage["checks"] or {}
            failed = [k for k, v in checks.items() if v is False]
            lines.append(
                "| %d %s | **%s** | %s |"
                % (stage["stage"], stage["stage_name"], stage["status"],
                   ("failed: " + ", ".join(sorted(failed))) if failed
                   else "all %d passed" % len(checks)))

        lines.append("")

    lines += ["---", ""]

    return lines
