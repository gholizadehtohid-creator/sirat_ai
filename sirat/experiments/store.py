"""Append-only storage for the experiment record.

WHY A SEPARATE DATABASE
-----------------------
`sirat_market.db` is 424 MB of market data that gets refetched, backfilled and
rebuilt. The research record must outlive any of that, so it lives in its own
file and never shares a transaction with a data refresh.

WHY TRIGGERS AND NOT DISCIPLINE
-------------------------------
Preregistration is only worth something if it cannot be edited after results
exist. Enforcing that in Python protects against mistakes but not against a
researcher with a SQL prompt and a bad afternoon, which is exactly the person
the control exists for. SQLite triggers raise on UPDATE and DELETE of a frozen
preregistration and of any recorded stage result, so the guarantee holds
wherever the write comes from.

Corrections are still possible -- they are just visible. A superseded
experiment is marked superseded and a new one cites it, which is the discipline
`experiment_registry.md` already followed by hand across fifteen experiments.
"""

import json
import os
import sqlite3

import pandas as pd


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

DB_PATH = os.path.join(ROOT, "sirat_research.db")


SCHEMA = """
CREATE TABLE IF NOT EXISTS experiments (
    experiment_id           TEXT PRIMARY KEY,
    hypothesis              TEXT NOT NULL,
    strategy_version        TEXT NOT NULL,
    researcher              TEXT NOT NULL,
    notes                   TEXT,
    expected_failure        TEXT NOT NULL,
    acceptance_criteria     TEXT NOT NULL,
    prereg_hash             TEXT NOT NULL,
    git_commit              TEXT NOT NULL,
    git_dirty               INTEGER,
    code_hash               TEXT NOT NULL,
    config_hash             TEXT NOT NULL,
    dataset_snapshot        TEXT NOT NULL,
    universe_snapshot       TEXT NOT NULL,
    reproducible            INTEGER NOT NULL,
    registered_at           TEXT NOT NULL,
    status                  TEXT NOT NULL,
    superseded_by           TEXT
);

CREATE TABLE IF NOT EXISTS preregistration (
    experiment_id           TEXT PRIMARY KEY,
    parameters              TEXT NOT NULL,
    validation_gates        TEXT NOT NULL,
    expected_metrics        TEXT NOT NULL,
    rejection_thresholds    TEXT NOT NULL,
    seed                    INTEGER NOT NULL,
    frozen_at               TEXT NOT NULL,
    content_hash            TEXT NOT NULL,
    FOREIGN KEY (experiment_id) REFERENCES experiments(experiment_id)
);

CREATE TABLE IF NOT EXISTS stage_results (
    experiment_id           TEXT NOT NULL,
    stage                   INTEGER NOT NULL,
    stage_name              TEXT NOT NULL,
    status                  TEXT NOT NULL,
    detail                  TEXT,
    metrics                 TEXT,
    recorded_at             TEXT NOT NULL,
    PRIMARY KEY (experiment_id, stage)
);

CREATE TABLE IF NOT EXISTS decisions (
    experiment_id           TEXT PRIMARY KEY,
    decision                TEXT NOT NULL,
    rationale               TEXT NOT NULL,
    rejected_at_stage       INTEGER,
    decided_at              TEXT NOT NULL,
    decided_by              TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS universe_snapshots (
    snapshot_id             TEXT PRIMARY KEY,
    taken_at                TEXT NOT NULL,
    instrument_count        INTEGER NOT NULL,
    content_hash            TEXT NOT NULL,
    payload                 TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS windows_touched (
    experiment_id           TEXT NOT NULL,
    window_start            TEXT NOT NULL,
    window_end              TEXT NOT NULL,
    purpose                 TEXT NOT NULL,
    touched_at              TEXT NOT NULL
);

-- A frozen preregistration is the whole point of preregistering.
CREATE TRIGGER IF NOT EXISTS prereg_no_update
BEFORE UPDATE ON preregistration
BEGIN
    SELECT RAISE(ABORT,
        'preregistration is immutable: register a new experiment citing this one');
END;

CREATE TRIGGER IF NOT EXISTS prereg_no_delete
BEFORE DELETE ON preregistration
BEGIN
    SELECT RAISE(ABORT, 'preregistration is immutable: it cannot be deleted');
END;

-- A stage result that can be rewritten is a stage result that will be.
CREATE TRIGGER IF NOT EXISTS stage_no_update
BEFORE UPDATE ON stage_results
BEGIN
    SELECT RAISE(ABORT,
        'stage results are append-only: re-run the experiment under a new id');
END;

CREATE TRIGGER IF NOT EXISTS stage_no_delete
BEFORE DELETE ON stage_results
BEGIN
    SELECT RAISE(ABORT, 'stage results are append-only: they cannot be deleted');
END;

CREATE TRIGGER IF NOT EXISTS decision_no_update
BEFORE UPDATE ON decisions
BEGIN
    SELECT RAISE(ABORT, 'a recorded decision is final; supersede the experiment');
END;
"""


