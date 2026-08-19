"""The research governance layer.

    from sirat.experiments import Manager, gates, stages, universe

    manager = Manager.register(
        experiment_id="E16-liquidity-monitor",
        hypothesis="...",
        ...,
        preregistration={"parameters": {...}, "validation_gates": {...},
                         "expected_metrics": {...},
                         "rejection_thresholds": {...}, "seed": 20260818})

    manager.run_stage(stages.DATA_INTEGRITY,
                      gates.data_integrity(panel, manager.thresholds(), ...))

Nothing else in the platform is allowed to produce a reportable number. That is
the entire idea: Phase 1 had every component this needs and no rule that any of
them be used.

WHY THE PUBLIC SURFACE IS THIS SMALL
-------------------------------------
`store` is deliberately not exported. It is the append-only writer and it has no
business being reached for by research code -- everything a researcher legally
needs goes through `Manager`, which is where the refusals live. It remains
importable for the tests and the CLI, which is the honest limit of what Python
enforcement can be.
"""

from sirat.experiments.manager import (
    ARCHIVE,
    ExperimentClosed,
    GovernanceError,
    HOLDOUT,
    Manager,
    NoEvidence,
    NotReproducible,
    PROMOTE,
    PreregistrationIncomplete,
    PreregistrationTampered,
    REJECT,
    RegistrationIncomplete,
    SUPERSEDE,
    StageOutOfOrder,
    UnknownExperiment,
    ValidationMissing,
    WindowAlreadySpent,
    kpi,
    registry,
)
from sirat.experiments import gates, provenance, stages, universe

__all__ = [
    "Manager",
    "gates",
    "provenance",
    "stages",
    "universe",
    "kpi",
    "registry",
    "PROMOTE",
    "REJECT",
    "ARCHIVE",
    "SUPERSEDE",
    "HOLDOUT",
    "GovernanceError",
    "RegistrationIncomplete",
    "PreregistrationIncomplete",
    "PreregistrationTampered",
    "StageOutOfOrder",
    "ExperimentClosed",
    "NoEvidence",
    "ValidationMissing",
    "NotReproducible",
    "WindowAlreadySpent",
    "UnknownExperiment",
]
