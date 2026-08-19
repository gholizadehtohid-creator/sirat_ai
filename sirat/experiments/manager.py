"""The Experiment Manager -- the object that makes validation non-optional.

WHAT THIS REPLACES
------------------
Fifteen experiments were governed by `experiment_registry.md`, a markdown table
maintained by hand. The discipline in it was real and it caught real problems
(E5 refused to burn a holdout; E11 reported its own validity failure). What it
could not do is REFUSE. A researcher in a hurry, or the same researcher on a bad
afternoon, could skip a stage, move a threshold, or publish a number with no
provenance, and nothing anywhere would object.

Every method here exists to object.

THE FOUR REFUSALS, IN ORDER OF HOW MUCH THEY WOULD HAVE SAVED
--------------------------------------------------------------
1. `run_stage` refuses a stage whose predecessor has not passed. Alpha Core's
   cost arithmetic -- a 0.55% edge against a 1.04% round trip -- was an
   afternoon's work and was not done until experiment 13. Under this class it
   is Stage 3 and nothing reaches Stage 4 without it.

2. `run_stage` refuses a verdict the caller supplies. The caller reports what it
   MEASURED; the platform decides PASS or FAIL by comparing those measurements
   to thresholds frozen before the run. A stage that omits a required check is
   incomplete, not passing -- four `null` execution counters were read as "no
   queues were ever hit" for an entire programme, and absence must never again
   be read as approval.

3. `thresholds` is the only door to a frozen number, and it re-verifies the
   preregistration hash on every call. Moving a threshold after seeing a result
   is the single most damaging thing a researcher can do and it leaves no trace
   in a docstring.

4. `decide` refuses to PROMOTE anything non-reproducible. Today that means it
   refuses everything, because git is not installed and no result in this
   repository can be re-derived. That is the correct behaviour, not a bug to
   work around.

WHAT THIS CLASS DOES NOT CLAIM
-------------------------------
It is not a sandbox. A determined researcher can import `store` directly, or
edit `sirat_research.db` with a SQL prompt -- the triggers make that raise, but
Python cannot stop someone who deletes the file. The control is designed for the
realistic failure, which is not sabotage: it is a competent person under time
pressure taking a shortcut that looks harmless. Every shortcut here raises.
"""

import json
import os

import pandas as pd

from sirat.experiments import provenance, stages, store


class GovernanceError(Exception):
    """Base for every refusal. Catching this catches all of them."""


class RegistrationIncomplete(GovernanceError):
    """A required registration field is missing or empty."""


class PreregistrationIncomplete(GovernanceError):
    """A required preregistration field is missing or empty."""


class PreregistrationTampered(GovernanceError):
    """The frozen record no longer hashes to what was recorded at freeze."""


class StageOutOfOrder(GovernanceError):
    """A stage was attempted before its predecessor passed."""


class ExperimentClosed(GovernanceError):
    """The experiment already reached a decision."""


class NoEvidence(GovernanceError):
    """A decision was attempted with no stage results behind it."""


class ValidationMissing(GovernanceError):
    """PROMOTE was attempted without the full validation record."""


class NotReproducible(GovernanceError):
    """PROMOTE was attempted on a result that cannot be re-derived."""


class WindowAlreadySpent(GovernanceError):
    """A holdout window was about to be evaluated a second time."""


class UnknownExperiment(GovernanceError):
    """No such experiment_id in the record."""


# Registration fields that carry meaning. Every one is required, and "required"
# means a non-empty value rather than a present key -- `hypothesis=""` is not a
# hypothesis and accepting it would make the whole check decorative.
REQUIRED_REGISTRATION = (
    "experiment_id", "hypothesis", "strategy_version", "researcher",
    "notes", "expected_failure", "acceptance_criteria",
)

REQUIRED_PREREGISTRATION = (
    "parameters", "validation_gates", "expected_metrics",
    "rejection_thresholds", "seed",
)

PROMOTE = "PROMOTE"
REJECT = "REJECT"
ARCHIVE = "ARCHIVE"
SUPERSEDE = "SUPERSEDE"

DECISIONS = (PROMOTE, REJECT, ARCHIVE, SUPERSEDE)

# Purposes a window can be touched for. `holdout` is the one that can be spent
# exactly once; the others are informational and may repeat.
HOLDOUT = "holdout"


