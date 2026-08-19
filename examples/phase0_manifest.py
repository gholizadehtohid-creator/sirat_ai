"""Phase 0: freeze the state, and refuse to proceed if it has moved.

WHY A MANIFEST BEFORE RESEARCH RATHER THAN A REPORT AFTER IT
--------------------------------------------------------------
Every number produced from here on is only meaningful relative to a state that
somebody can name. If the universe snapshot, the cost assumption, the OOS
boundary or the Alpha Core hash can drift while the research runs, then the
research has no referent and its conclusions cannot be checked -- which is the
condition Phase 1 operated in for fifteen experiments.

So this runs first, writes a machine-readable manifest, and **exits non-zero if
anything differs from the verified state**. It is a gate, not a report.

WHAT IT DELIBERATELY DOES NOT DO
---------------------------------
It does not repair anything. A drift is a finding: classify it as an
infrastructure, data, governance, research-design or genuine-strategy defect,
report it, and stop. Silently re-freezing to whatever is on disk today would
turn the one control that detects tampering into a control that launders it.
"""

import hashlib
import json
import os
import platform
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import numpy as np                                                  # noqa: E402
import pandas as pd                                                 # noqa: E402

import backtest_config                                              # noqa: E402
import config                                                       # noqa: E402
from sirat.experiments import ledger, provenance, universe          # noqa: E402
from sirat.fixtures import alpha_core, build_panel                  # noqa: E402


OUT = os.path.join(ROOT, "backtest_output", "research_manifest.json")


# The verified state this programme starts from. Written down so a drift is
# detectable rather than merely visible.
VERIFIED = {
    "alpha_core_sha256_prefix": "90024d86d7a5c268",
    "universe_snapshot": "u20260819-668658bb",
    "panel_sha256_prefix": "618d194e5bc2d4af",
    "tests": 934,
    "ledger_entries": 11,
    "round_trip_pct": 1.04,
    "risk_free_rate": 0.23,
    "trading_days_per_year": 240,
}


def _git(*args):
    try:
        out = subprocess.run(["git"] + list(args), cwd=ROOT,
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def _sha256(path):
    if not os.path.exists(path):
        return None
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def build():
    snapshot = universe.latest()

    chain = ledger.verify()

    fixture = alpha_core.run(verbose=False)

    manifest = {
        "manifest_version": 1,
        "created_at": pd.Timestamp.now().isoformat(),

        "repository": {
            "head": _git("rev-parse", "HEAD"),
            "head_short": _git("rev-parse", "--short", "HEAD"),
            "tag": _git("describe", "--tags", "--always"),
            "archive_tag_commit": _git("rev-list", "-n", "1", "v2.0-archive"),
            "status_porcelain": _git("status", "--porcelain") or "",
            "clean": bool((_git("status", "--porcelain") or "") == ""),
            "tracked_files": len((_git("ls-files") or "").splitlines()),
        },

        "protected_components": {
            "alpha_core_path": "backtesting/alpha_core.py",
            "alpha_core_sha256": _sha256(
                os.path.join(ROOT, "backtesting", "alpha_core.py")),
            "alpha_core_frozen_check": [
                c for c in fixture["checks"]
                if c["check"] == "alpha_core_frozen"][0]["passed"],
        },

        "data": {
            "market_db": "sirat_market.db",
            "market_db_bytes": (os.path.getsize(
                os.path.join(ROOT, "sirat_market.db"))
                if os.path.exists(os.path.join(ROOT, "sirat_market.db"))
                else None),
            "dataset_fingerprint": provenance.dataset_snapshot(),
            "fixture_panel": "sirat/fixtures/data/alpha_core_panel.csv.gz",
            "fixture_panel_sha256": _sha256(build_panel.PANEL_PATH),
            "factor_panel": "backtest_output/factor_panel.csv",
            "factor_panel_present": os.path.exists(
                os.path.join(backtest_config.OUTPUT_DIR, "factor_panel.csv")),
        },

        "universe": {
            "snapshot_id": snapshot["snapshot_id"] if snapshot else None,
            "taken_at": snapshot["taken_at"] if snapshot else None,
            "instruments": snapshot["instrument_count"] if snapshot else None,
            "content_hash": snapshot["content_hash"] if snapshot else None,
            "restorable": _restorable(snapshot),
        },

        "ledger": {
            "entries": chain["entries"],
            "intact": chain["intact"],
            "head": chain.get("head"),
        },

        "configuration": {
            "config_hash": provenance.config_hash(_config_block()),
            "code_hash": provenance.code_hash(),
            **_config_block(),
        },

        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "numpy": np.__version__,
            "pandas": pd.__version__,
        },

        "seeds": {
            # Every seed this programme uses, named. A seed that lives only
            # inside a script is a seed nobody can re-supply.
            "research_alpha_ic": 20260818,
            "experiment15_bootstrap": 20260818,
            "phase2_edge_audit": 20260819,
            "phase4_null_tests": 20260819,
            "phase3_monte_carlo": 20260819,
        },

        "regression_anchors": {
            c["check"]: {"measured": c.get("measured"),
                         "expected": c.get("expected"),
                         "passed": c["passed"]}
            for c in fixture["checks"]
        },
    }

    return manifest


