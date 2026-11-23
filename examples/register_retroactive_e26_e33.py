"""Bring E26-E33 into the Experiment Manager's record -- RETROACTIVELY.

WHY THIS EXISTS (2026-11-23)
----------------------------
E26, E27, E28, E30, E31, E32 and E33 were preregistered as JSON files and run
as standalone scripts. None of them was registered with the Experiment Manager,
so the research ledger stopped at E25 while the only two "accepted" results in
the repository (E27, E28) and five rejections sat outside it -- and CI stayed
green, because it checked the chain it had, not what was missing from it. The
2026-11-23 review found that; this script is the repair.

WHAT THIS IS, EXACTLY
---------------------
Not a re-run and not an assessment. It writes down, through the Manager's own
public methods and nothing else, what the files already in the repository say:

  * the hypothesis, the decision rule and the declared limits, verbatim from
    backtest_output/E??_preregistration.json (the whole file is embedded in
    the frozen record);
  * the verdict, verbatim from backtest_output/E??_results.json;
  * the commits and author dates of both files, from git;
  * the data window the experiment evaluated, from the result file -- and the
    part of it that lies inside the reservation R-FORWARD-2027
    (2026-08-19..2027-08-19), which every one of the seven read without
    claiming it.

Every entry says, in its researcher, notes, parameters and rationale, that it
was registered after the fact on 2026-11-23.

FOUR THINGS THE MANAGER CANNOT SAY, AND WHAT IS RECORDED INSTEAD
----------------------------------------------------------------
1. It has no "retroactive" flag. The label is carried in the text fields, as
   examples/replay_phase1.py did for Phase 1 ("replayed 2026-08-19").

2. Its provenance columns (git commit, code hash, dataset and universe
   snapshot) are captured at REGISTRATION. Here they describe the tree on
   2026-11-23, not the run. The commits that carried the preregistration, the
   script and the result are in `parameters`; no universe snapshot is granted,
   because none was recorded when these ran.

3. Its verdict comes from six stages, and none was run for these experiments.
   A decision needs at least one stage result, and a stage must report every
   check. So Stage 1 is recorded with every check False and the basis NOT RUN
   -- the platform's own reading of an unmeasured check (replay_phase1's rule
   2: never computed is a FAIL, never a skip). Nothing is granted in
   retrospect: no check is marked passed on this script's say-so.
   The Manager decision follows each experiment's OWN verdict: REJECT where
   its result file says REJECT (or REVERT), ARCHIVE where its own test
   accepted a hypothesis -- never PROMOTE. The stage number on a REJECT
   therefore says where the platform's record stops, NOT why the experiment
   was rejected; each rationale says so.
   SIDE EFFECT, stated: the platform's KPI counts rejections at stages 1-3 as
   cheap. These five were rejected after full backtests, so they flatter it.

4. A reservation can only be CLAIMED, whole, by one experiment. None of these
   preregistered R-FORWARD-2027 as its holdout, so none may claim it, and a
   claim would be false. The reads are recorded in `windows_touched` under
   informational purposes (the Manager allows any purpose other than
   "holdout" to repeat). The reservation stays formally unclaimed: a future
   claim is NOT refused by these rows. Whoever claims it must start after
   the last read recorded here.

USAGE
-----
    python examples/register_retroactive_e26_e33.py --dry-run
    python examples/register_retroactive_e26_e33.py --apply

--dry-run (the default) rehearses the whole registration on a temporary COPY
of the research database and prints every row it wrote there. --apply runs the
same rehearsal first and touches the real database only if it succeeded.
Idempotent: an experiment already decided is skipped; one left half-written
by an interrupted run is completed, never duplicated.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from sirat.experiments import ledger, stages, store                 # noqa: E402
from sirat.experiments import manager as mgr                        # noqa: E402


REGISTERED_ON = "2026-11-23"

AUTHOR = "retroactive registration %s" % REGISTERED_ON

RESERVATION_ID = "R-FORWARD-2027"

# Used only if the store holds no such reservation (a test database). The live
# record's own dates are read from the store and win.
RESERVATION_WINDOW = ("2026-08-19", "2027-08-19")

# Informational purposes. NEVER "holdout": a holdout touch inside the
# reservation needs a claim, a claim spends the whole reservation in one
# experiment's name, and none of these experiments preregistered it.
PURPOSE_EVALUATION = "evaluation-retroactive"
PURPOSE_INSIDE = "evaluation-inside-%s-unclaimed" % RESERVATION_ID

NOT_RUN = (
    "NOT RUN THROUGH THE PLATFORM: this experiment never called a gate in "
    "sirat.experiments.gates. Recorded False because the platform reads an "
    "unmeasured check as a fail, not because the check was run and failed "
    "(retroactive record, %s)." % REGISTERED_ON)


def _commit(sha, author_date):
    return {"commit": sha, "author_date": author_date}


# Commit facts from `git log` on 2026-11-23. They are re-checked against git
# before anything is written (see verify_git): a typo here must not become a
# permanent line in an append-only ledger.
RECORDS = (
    {
        "key": "E26",
        "experiment_id": "E26-v2-basket-stop-cadence-cash",
        "prereg_commit": _commit("bfd8fb0994217445fed3ebe0c51f780ce77b6146",
                                 "2026-11-05T08:02:31+03:30"),
        "result_commit": _commit("a0888ef1caa78f073e72970ee51b323caa392365",
                                 "2026-11-05T11:31:08+03:30"),
        "script": "examples/basket_random_null.py",
        "script_commit": _commit("bfd8fb0994217445fed3ebe0c51f780ce77b6146",
                                 "2026-11-05T08:02:31+03:30"),
        "seed": 20261105,
        "seed_note": "examples/basket_random_null.py SEED, as the "
                     "preregistration states (500 draws, seed 20261105)",
        "decision": mgr.REJECT,
    },
    {
        "key": "E27",
        "experiment_id": "E27-market-exposure-allocation",
        "prereg_commit": _commit("ecc494bf75f77b7b425121813e3c0629536c2fbd",
                                 "2026-11-05T08:52:40+03:30"),
        "result_commit": _commit("b772e2138d6ba66caaf36b57ed3da5b89d33b4b0",
                                 "2026-11-05T08:53:42+03:30"),
        "script": "examples/allocation_study.py",
        "script_commit": _commit("d4db10967dbb1901f045470de6abed83e3b11efc",
                                 "2026-11-05T08:53:28+03:30"),
        "seed": None,
        "decision": mgr.ARCHIVE,
    },
    {
        "key": "E28",
        "experiment_id": "E28-gold-sleeve",
        "prereg_commit": _commit("f1c4724a14910af3e2aa964c8e82d193a7b109a6",
                                 "2026-11-05T08:58:08+03:30"),
        "result_commit": _commit("173e089592542ce12e64843b3878416316766c0b",
                                 "2026-11-05T08:58:59+03:30"),
        "script": "examples/gold_sleeve_study.py",
        "script_commit": _commit("395efa020c525b08090c0c933bb1e05b7c06069f",
                                 "2026-11-05T08:58:32+03:30"),
        "seed": None,
        "decision": mgr.ARCHIVE,
    },
    {
        "key": "E30",
        "experiment_id": "E30-outlook-forward-yield",
        "prereg_commit": _commit("ee17634eb1a1191ecf6af137afea22822cac23c8",
                                 "2026-11-10T22:23:02+03:30"),
        "result_commit": _commit("8bd8ab8c1acfdfdaae144f21ca1fccc74c6923d3",
                                 "2026-11-10T22:25:19+03:30"),
        "script": "examples/outlook_backtest.py",
        "script_commit": _commit("ee17634eb1a1191ecf6af137afea22822cac23c8",
                                 "2026-11-10T22:23:02+03:30"),
        "seed": 30,
        "seed_note": "examples/outlook_backtest.py SEED (random null)",
        "decision": mgr.REJECT,
    },
    {
        "key": "E31",
        "experiment_id": "E31-laggard-quality-flow-turn",
        "prereg_commit": _commit("629e392046a01d931cf1224df43b89003bd9c832",
                                 "2026-11-10T22:38:29+03:30"),
        "result_commit": _commit("1ae0e2021108c80eb803331a4a6c2cd9a0038615",
                                 "2026-11-10T22:43:25+03:30"),
        "script": "examples/laggard_backtest.py",
        "script_commit": _commit("629e392046a01d931cf1224df43b89003bd9c832",
                                 "2026-11-10T22:38:29+03:30"),
        "seed": 30,
        "seed_note": "examples/outlook_backtest.py SEED, which "
                     "laggard_backtest.py imports (random null)",
        "decision": mgr.REJECT,
        "window_from": "E30",
    },
    {
        "key": "E32",
        "experiment_id": "E32-forward-fundamental-quality",
        "prereg_commit": _commit("7b31fba0fcc142fb9834695a4a65c60b781e803b",
                                 "2026-11-10T22:47:07+03:30"),
        "result_commit": _commit("73b767c13ab12261fdf64d09c5abe01f1af0dbe1",
                                 "2026-11-10T22:50:05+03:30"),
        "script": "examples/quality_backtest.py",
        "script_commit": _commit("7b31fba0fcc142fb9834695a4a65c60b781e803b",
                                 "2026-11-10T22:47:07+03:30"),
        "seed": 30,
        "seed_note": "examples/outlook_backtest.py SEED, which "
                     "quality_backtest.py imports (random null)",
        "decision": mgr.REJECT,
        "window_from": "E30",
    },
    {
        "key": "E33",
        "experiment_id": "E33-best-of-cheap-industries",
        "prereg_commit": _commit("b2ddd6a7995876abb342aaa6766063bf827719fe",
                                 "2026-11-11T21:00:41+03:30"),
        "result_commit": _commit("0cad8a7ce794ba920a9a2f89f0f5449e634ba1da",
                                 "2026-11-11T21:04:10+03:30"),
        "script": "examples/cheap_sector_backtest.py",
        "script_commit": _commit("b2ddd6a7995876abb342aaa6766063bf827719fe",
                                 "2026-11-11T21:00:41+03:30"),
        "seed": 33,
        "seed_note": "examples/cheap_sector_backtest.py SEED (random null)",
        "decision": mgr.REJECT,
    },
)


class Refused(Exception):
    """The files or git do not say what this script is about to record."""


# ==============================
# READING WHAT THE FILES SAY
# ==============================

def prereg_file(key):
    return "backtest_output/%s_preregistration.json" % key


def result_file(key):
    return "backtest_output/%s_results.json" % key


def _load(relative):
    with open(os.path.join(ROOT, relative), encoding="utf-8") as handle:
        return json.load(handle)


def _sha256(relative):
    with open(os.path.join(ROOT, relative), "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def _text(value):
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def hypothesis_of(prereg):
    """The question and the hypotheses, in the preregistration's own words."""

    if isinstance(prereg.get("hypothesis"), str):
        return prereg["hypothesis"]

    claims = prereg.get("hypotheses") or prereg.get("hypothesis") or {}

    parts = [prereg.get("question", "").strip()]
    parts.extend("%s: %s" % (name, claims[name]) for name in sorted(claims))

    return " | ".join(p for p in parts if p)