def _nonempty(value):
    """Present, and actually carrying something.

    An empty dict, an empty list and a whitespace string are all ways of
    satisfying a required field without answering it.
    """

    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (dict, list, tuple, set)):
        return bool(value)
    return True


class Manager(object):
    """One experiment, from registration to decision.

    Construct via `register()` for a new experiment or `load()` for one already
    in the record. The constructor is deliberately not the entry point: an
    experiment that exists only as a Python object has not been registered, and
    the whole design rests on registration being the thing that happened.
    """

    def __init__(self, experiment_id, path=None):
        self.experiment_id = experiment_id
        self.path = path

        # An empty or absent research database is not an error condition, it is
        # the ordinary state before the first registration. Without this the
        # caller gets `no such table: experiments` -- a SQLite implementation
        # detail -- instead of being told the experiment was never registered,
        # which is the thing they actually need to know.
        store.initialise(path)

        record = store.get_experiment(experiment_id, path=path)

        if record is None:
            raise UnknownExperiment(
                "no experiment %r in the record. Register it before running "
                "anything." % experiment_id)

        self._record = record

    # ==============================
    # REGISTRATION
    # ==============================

    @classmethod
    def register(cls, experiment_id, hypothesis, strategy_version, researcher,
                 notes, expected_failure, acceptance_criteria,
                 preregistration, config=None, universe_snapshot=None,
                 path=None):
        """Create an experiment. Nothing may run before this returns.

        Registration and preregistration happen together and cannot be
        separated. Registering first and preregistering "once the parameters
        settle" is the same as not preregistering -- by the time the parameters
        settle, a result usually exists.
        """

        store.initialise(path)

        fields = {
            "experiment_id": experiment_id,
            "hypothesis": hypothesis,
            "strategy_version": strategy_version,
            "researcher": researcher,
            "notes": notes,
            "expected_failure": expected_failure,
            "acceptance_criteria": acceptance_criteria,
        }

        missing = [k for k in REQUIRED_REGISTRATION if not _nonempty(fields[k])]

        if missing:
            raise RegistrationIncomplete(
                "cannot register without: %s. Every field is required because "
                "every one of them was missing from at least one Phase 1 "
                "experiment." % ", ".join(missing))

        if store.get_experiment(experiment_id, path=path) is not None:
            raise GovernanceError(
                "experiment %r is already registered. Results are never "
                "overwritten -- register a new id citing this one."
                % experiment_id)

        prereg = cls._validate_preregistration(preregistration)

        prereg_hash = provenance.content_hash(prereg)

        block = provenance.capture(config=config,
                                   universe_snapshot_id=universe_snapshot)

        record = dict(fields)
        record.update({
            "prereg_hash": prereg_hash,
            "git_commit": block["git_commit"],
            "git_dirty": block["git_dirty"],
            "code_hash": block["code_hash"],
            "config_hash": block["config_hash"],
            "dataset_snapshot": block["dataset_snapshot"],
            "universe_snapshot": block["universe_snapshot"],
            "reproducible": block["reproducible"],
            "registered_at": block["executed_at"],
            "status": "REGISTERED",
            "superseded_by": None,
        })

        store.insert_experiment(record, path=path)
        store.insert_preregistration(experiment_id, prereg, prereg_hash,
                                     path=path)

        return cls(experiment_id, path=path)

    @staticmethod
    def _validate_preregistration(prereg):
        """Every field present and carrying something, seed an actual integer.

        The seed has no default anywhere in this platform. A default seed is a
        seed nobody chose, and a result that depends on one cannot be reproduced
        by someone reading the record -- they would have to read the source of
        whatever library supplied it.
        """

        if not isinstance(prereg, dict):
            raise PreregistrationIncomplete(
                "preregistration must be a mapping, got %s"
                % type(prereg).__name__)

        missing = [k for k in REQUIRED_PREREGISTRATION
                   if not _nonempty(prereg.get(k))]

        if missing:
            raise PreregistrationIncomplete(
                "preregistration is missing: %s" % ", ".join(missing))

        try:
            seed = int(prereg["seed"])
        except (TypeError, ValueError):
            raise PreregistrationIncomplete(
                "seed must be an integer, got %r" % (prereg["seed"],))

        gates = prereg["validation_gates"]

        if not isinstance(gates, dict):
            raise PreregistrationIncomplete(
                "validation_gates must map a gate name to its condition")

        thresholds = prereg["rejection_thresholds"]

        if not isinstance(thresholds, dict):
            raise PreregistrationIncomplete(
                "rejection_thresholds must map a metric name to its threshold")

        return {
            "parameters": prereg["parameters"],
            "validation_gates": gates,
            "expected_metrics": prereg["expected_metrics"],
            "rejection_thresholds": thresholds,
            "seed": seed,
        }

    @classmethod
    def load(cls, experiment_id, path=None):
        return cls(experiment_id, path=path)

    # ==============================
    # THE FROZEN RECORD
    # ==============================

    def preregistration(self):
        """The frozen record, hash-verified on every read.

        Verification is not paranoia about the triggers -- it is the check that
        catches the failure mode the triggers cannot see: a database restored
        from a backup, or rebuilt by a script, in which the rows exist and no
        UPDATE ever ran.
        """

        prereg = store.get_preregistration(self.experiment_id, path=self.path)

        if prereg is None:
            raise PreregistrationIncomplete(
                "experiment %r has no preregistration. It cannot run."
                % self.experiment_id)

        frozen = {k: prereg[k] for k in REQUIRED_PREREGISTRATION}

        recomputed = provenance.content_hash(frozen)

        if recomputed != prereg["content_hash"]:
            raise PreregistrationTampered(
                "preregistration for %r no longer hashes to its frozen value "
                "(%s != %s). The record has been altered outside the platform; "
                "no result from this experiment may be reported."
                % (self.experiment_id, recomputed, prereg["content_hash"]))

        if recomputed != self._record["prereg_hash"]:
            raise PreregistrationTampered(
                "preregistration hash disagrees with the registration record "
                "(%s != %s)." % (recomputed, self._record["prereg_hash"]))

        return prereg

    def thresholds(self):
        """The only door to a rejection threshold.

        A gate that reads its threshold from a module constant is reading a
        number that can be edited after the result exists. A gate that reads it
        from here is reading a number that cannot.
        """

        return dict(self.preregistration()["rejection_thresholds"])

    def parameters(self):
        return self.preregistration()["parameters"]

    def gates(self):
        return dict(self.preregistration()["validation_gates"])

    def seed(self):
        return int(self.preregistration()["seed"])

    def expected_metrics(self):
        return dict(self.preregistration()["expected_metrics"])

    # ==============================
    # STAGES
    # ==============================

    def stage_results(self):
        return store.get_stages(self.experiment_id, path=self.path)

    def decision(self):
        return store.get_decision(self.experiment_id, path=self.path)

    def _assert_open(self):
        if self.decision() is not None:
            raise ExperimentClosed(
                "experiment %r already reached a decision. Register a new "
                "experiment citing it." % self.experiment_id)

    def next_stage(self):
        """The stage this experiment is allowed to run next, or None."""

        done = {r["stage"]: r for r in self.stage_results()}

        for stage in stages.ORDER:
            result = done.get(stage)
            if result is None:
                return stage
            if result["status"] != stages.PASS:
                return None

        return None

    def run_stage(self, stage, metrics, detail=None):
        """Record one stage. The platform decides the verdict, not the caller.

        `metrics` maps each of the stage's required checks to a boolean: True
        for "this check was run and the result was acceptable", False for "this
        check was run and it was not". A missing key is neither -- it raises,
        because a stage that did not run survivorship reports nothing about
        survivorship.

        `detail` carries the numbers behind those booleans. It is stored, never
        interpreted: the record has to let a third party recompute the verdict,
        and a bare PASS does not.
        """

        self._assert_open()

        stage = int(stage)

        if stage not in stages.NAMES:
            raise GovernanceError("no stage %d; stages are 1-6" % stage)

        if hasattr(metrics, "metrics"):
            # A GateResult from sirat.experiments.gates. Accepting the object
            # directly keeps the caller from unpacking it wrongly.
            detail = detail if detail is not None else metrics.detail
            metrics = metrics.metrics

        done = {r["stage"]: r for r in self.stage_results()}

        if stage in done:
            raise StageOutOfOrder(
                "stage %d is already recorded for %r. Stage results are "
                "append-only; re-run under a new experiment id."
                % (stage, self.experiment_id))

        for earlier in stages.ORDER:
            if earlier >= stage:
                break
            previous = done.get(earlier)
            if previous is None:
                raise StageOutOfOrder(
                    "stage %d (%s) cannot run: stage %d (%s) has not run. The "
                    "ordering is the product -- it is what makes rejection "
                    "cheap." % (stage, stages.NAMES[stage], earlier,
                                stages.NAMES[earlier]))
            if previous["status"] != stages.PASS:
                raise StageOutOfOrder(
                    "stage %d (%s) cannot run: stage %d (%s) reported %s. An "
                    "idea that failed a cheap check does not earn an expensive "
                    "one." % (stage, stages.NAMES[stage], earlier,
                              stages.NAMES[earlier], previous["status"]))

        stages.validate_metrics(stage, metrics)

        status, failed = stages.verdict_from(metrics)

        text = ("all checks passed" if status == stages.PASS
                else "failed: " + ", ".join(sorted(failed)))

        store.insert_stage(self.experiment_id, stage, stages.NAMES[stage],
                           status, text, {"checks": dict(metrics),
                                          "detail": detail or {}},
                           path=self.path)

        return {"stage": stage, "stage_name": stages.NAMES[stage],
                "status": status, "failed": failed, "detail": detail or {}}

    # ==============================
    # HOLDOUT LEDGER
    # ==============================

    def touch_window(self, start, end, purpose):
        """Record that this experiment evaluated a window -- and refuse a repeat.

        A holdout is a quantity that is spent, not a dataset that is used. E5
        stopped an experiment by noticing this by hand, in a preflight audit
        that had to be written specially. It is now arithmetic.
        """

        self._assert_open()

        if purpose == HOLDOUT:
            already = [e for e in store.window_touched_by(
                start, end, purpose, path=self.path)
                if e != self.experiment_id]

            if already:
                raise WindowAlreadySpent(
                    "holdout %s..%s was already evaluated by %s. A window "
                    "evaluated twice is not a holdout; it is a training set "
                    "with extra steps." % (start, end, ", ".join(already)))

        store.record_window(self.experiment_id, start, end, purpose,
                            path=self.path)

    # ==============================
    # DECISION
    # ==============================

    def decide(self, decision, rationale, by):
        """Close the experiment. Refuses more than it accepts, on purpose."""

        self._assert_open()

        if decision not in DECISIONS:
            raise GovernanceError(
                "decision must be one of %s" % ", ".join(DECISIONS))

        if not _nonempty(rationale) or not _nonempty(by):
            raise GovernanceError(
                "a decision needs a rationale and a person. An unattributed "
                "decision is not a decision.")

        results = self.stage_results()

        if not results:
            raise NoEvidence(
                "experiment %r has no stage results. There is nothing to "
                "decide about." % self.experiment_id)

        failed = [r for r in results if r["status"] == stages.FAIL]

        rejected_at = min(r["stage"] for r in failed) if failed else None

        if decision == REJECT and rejected_at is None:
            raise GovernanceError(
                "REJECT requires a failed stage. If the idea passed every gate "
                "and is still being rejected, the reason belongs in a gate "
                "that runs next time -- record it and register the change.")

        if decision == PROMOTE:
            self._assert_promotable(results)

        store.insert_decision(self.experiment_id, decision, rationale,
                              rejected_at, by, path=self.path)

        self._record = store.get_experiment(self.experiment_id, path=self.path)

        return {
            "experiment_id": self.experiment_id,
            "decision": decision,
            "rejected_at_stage": rejected_at,
            "rejected_at_stage_name": (stages.NAMES[rejected_at]
                                       if rejected_at else None),
            "cheap_rejection": bool(rejected_at in stages.CHEAP_STAGES),
        }

    def _assert_promotable(self, results):
        """PROMOTE is the only decision that can lose money, so it is the only
        one with preconditions."""

        passed = {r["stage"] for r in results if r["status"] == stages.PASS}

        missing = [s for s in stages.ORDER if s not in passed]

        if missing:
            raise ValidationMissing(
                "cannot PROMOTE without every stage passed. Missing: %s. "
                "Validation existed for the whole of Phase 1 and was never "
                "called; this is the constraint that makes that impossible."
                % ", ".join("%d (%s)" % (s, stages.NAMES[s]) for s in missing))

        if not self._record["reproducible"]:
            # Explain the RECORD, not the working tree as it stands now.
            #
            # The refusal is driven by what was captured at registration, and
            # that is permanent: an experiment registered from an unrecoverable
            # tree stays unpromotable however clean the repository becomes
            # afterwards. Reporting the current state instead would tell
            # someone to commit their changes and try again, which for this
            # experiment will never work -- the code that produced the result
            # is already gone. The remedy is a new experiment, and the message
            # has to say so.
            stored = self._record

            if stored["git_commit"] == provenance.UNAVAILABLE:
                cause = ("no commit was recorded when it was registered, so "
                         "there is no code version to reproduce from")
            elif stored["git_dirty"]:
                cause = ("it was registered from a working tree with "
                         "uncommitted changes (commit %s), so the code that "
                         "ran cannot be recovered from that commit"
                         % stored["git_commit"][:12])
            else:
                cause = "its provenance was recorded as not reproducible"

            current = provenance.reproducibility_report()

            raise NotReproducible(
                "cannot PROMOTE %s: %s. This cannot be repaired after the "
                "fact -- register a new experiment citing this one, from a "
                "recoverable tree. (The repository right now: %s.)"
                % (self.experiment_id, cause,
                   "reproducible" if current["reproducible"]
                   else current["reason"]))

    # ==============================
    # THE RECORD
    # ==============================

    def record(self):
        """Everything a third party needs to check this experiment.

        Deliberately one object rather than a set of accessors: the failure
        being designed against is a result reported without its provenance, and
        that becomes much easier when provenance takes an extra call.
        """

        results = self.stage_results()

        return {
            "experiment_id": self.experiment_id,
            "hypothesis": self._record["hypothesis"],
            "strategy_version": self._record["strategy_version"],
            "researcher": self._record["researcher"],
            "notes": self._record["notes"],
            "expected_failure": json.loads(self._record["expected_failure"]),
            "acceptance_criteria": json.loads(
                self._record["acceptance_criteria"]),
            "registered_at": self._record["registered_at"],
            "status": self._record["status"],
            "provenance": {
                "git_commit": self._record["git_commit"],
                "git_dirty": (None if self._record["git_dirty"] is None
                              else bool(self._record["git_dirty"])),
                "code_hash": self._record["code_hash"],
                "config_hash": self._record["config_hash"],
                "dataset_snapshot": self._record["dataset_snapshot"],
                "universe_snapshot": self._record["universe_snapshot"],
                "prereg_hash": self._record["prereg_hash"],
                "reproducible": bool(self._record["reproducible"]),
            },
            "preregistration": self.preregistration(),
            "stages": [
                {"stage": r["stage"], "stage_name": r["stage_name"],
                 "status": r["status"], "detail": r["detail"],
                 "metrics": json.loads(r["metrics"]),
                 "recorded_at": r["recorded_at"]}
                for r in results
            ],
            "decision": self.decision(),
        }

    def write_record(self, directory=None):
        """The record as a file beside the experiment's other artifacts."""

        directory = directory or os.path.join(
            os.path.dirname(store.DB_PATH), "backtest_output")

        os.makedirs(directory, exist_ok=True)

        path = os.path.join(directory, "%s_record.json" % self.experiment_id)

        with open(path, "w", encoding="utf-8") as handle:
            json.dump(self.record(), handle, ensure_ascii=False, indent=2,
                      default=str)

        return path


# ==============================
# PLATFORM-LEVEL REPORTING
# ==============================

def kpi(path=None):
    """The platform's only KPI: at which stage do bad ideas die.

    Not the number of experiments. A platform that runs a hundred experiments
    and rejects all of them at Stage 5 has burned a hundred weeks; one that runs
    ten and rejects seven at Stage 1 has done its job.
    """

    store.initialise(path)

    report = stages.cheap_rejection_rate(store.all_decisions(path=path))

    report["reproducibility"] = provenance.reproducibility_report()

    return report


def registry(path=None):
    """Every experiment, newest first -- the machine-readable registry."""

    store.initialise(path)

    conn = store.connect(path)

    try:
        rows = conn.execute(
            "SELECT e.experiment_id, e.hypothesis, e.status, e.registered_at, "
            "e.reproducible, d.decision, d.rejected_at_stage "
            "FROM experiments e LEFT JOIN decisions d "
            "ON e.experiment_id = d.experiment_id "
            "ORDER BY e.registered_at DESC").fetchall()
    finally:
        conn.close()

    return [dict(r) for r in rows]


def now():
    return pd.Timestamp.now().isoformat()
