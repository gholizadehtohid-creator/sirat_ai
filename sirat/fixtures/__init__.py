"""Regression fixtures: the platform must keep rediscovering its own negatives.

A governance platform that cannot re-derive a known failure has no standing to
judge a new idea. `alpha_core` is that test, and it is adversarial by
construction: it fails when Alpha Core looks WORSE than recorded and it fails
just as hard when Alpha Core looks BETTER, because a rejected strategy that
starts passing has almost always been rescued by a defect rather than a fix.
"""

from sirat.fixtures import alpha_core

__all__ = ["alpha_core"]