def windows_of(key, result, results):
    """[(start, end, role)] as the result file states them.

    E31 and E32 store no window: their preregistrations fix E30's decision
    dates, so E30's recorded window is theirs, and the role says where it
    came from instead of presenting it as their own field.
    """

    if key == "E26":
        spans = sorted(result["windows"])
        return [tuple(span.split("..")) + ("evaluation window %d" % (i + 1),)
                for i, span in enumerate(spans)]

    if key == "E27":
        return [(result["windows"][name]["range"][0],
                 result["windows"][name]["range"][1], name)
                for name in ("primary", "secondary")]

    if key == "E28":
        return [(result["range"][0], result["range"][1], "the one window")]

    if key in ("E31", "E32"):
        window = results["E30"]["window"]
        return [(window[0], window[1],
                 "E30's window -- this result file records none, and the "
                 "preregistration fixes E30's decision dates")]

    return [(result["window"][0], result["window"][1], "evaluation window")]


def verdict_of(key, result):
    """The verdict, copied from the result file -- never restated."""

    if key in ("E26", "E27", "E28"):
        return {"verdict": result["verdict"]}

    stated = {"decision": result["decision"]}

    for field in ("alpha", "primary", "random_null"):
        if field in result:
            stated[field] = result[field]

    return stated


def own_verdict_matches(record, result):
    """The Manager decision this script will record must be what the result
    file says, or nothing is written."""

    key = record["key"]

    if key == "E26":
        said = str(result["verdict"]).strip().upper().startswith("REVERT")
        return said and record["decision"] == mgr.REJECT

    if key in ("E27", "E28"):
        accepted = [name for name, value in result["verdict"].items()
                    if value == "ACCEPT"]
        return bool(accepted) and record["decision"] == mgr.ARCHIVE

    return result["decision"] == "REJECT" and record["decision"] == mgr.REJECT


