"""SIRAT-AI -- a quantitative research governance platform.

The distinction this package exists to make: a backtester answers "what would
this have returned", a governance platform answers "is this answer allowed to
count". Phase 1 had an excellent backtester and no answer to the second
question, and it cost fifteen experiments to find that out.

    sirat.experiments   registration, preregistration, six mandatory stages,
                        append-only provenance, decisions
    sirat.fixtures      the adversarial regression suite -- the platform must
                        keep rediscovering its own known negatives or the build
                        is red

Everything that computes a number still lives where it always did:
`backtesting/`, `factor_engine.py`, `portfolio_engine.py`. This package does not
reimplement any of it. It decides when it runs and whether the result stands.
"""

__all__ = ["experiments", "fixtures"]