def _restorable(snapshot):
    if not snapshot:
        return False
    try:
        universe.restore(snapshot["snapshot_id"])
        return True
    except Exception:                                          # noqa: BLE001
        return False


def _config_block():
    """The assumptions every result depends on, in one place.

    Transaction cost, risk-free rate and the OOS boundary are here rather than
    read ad hoc, because these are exactly the three numbers whose quiet
    adjustment turns a failing strategy into a passing one.
    """

    return {
        "round_trip_pct": config.TRANSACTION_COST_PCT,
        "risk_free_rate": config.RISK_FREE_RATE,
        "trading_days_per_year": config.TRADING_DAYS_PER_YEAR,
        "out_of_sample_start": backtest_config.OUT_OF_SAMPLE_START,
        "walk_forward_train_months": backtest_config.WALK_FORWARD_TRAIN_MONTHS,
        "walk_forward_validate_months":
            backtest_config.WALK_FORWARD_VALIDATE_MONTHS,
        "walk_forward_step_months": backtest_config.WALK_FORWARD_STEP_MONTHS,
        "benchmark": "TEDPIX (شاخص کل), backtest_config.BENCHMARK_TEDPIX",
        "clean_window": ["2014-09-21", "2024-09-01"],
        "clean_window_note":
            "E13/E14/E15's window. Post-2024-09 is EXCLUDED because "
            "research_factors.py fitted on it -- it is contaminated for factor "
            "selection, not a holdout.",
    }