# ==============================
# GIT: THE COMMIT FACTS, CHECKED
# ==============================

def _git(*arguments):
    try:
        out = subprocess.run(["git"] + list(arguments), cwd=ROOT,
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None

    if out.returncode != 0:
        return None

    return out.stdout.strip()


def verify_git(records=RECORDS):
    """Raise unless git confirms every commit this script is about to cite,
    and that the cited files are unchanged since those commits."""

    if _git("rev-parse", "HEAD") is None:
        raise Refused(
            "git is not available, so the commit hashes and dates cannot be "
            "checked. Nothing is written on unchecked facts.")

    problems = []

    for record in records:
        key = record["key"]

        for relative, fact, first in (
                (prereg_file(key), record["prereg_commit"], False),
                (result_file(key), record["result_commit"], False),
                (record["script"], record["script_commit"], True)):

            arguments = ["log", "--format=%H %aI"]

            if first:
                # The commit that ADDED the script: later edits to a shared
                # helper do not change when it first existed.
                arguments.append("--diff-filter=A")

            lines = (_git(*(arguments + ["--", relative])) or "").splitlines()

            found = lines[-1].split() if (lines and first) else (
                lines[0].split() if lines else [])

            if found != [fact["commit"], fact["author_date"]]:
                problems.append("%s: git says %s, this script says %s %s"
                                % (relative, " ".join(found) or "nothing",
                                   fact["commit"], fact["author_date"]))

            if not first and len(lines) != 1:
                problems.append("%s has %d commits; the record assumes it "
                                "was committed once and never changed"
                                % (relative, len(lines)))

        dirty = _git("status", "--porcelain", "--", prereg_file(key),
                     result_file(key))

        if dirty:
            problems.append("%s: the working copy differs from the commit "
                            "(%s)" % (key, dirty.replace("\n", "; ")))

    if problems:
        raise Refused("git does not confirm the record:\n  "
                      + "\n  ".join(problems))


# ==============================
# WHAT WILL BE WRITTEN
# ==============================

def reservation_window(path=None):
    for held in store.reservations(path=path):
        if held["reservation_id"] == RESERVATION_ID:
            return (held["window_start"], held["window_end"])

    return RESERVATION_WINDOW


def inside_reservation(windows, reserved):
    """The part of each window that lies inside the reservation."""

    out = []

    for start, end, _role in windows:
        low, high = max(start, reserved[0]), min(end, reserved[1])
        if low <= high:
            out.append((low, high))

    return out


def plan(record, results, reserved):
    """Every argument the Manager will be given for one experiment."""

    key = record["key"]
    prereg = _load(prereg_file(key))
    result = results[key]

    if prereg.get("experiment_id") != record["experiment_id"]:
        raise Refused("%s names experiment %r, not %r"
                      % (prereg_file(key), prereg.get("experiment_id"),
                         record["experiment_id"]))

    if not own_verdict_matches(record, result):
        raise Refused("%s does not state the verdict this script would "
                      "record for %s (%s)" % (result_file(key), key,
                                              record["decision"]))

    windows = windows_of(key, result, results)
    inside = inside_reservation(windows, reserved)
    verdict = verdict_of(key, result)

    read = "; ".join("%s..%s (%s)" % w for w in windows)
    slice_text = ("; ".join("%s..%s" % w for w in inside)
                  if inside else "none")

    pc, rc, sc = (record["prereg_commit"], record["result_commit"],
                  record["script_commit"])

    notes = (
        "RETROACTIVE RECORD. Registered after the fact on %s: this experiment "
        "was preregistered as %s (commit %s, %s) and run by %s (first "
        "committed %s, %s) OUTSIDE the Experiment Manager; its result is %s "
        "(commit %s, %s). Verdict as that file states it: %s. Data window "
        "evaluated, from the result file: %s. Inside the reservation %s "
        "(%s..%s), read WITHOUT a claim: %s. The provenance columns of this "
        "record (git commit, code hash, dataset snapshot) describe the tree "
        "on %s, not the run; no universe snapshot was recorded when it ran "
        "and none is granted. No platform gate was run."
        % (REGISTERED_ON, prereg_file(key), pc["commit"][:7],
           pc["author_date"], record["script"], sc["commit"][:7],
           sc["author_date"], result_file(key), rc["commit"][:7],
           rc["author_date"],
           # The full stop is the sentence's own; the verdict is carried
           # verbatim in the parameters and the rationale.
           _text(verdict.get("verdict", verdict.get("decision"))).rstrip("."),
           read, RESERVATION_ID, reserved[0], reserved[1], slice_text,
           REGISTERED_ON))

    seed = record["seed"]

    parameters = {
        "retroactive_record": True,
        "registered_after_the_fact_on": REGISTERED_ON,
        "preregistration_file": prereg_file(key),
        "preregistration_file_sha256": _sha256(prereg_file(key)),
        "preregistration_commit": pc,
        "result_file": result_file(key),
        "result_file_sha256": _sha256(result_file(key)),
        "result_commit": rc,
        "script": record["script"],
        "script_first_commit": sc,
        "data_windows_evaluated": [
            {"start": s, "end": e, "role": role} for s, e, role in windows],
        "read_inside_reservation": {
            "reservation": RESERVATION_ID,
            "reserved_window": list(reserved),
            "read": [list(w) for w in inside],
            "claimed": False,
        },
        "seed_note": (record.get("seed_note") if seed is not None else
                      "deterministic study, no random draw: the 0 stored as "
                      "the seed is a placeholder the record requires"),
        "verdict_as_the_result_file_states": verdict,
        "original_preregistration": prereg,
    }

    gates = {name: prereg[name] for name in
             ("hypotheses", "hypothesis", "test", "evaluation", "benchmarks",
              "decision_rule") if name in prereg}

    # Verbatim, not transcribed into numbers: a threshold retyped here could
    # differ from the one the experiment was actually judged against.
    thresholds = {name: prereg[name] for name in ("test", "decision_rule")
                  if name in prereg}

    if isinstance(result.get("verdict"), dict) and \
            "alpha_per_hypothesis" in result["verdict"]:
        thresholds["alpha_per_hypothesis"] = \
            result["verdict"]["alpha_per_hypothesis"]

    if "alpha" in result:
        thresholds["alpha"] = result["alpha"]

    expected = prereg["expected_failure"]

    detail = {check: {"value": None, "source": prereg_file(key),
                      "basis": NOT_RUN}
              for check in stages.REQUIRED_CHECKS[stages.DATA_INTEGRITY]}

    if "pit_blocked_future_reads" in result:
        # The experiment's own point-in-time count, carried as information.
        # It does not turn the check into a pass: the platform's gate, which
        # also tests the forward alignment, was not the thing that ran.
        detail["point_in_time"]["value"] = result["pit_blocked_future_reads"]
        detail["point_in_time"]["source"] = (
            "%s / pit_blocked_future_reads" % result_file(key))

    detail["declared_limits_as_preregistered"] = prereg.get("declared_limits")

    if record["decision"] == mgr.ARCHIVE:
        closing = (
            "ARCHIVE, not PROMOTE and not a platform acceptance: 'ACCEPT' is "
            "the study's own preregistered test, on the assumptions its "
            "preregistration declares.")
    else:
        closing = (
            "REJECT follows the experiment's own verdict. The stage number "
            "says where the platform's record stops; it is NOT the reason "
            "the experiment gave.")

    rationale = (
        "RETROACTIVE RECORD, registered after the fact on %s. Verdict as the "
        "experiment's own result file states it (%s, commit %s, %s): %s. "
        "Preregistration: %s, commit %s, %s. The Experiment Manager did not "
        "adjudicate this experiment when it ran: none of its six stages was "
        "run, so Stage 1 is recorded as not run, which the platform reads as "
        "a fail. %s"
        % (REGISTERED_ON, result_file(key), rc["commit"][:7],
           rc["author_date"], _text(verdict), prereg_file(key),
           pc["commit"][:7], pc["author_date"], closing))

    return {
        "key": key,
        "register": {
            "experiment_id": record["experiment_id"],
            "hypothesis": "[Registered retroactively %s] %s"
                          % (REGISTERED_ON, hypothesis_of(prereg)),
            "strategy_version": "%s as first committed %s (%s); result "
                                "commit %s"
                                % (record["script"], sc["commit"][:7],
                                   sc["author_date"], rc["commit"][:7]),
            "researcher": "%s (original researcher, as preregistered: %s)"
                          % (AUTHOR, prereg.get("researcher", "not stated")),
            "notes": notes,
            "expected_failure": (expected if isinstance(expected, list)
                                 else [expected]),
            "acceptance_criteria": {
                name: prereg[name] for name in
                ("test", "evaluation", "decision_rule") if name in prereg},
            "preregistration": {
                "parameters": parameters,
                "validation_gates": gates,
                "expected_metrics": {
                    "none_declared": "the original preregistration states no "
                                     "expected metric values; its "
                                     "expected_failure is in the registration"},
                "rejection_thresholds": thresholds,
                "seed": 0 if seed is None else seed,
            },
            "config": {"retroactive": True,
                       "preregistration_file": prereg_file(key),
                       "result_file": result_file(key)},
            "universe_snapshot": None,
        },
        "windows": ([(s, e, PURPOSE_EVALUATION) for s, e, _ in windows]
                    + [(s, e, PURPOSE_INSIDE) for s, e in inside]),
        "stage": {
            "stage": stages.DATA_INTEGRITY,
            "metrics": {check: False for check in
                        stages.REQUIRED_CHECKS[stages.DATA_INTEGRITY]},
            "detail": detail,
        },
        "decision": {"decision": record["decision"], "rationale": rationale,
                     "by": "%s (verdict as recorded in %s)"
                           % (AUTHOR, result_file(key))},
    }


def plans(path=None, records=RECORDS):
    results = {r["key"]: _load(result_file(r["key"])) for r in RECORDS}
    reserved = reservation_window(path)

    return [plan(record, results, reserved) for record in records]


# ==============================
# WRITING IT, THROUGH THE MANAGER
# ==============================

def _window_recorded(experiment_id, start, end, purpose, path):
    return experiment_id in store.window_touched_by(start, end, purpose,
                                                    path=path)


def apply_one(item, path=None):
    """Register, record windows and Stage 1, decide. Returns what happened.

    Every write is a Manager method. Each step is skipped if the record
    already holds it, so a run interrupted half way is finished by the next
    one rather than refused or doubled.
    """

    experiment_id = item["register"]["experiment_id"]

    for purpose in {w[2] for w in item["windows"]}:
        if purpose == mgr.HOLDOUT:
            raise Refused("a retroactive read must never be recorded as a "
                          "holdout: it would need a claim on %s"
                          % RESERVATION_ID)

    try:
        handle = mgr.Manager.load(experiment_id, path=path)
        registered = False
    except mgr.UnknownExperiment:
        handle = mgr.Manager.register(path=path, **item["register"])
        registered = True

    if handle.decision() is not None:
        return {"experiment_id": experiment_id, "action": "skipped",
                "reason": "already decided (%s)"
                          % handle.decision()["decision"]}

    for start, end, purpose in item["windows"]:
        if not _window_recorded(experiment_id, start, end, purpose, path):
            handle.touch_window(start, end, purpose)

    if not handle.stage_results():
        handle.run_stage(item["stage"]["stage"], item["stage"]["metrics"],
                         detail=item["stage"]["detail"])

    closed = handle.decide(item["decision"]["decision"],
                           item["decision"]["rationale"],
                           item["decision"]["by"])

    return {"experiment_id": experiment_id,
            "action": "registered" if registered else "completed",
            "decision": closed["decision"],
            "rejected_at_stage": closed["rejected_at_stage"],
            "ledger_entry_hash": closed["ledger_entry_hash"]}


def apply(path=None, check_git=True, records=RECORDS):
    """All seven, in order. Raises before the first write if anything about
    the files or git disagrees with what would be recorded."""

    if check_git:
        verify_git(records)

    todo = plans(path, records)            # every refusal happens here

    return [apply_one(item, path=path) for item in todo]


# ==============================
# REPORTING
# ==============================

def rows_written(path, ids):
    """Read back, row by row, what the record now holds for these ids."""

    conn = store.connect(path)

    try:
        out = {}

        for experiment_id in ids:
            block = {}

            for table in ("experiments", "preregistration", "stage_results",
                          "windows_touched", "decisions"):
                rows = conn.execute(
                    "SELECT * FROM %s WHERE experiment_id = ?" % table,
                    (experiment_id,)).fetchall()
                block[table] = [dict(r) for r in rows]

            rows = conn.execute(
                "SELECT seq, experiment_id, appended_at, prev_hash, "
                "entry_hash FROM ledger WHERE experiment_id = ?",
                (experiment_id,)).fetchall()
            block["ledger"] = [dict(r) for r in rows]

            out[experiment_id] = block

        out["windows_reserved"] = [
            dict(r) for r in conn.execute("SELECT * FROM windows_reserved")]
    finally:
        conn.close()

    return out


def rehearse(path=None, check_git=True):
    """The whole registration on a temporary COPY of the research database.

    The copy starts as the live record, so the rehearsal meets the same
    existing rows, triggers and chain the real run will. It is deleted
    afterwards; nothing here can reach the live file.
    """

    live = path or store.DB_PATH

    folder = tempfile.mkdtemp(prefix="sirat_retro_")
    copy = os.path.join(folder, "research_copy.db")

    try:
        if os.path.exists(live):
            shutil.copy2(live, copy)

        before = ledger.verify(copy)
        outcomes = apply(copy, check_git=check_git)
        after = ledger.verify(copy)
        ids = [o["experiment_id"] for o in outcomes]

        return {"outcomes": outcomes, "ledger_before": before,
                "ledger_after": after, "rows": rows_written(copy, ids),
                "kpi_after": stages.cheap_rejection_rate(
                    store.all_decisions(path=copy))}
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def say(message=""):
    print(message, flush=True)


def _print_outcomes(outcomes, rows):
    for outcome in outcomes:
        experiment_id = outcome["experiment_id"]
        entry = (rows[experiment_id]["ledger"] or [{}])[0]

        if outcome["action"] == "skipped":
            say("  %-36s skipped: %s (ledger seq %s)"
                % (experiment_id, outcome["reason"], entry.get("seq")))
            continue

        say("  %-36s %-10s %-7s stage %s   ledger seq %s  %s"
            % (experiment_id, outcome["action"], outcome["decision"],
               outcome["rejected_at_stage"], entry.get("seq"),
               (entry.get("entry_hash") or "")[:16]))


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Register E26-E33 with the Experiment Manager, "
                    "retroactively and labelled as such.")
    parser.add_argument("--dry-run", action="store_true",
                        help="rehearse on a temporary copy and print every "
                             "row (the default)")
    parser.add_argument("--apply", action="store_true",
                        help="rehearse, then write to the research database")
    parser.add_argument("--db", default=None,
                        help="research database (default: the live one)")
    parser.add_argument("--full", action="store_true",
                        help="with --dry-run: print the rows in full")

    args = parser.parse_args(argv)

    if args.apply and args.dry_run:
        parser.error("--apply and --dry-run are opposites")

    target = args.db or store.DB_PATH

    say("=" * 78)
    say("E26-E33: RETROACTIVE REGISTRATION (%s)" % REGISTERED_ON)
    say("=" * 78)
    say("  research database  %s" % target)

    try:
        rehearsal = rehearse(target)
    except (Refused, mgr.GovernanceError, stages.StageIncomplete,
            ledger.LedgerError) as exc:
        say()
        say("REFUSED -- nothing was written anywhere but a temporary copy:")
        say("  %s: %s" % (type(exc).__name__, exc))
        return 2

    say("  rehearsal on a copy: ledger %d -> %d entries, chain %s"
        % (rehearsal["ledger_before"]["entries"],
           rehearsal["ledger_after"]["entries"],
           "intact" if rehearsal["ledger_after"]["intact"] else "BROKEN"))

    if not rehearsal["ledger_after"]["intact"]:
        say("REFUSED -- the rehearsal broke the chain: %s"
            % rehearsal["ledger_after"].get("reason"))
        return 2

    if not args.apply:
        say()
        say("DRY RUN -- the live database was not opened for writing.")
        say("What --apply would write (timestamps, and so the entry hashes, "
            "will differ):")
        say()
        _print_outcomes(rehearsal["outcomes"], rehearsal["rows"])
        say()
        say("  KPI after: %s" % json.dumps(rehearsal["kpi_after"]))
        say("  reservation after: %s" % json.dumps(
            [{k: r[k] for k in ("reservation_id", "window_start",
                                "window_end", "claimed_by")}
             for r in rehearsal["rows"]["windows_reserved"]]))
        say()

        for experiment_id, block in rehearsal["rows"].items():
            if experiment_id == "windows_reserved":
                continue

            say("-" * 78)
            say(experiment_id)

            for table, rows in block.items():
                for row in rows:
                    text = json.dumps(row, ensure_ascii=False, default=str)
                    say("  [%s] %s" % (table, text if args.full
                                       else text[:900] + (
                                           " ..." if len(text) > 900 else "")))

        return 0

    outcomes = apply(target)
    ids = [o["experiment_id"] for o in outcomes]
    chain = ledger.verify(target)

    say()
    say("APPLIED to %s" % target)
    _print_outcomes(outcomes, rows_written(target, ids))
    say()
    say("  ledger: %d entries, chain %s, head %s"
        % (chain["entries"], "intact" if chain["intact"] else "BROKEN",
           chain.get("head", "")[:16]))

    return 0 if chain["intact"] else 1


if __name__ == "__main__":
    sys.exit(main())