def connect(path=None):
    conn = sqlite3.connect(path or DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def initialise(path=None):
    conn = connect(path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def insert_experiment(record, path=None):
    conn = connect(path)
    try:
        conn.execute(
            "INSERT INTO experiments (experiment_id, hypothesis, "
            "strategy_version, researcher, notes, expected_failure, "
            "acceptance_criteria, prereg_hash, git_commit, git_dirty, "
            "code_hash, config_hash, dataset_snapshot, universe_snapshot, "
            "reproducible, registered_at, status, superseded_by) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (record["experiment_id"], record["hypothesis"],
             record["strategy_version"], record["researcher"],
             record.get("notes"), _json(record["expected_failure"]),
             _json(record["acceptance_criteria"]), record["prereg_hash"],
             record["git_commit"],
             None if record.get("git_dirty") is None
             else int(record["git_dirty"]),
             record["code_hash"], record["config_hash"],
             record["dataset_snapshot"], record["universe_snapshot"],
             int(record["reproducible"]), record["registered_at"],
             record["status"], record.get("superseded_by")))
        conn.commit()
    finally:
        conn.close()


def insert_preregistration(experiment_id, prereg, content_hash, path=None):
    conn = connect(path)
    try:
        conn.execute(
            "INSERT INTO preregistration (experiment_id, parameters, "
            "validation_gates, expected_metrics, rejection_thresholds, seed, "
            "frozen_at, content_hash) VALUES (?,?,?,?,?,?,?,?)",
            (experiment_id, _json(prereg["parameters"]),
             _json(prereg["validation_gates"]),
             _json(prereg["expected_metrics"]),
             _json(prereg["rejection_thresholds"]), int(prereg["seed"]),
             pd.Timestamp.now().isoformat(), content_hash))
        conn.commit()
    finally:
        conn.close()


def get_preregistration(experiment_id, path=None):
    conn = connect(path)
    try:
        row = conn.execute(
            "SELECT * FROM preregistration WHERE experiment_id = ?",
            (experiment_id,)).fetchone()
    finally:
        conn.close()

    if row is None:
        return None

    return {
        "experiment_id": row["experiment_id"],
        "parameters": json.loads(row["parameters"]),
        "validation_gates": json.loads(row["validation_gates"]),
        "expected_metrics": json.loads(row["expected_metrics"]),
        "rejection_thresholds": json.loads(row["rejection_thresholds"]),
        "seed": row["seed"],
        "frozen_at": row["frozen_at"],
        "content_hash": row["content_hash"],
    }


def get_experiment(experiment_id, path=None):
    conn = connect(path)
    try:
        row = conn.execute(
            "SELECT * FROM experiments WHERE experiment_id = ?",
            (experiment_id,)).fetchone()
    finally:
        conn.close()

    return dict(row) if row else None


def insert_stage(experiment_id, stage, stage_name, status, detail, metrics,
                 path=None):
    conn = connect(path)
    try:
        conn.execute(
            "INSERT INTO stage_results (experiment_id, stage, stage_name, "
            "status, detail, metrics, recorded_at) VALUES (?,?,?,?,?,?,?)",
            (experiment_id, int(stage), stage_name, status, detail,
             _json(metrics or {}), pd.Timestamp.now().isoformat()))
        conn.commit()
    finally:
        conn.close()


def get_stages(experiment_id, path=None):
    conn = connect(path)
    try:
        rows = conn.execute(
            "SELECT * FROM stage_results WHERE experiment_id = ? "
            "ORDER BY stage", (experiment_id,)).fetchall()
    finally:
        conn.close()

    return [dict(r) for r in rows]


def insert_decision(experiment_id, decision, rationale, stage, by, path=None):
    conn = connect(path)
    try:
        conn.execute(
            "INSERT INTO decisions (experiment_id, decision, rationale, "
            "rejected_at_stage, decided_at, decided_by) VALUES (?,?,?,?,?,?)",
            (experiment_id, decision, rationale,
             None if stage is None else int(stage),
             pd.Timestamp.now().isoformat(), by))
        conn.execute("UPDATE experiments SET status = ? WHERE experiment_id = ?",
                     (decision, experiment_id))
        conn.commit()
    finally:
        conn.close()


def get_decision(experiment_id, path=None):
    conn = connect(path)
    try:
        row = conn.execute("SELECT * FROM decisions WHERE experiment_id = ?",
                           (experiment_id,)).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def record_window(experiment_id, start, end, purpose, path=None):
    conn = connect(path)
    try:
        conn.execute(
            "INSERT INTO windows_touched (experiment_id, window_start, "
            "window_end, purpose, touched_at) VALUES (?,?,?,?,?)",
            (experiment_id, str(start), str(end), purpose,
             pd.Timestamp.now().isoformat()))
        conn.commit()
    finally:
        conn.close()


def window_touched_by(start, end, purpose, path=None):
    """Which experiments already evaluated this window for this purpose.

    The control that keeps a holdout spendable exactly once.
    """

    conn = connect(path)
    try:
        rows = conn.execute(
            "SELECT experiment_id FROM windows_touched WHERE window_start = ? "
            "AND window_end = ? AND purpose = ?",
            (str(start), str(end), purpose)).fetchall()
    finally:
        conn.close()

    return [r["experiment_id"] for r in rows]


def insert_universe_snapshot(snapshot_id, count, content, payload, path=None,
                             taken_at=None):
    """Record a frozen universe.

    `taken_at` is the moment the snapshot DESCRIBES, not the moment the row was
    written. They are normally the same and differ exactly when it matters: a
    snapshot reconstructed for a past date must age from that date, or every
    backfilled snapshot would read as fresh and the schedule would think it was
    up to date when it was not.
    """

    stamp = (pd.Timestamp(taken_at) if taken_at is not None
             else pd.Timestamp.now())

    conn = connect(path)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO universe_snapshots (snapshot_id, taken_at, "
            "instrument_count, content_hash, payload) VALUES (?,?,?,?,?)",
            (snapshot_id, stamp.isoformat(), int(count),
             content, _json(payload)))
        conn.commit()
    finally:
        conn.close()


def latest_universe_snapshot(path=None):
    conn = connect(path)
    try:
        row = conn.execute(
            "SELECT snapshot_id, taken_at, instrument_count, content_hash "
            "FROM universe_snapshots ORDER BY taken_at DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def all_decisions(path=None):
    conn = connect(path)
    try:
        rows = conn.execute("SELECT * FROM decisions").fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]