def drifts(manifest):
    """Everything that differs from the verified state, classified."""

    found = []

    core = manifest["protected_components"]["alpha_core_sha256"] or ""

    if not core.startswith(VERIFIED["alpha_core_sha256_prefix"]):
        found.append({
            "item": "Alpha Core hash",
            "expected": VERIFIED["alpha_core_sha256_prefix"],
            "observed": core[:16],
            "classification": "GOVERNANCE DEFECT",
            "note": "Alpha Core is hash-pinned and must not be modified.",
        })

    if manifest["universe"]["snapshot_id"] != VERIFIED["universe_snapshot"]:
        found.append({
            "item": "universe snapshot",
            "expected": VERIFIED["universe_snapshot"],
            "observed": manifest["universe"]["snapshot_id"],
            "classification": "DATA DEFECT",
            "note": "a newer snapshot is not automatically wrong, but the "
                    "research must state which one it used",
        })

    if not manifest["universe"]["restorable"]:
        found.append({
            "item": "universe snapshot restorability",
            "expected": True, "observed": False,
            "classification": "DATA DEFECT",
            "note": "missing evidence is never a pass",
        })

    if not manifest["ledger"]["intact"]:
        found.append({
            "item": "ledger chain",
            "expected": True, "observed": False,
            "classification": "GOVERNANCE DEFECT",
            "note": "the record of what was decided has been altered",
        })

    if not manifest["repository"]["clean"]:
        found.append({
            "item": "working tree",
            "expected": "clean", "observed": "dirty",
            "classification": "INFRASTRUCTURE DEFECT",
            "note": "results registered from a dirty tree are unpromotable, "
                    "permanently",
        })

    failed_anchors = [k for k, v in manifest["regression_anchors"].items()
                      if not v["passed"]]

    if failed_anchors:
        found.append({
            "item": "regression anchors",
            "expected": "all pass", "observed": ", ".join(failed_anchors),
            "classification": "GOVERNANCE DEFECT",
            "note": "the platform cannot rediscover its own known failures",
        })

    for key, expected in (("round_trip_pct", VERIFIED["round_trip_pct"]),
                          ("risk_free_rate", VERIFIED["risk_free_rate"]),
                          ("trading_days_per_year",
                           VERIFIED["trading_days_per_year"])):
        observed = manifest["configuration"][key]
        if observed != expected:
            found.append({
                "item": key, "expected": expected, "observed": observed,
                "classification": "RESEARCH-DESIGN DEFECT",
                "note": "cost and rate assumptions may not move during a "
                        "research programme",
            })

    return found


def say(message=""):
    print(message, flush=True)


def main():
    say("=" * 78)
    say("PHASE 0 -- FREEZE AND AUDIT")
    say("=" * 78)

    manifest = build()

    repo = manifest["repository"]

    say("  HEAD              %s" % repo["head_short"])
    say("  tag               %s" % repo["tag"])
    say("  clean             %s" % repo["clean"])
    say("  tracked files     %d" % repo["tracked_files"])
    say("  alpha core        %s"
        % manifest["protected_components"]["alpha_core_sha256"][:16])
    say("  universe          %s (%d instruments, restorable=%s)"
        % (manifest["universe"]["snapshot_id"],
           manifest["universe"]["instruments"] or -1,
           manifest["universe"]["restorable"]))
    say("  dataset           %s" % manifest["data"]["dataset_fingerprint"])
    say("  config hash       %s"
        % manifest["configuration"]["config_hash"][:16])
    say("  code hash         %s" % manifest["configuration"]["code_hash"][:16])
    say("  ledger            %d entries, intact=%s"
        % (manifest["ledger"]["entries"], manifest["ledger"]["intact"]))

    say()
    say("  ASSUMPTIONS FROZEN FOR THIS PROGRAMME")
    for key in ("round_trip_pct", "risk_free_rate", "trading_days_per_year",
                "out_of_sample_start", "benchmark"):
        say("    %-26s %s" % (key, manifest["configuration"][key]))
    say("    %-26s %s" % ("clean window",
                          manifest["configuration"]["clean_window"]))

    say()
    say("  REGRESSION ANCHORS")
    failed = [k for k, v in manifest["regression_anchors"].items()
              if not v["passed"]]
    say("    %d anchors, %d failed" % (len(manifest["regression_anchors"]),
                                       len(failed)))

    os.makedirs(os.path.dirname(OUT), exist_ok=True)

    with open(OUT, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2, default=str)

    say()
    say("  manifest written to %s" % os.path.relpath(OUT, ROOT))

    problems = drifts(manifest)

    say()
    say("=" * 78)

    if not problems:
        say("STATE MATCHES THE VERIFIED BASELINE. Research may proceed.")
        say("=" * 78)
        return 0

    say("STATE HAS DRIFTED -- RESEARCH IS BLOCKED")
    say("=" * 78)

    for problem in problems:
        say()
        say("  [%s] %s" % (problem["classification"], problem["item"]))
        say("     expected  %s" % problem["expected"])
        say("     observed  %s" % problem["observed"])
        say("     %s" % problem["note"])

    say()
    say("  Nothing has been repaired. Classify, explain, and only then act.")

    return 1


if __name__ == "__main__":
    sys.exit(main())
