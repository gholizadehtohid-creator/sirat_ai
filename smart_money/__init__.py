"""Smart Money Accumulation Scanner -- names being collected before the move.

WHAT IT LOOKS FOR
-----------------
One rare conjunction, not a list of independently interesting facts:

    money is flowing IN (MFI above 70) while the price is still DOWN
    (at the floor of its own multi-month range, near a tested support).

Either half alone is ordinary. On the session this module was written, 306 of
606 liquid names had MFI above 70 -- half the market -- and 38 sat in the
bottom third of their 120-session range. Eight had both. That eight is the
entire output of this scanner, and the reason it is worth running: a name whose
money-flow index is overbought while its price is not is a name where buying is
being absorbed without the price being paid up for. That is what accumulation
looks like from outside the order book.

Everything else here is corroboration of that one conjunction:

    who is buying     حقوقی selling into حقیقی hands (aggregate transfer)
    how concentrated  the average individual ticket against its own baseline
    at what price     P/E against the sector's own median, not the market's
    at what size      small and mid caps weighted up, large caps kept but muted
    on what volume    against the name's 20-session average

THE ONE THING THIS SCANNER REFUSES TO DO
-----------------------------------------
Count the same fact twice. Every rial bought was sold by somebody, so net
حقیقی flow IS net حقوقی flow with the sign flipped -- moneyflow.py says this
in its own docstring and it is the trap this scoring model is built around.
"فروش حقوقی" and "خرید حقیقی" are ONE observation, scored ONCE, in the
20-point transfer component. The separate 15-point کد به کد component scores
something genuinely independent of it: not how much moved, but how FEW hands it
moved into. Values and counts are different columns and different evidence.

WHAT THIS IS NOT
----------------
Not a validated edge, and nothing here should be read as one. This programme's
own research rejected the closest claims to it: E19 found no tradable edge in
the technical stack net of cost, E21 found حقیقی/حقوقی flow predicts queue-locks
(tradability) rather than returns, and E24 formally rejected the flow signal's
sign after a one-session decay. A scanner assembled from those ingredients
inherits their verdict. What it produces is a WATCHLIST -- a short, explicit,
reproducible reason to look at eight names instead of six hundred.

That is also why every scan is written to `smart_money_signals` with the
timestamp it was computed at. A watchlist nobody scores afterwards stays an
opinion forever; a watchlist logged before its outcomes exist can eventually be
counted. The log is the honest path from "this looks interesting" to "this was
measured", and it is the only path this module offers.

PUBLIC API
----------
    scan(top=20)              whole-market scan -> ranked candidates + funnel
    ensure_schema()           create the signal log's table (lazy, idempotent)
    recent_signals(limit)     what past scans wrote, for evaluation
"""

from .scanner import (
    CAVEAT,
    WEIGHTS,
    scan,
)
from .store import (
    ensure_schema,
    recent_signals,
    signal_history,
)

__all__ = [
    "CAVEAT",
    "WEIGHTS",
    "scan",
    "ensure_schema",
    "recent_signals",
    "signal_history",
]
