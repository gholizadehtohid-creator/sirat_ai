"""Portfolio limits as an object: how many names, how large, how concentrated.

WHY A POLICY OBJECT WHEN portfolio.py ALREADY BUILDS BASKETS
------------------------------------------------------------
`portfolio.py` selects, sizes and finalises ten names in one call and is what the
advisor runs. It is not changed by this module. What it cannot do is answer a
question in isolation:

    "the book holds these seven positions -- may I add this eighth one, and how
     much of it?"

Every limit needed to answer that lives inside portfolio.build() as a local
variable, which means the only way to ask is to rebuild the whole basket. A swing
system generates signals one at a time, over days, against a book that already
exists, so the limits have to be addressable on their own. That is all a Policy
is: the same constants, in an object that can be asked questions and tightened
for a regime without mutating config.

THE LIMIT THAT IS ALWAYS MISSED
-------------------------------
Ten positions each allowed up to 15% of equity sum to 150%. The first version of
portfolio.py produced exactly that -- a basket worth 1.43x the account, with every
individual position inside its own limit. A per-position cap and a portfolio cap
are different constraints and satisfying one says nothing about the other, so
`capacity()` reports both and `admit()` checks both.

THE SECTOR CAP IS A CONSTRAINT, NOT A PREFERENCE
------------------------------------------------
Ten names chosen on score alone routinely land eight in one industry, and a basket
of eight petrochemicals is one position wearing eight names. `allocate()`
therefore fills one name per sector before any sector gets a second -- the same
two-pass rule portfolio.py uses, restated here so a caller allocating
incrementally gets the same shape as one allocating all at once.

NOTHING HERE READS A DATABASE OR PLACES AN ORDER.
"""

import math

import config


def _finite(value):
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _sector_of(position):
    name = (position or {}).get("sector_name") or (position or {}).get("sector")
    return (str(name).strip() or "?") if name else "?"


# Reasons a candidate is refused. Named constants so a caller can branch on them
# without matching on Persian text.
REFUSE_FULL = "portfolio_full"
REFUSE_SECTOR_COUNT = "sector_count"
REFUSE_SECTOR_VALUE = "sector_value"
REFUSE_CASH = "cash_floor"
REFUSE_TOO_SMALL = "below_min_position"
REFUSE_DUPLICATE = "already_held"
REFUSE_NO_SIZE = "no_size"


class Policy(object):
    """Every portfolio-level limit, in one place, immutable by convention.

    `replace()` returns a new Policy rather than mutating this one. A sweep that
    mutated a shared policy would change the limits of every run that came after
    it in the same process -- which is the bug BacktestConfig exists to avoid on
    the backtest side.
    """

    __slots__ = ("max_positions", "max_position_pct", "min_position_pct",
                 "max_per_sector", "max_sector_pct", "min_cash_pct",
                 "target_invested_pct", "max_adv_participation_pct")

    def __init__(self, max_positions=None, max_position_pct=None,
                 min_position_pct=None, max_per_sector=None,
                 max_sector_pct=None, min_cash_pct=None,
                 target_invested_pct=None, max_adv_participation_pct=None):

        self.max_positions = int(
            config.PORTFOLIO_SIZE if max_positions is None else max_positions)
        self.max_position_pct = float(
            config.MAX_POSITION_PCT if max_position_pct is None
            else max_position_pct)
        self.min_position_pct = float(
            config.MIN_POSITION_PCT if min_position_pct is None
            else min_position_pct)
        self.max_per_sector = int(
            config.MAX_PER_SECTOR if max_per_sector is None else max_per_sector)
        self.max_sector_pct = float(
            config.MAX_SECTOR_PCT if max_sector_pct is None else max_sector_pct)
        self.min_cash_pct = float(
            config.MIN_CASH_PCT if min_cash_pct is None else min_cash_pct)
        self.target_invested_pct = float(
            config.TARGET_INVESTED_PCT if target_invested_pct is None
            else target_invested_pct)
        self.max_adv_participation_pct = float(
            config.MAX_ADV_PARTICIPATION_PCT
            if max_adv_participation_pct is None else max_adv_participation_pct)

    def replace(self, **changes):

        current = {name: getattr(self, name) for name in self.__slots__}
        current.update(changes)

        return Policy(**current)

    def for_regime(self, assessment):
        """Tighten the policy for a market read. Never loosens it.

        The invested ceiling becomes the smaller of the policy's own target and
        what the regime permits, and nothing else moves. One-directional on
        purpose: a defect in this layer can only ever leave the book holding more
        cash than intended.
        """

        if not assessment:
            return self

        if not assessment.get("available"):
            # An undetectable market is not a neutral one. No new exposure.
            return self.replace(target_invested_pct=0.0, min_cash_pct=100.0)

        permitted = _finite(assessment.get("max_invested_pct"))

        if permitted is None:
            return self

        target = min(self.target_invested_pct, permitted)

        return self.replace(
            target_invested_pct=target,
            min_cash_pct=max(self.min_cash_pct, 100.0 - permitted),
        )

    def as_dict(self):
        return {name: getattr(self, name) for name in self.__slots__}

    def __repr__(self):
        return "Policy(positions=%d, position<=%.1f%%, sector<=%.1f%%, cash>=%.1f%%)" % (
            self.max_positions, self.max_position_pct, self.max_sector_pct,
            self.min_cash_pct)


def from_config():
    """The live policy. A function rather than a module constant so a change to
    config at runtime (a test, a sweep) is picked up rather than frozen at import."""

    return Policy()


# ==============================
# WHERE THE BOOK IS NOW
# ==============================

def state_of(positions=None, equity=None, cash=None):
    """Current exposure, by name and by sector.

    `cash` is optional: when the caller knows it, it is used; when not, it is
    inferred as equity minus holdings, which assumes the book has no leverage.
    That assumption is true of this system and is stated in the output so a
    caller for whom it is false can see which number was assumed.
    """

    equity = _finite(equity) or 0.0

    rows = list(positions or [])

    invested = sum(_finite(p.get("position_value")) or 0.0 for p in rows)

    by_sector = {}

    for position in rows:

        key = _sector_of(position)

        entry = by_sector.setdefault(key, {"count": 0, "value": 0.0})

        entry["count"] += 1
        entry["value"] += _finite(position.get("position_value")) or 0.0

    for entry in by_sector.values():
        entry["share_pct"] = (round(entry["value"] / equity * 100.0, 2)
                              if equity else None)

    inferred_cash = _finite(cash)

    if inferred_cash is None:
        inferred_cash = max(0.0, equity - invested)
        cash_source = "inferred (equity - holdings)"
    else:
        cash_source = "supplied"

    return {
        "equity": equity,
        "positions": len(rows),
        "invested_value": invested,
        "invested_pct": round(invested / equity * 100.0, 2) if equity else None,
        "cash": inferred_cash,
        "cash_pct": round(inferred_cash / equity * 100.0, 2) if equity else None,
        "cash_source": cash_source,
        "by_sector": by_sector,
        "held_codes": {str(p.get("ins_code")) for p in rows
                       if p.get("ins_code") is not None},
        "held_symbols": {p.get("symbol") for p in rows if p.get("symbol")},
    }


def capacity(policy, state):
    """What room is left, on every limit at once.

    `value_left` is the binding number and it is the MINIMUM of two independent
    ceilings -- the invested-percentage target and the cash floor -- not either
    one alone. They are not the same constraint: a 90% invested target and a 10%
    cash floor agree only when the book has no other claim on its cash.
    """

    equity = _finite(state.get("equity")) or 0.0

    names_left = max(0, policy.max_positions - int(state.get("positions") or 0))

    invested = _finite(state.get("invested_value")) or 0.0

    target_value = equity * (policy.target_invested_pct / 100.0)

    cash_floor_value = equity * (policy.min_cash_pct / 100.0)

    spendable_by_target = max(0.0, target_value - invested)
    spendable_by_cash = max(0.0, (_finite(state.get("cash")) or 0.0)
                            - cash_floor_value)

    value_left = min(spendable_by_target, spendable_by_cash)

    return {
        "names_left": names_left,
        "full": names_left <= 0,
        "value_left": value_left,
        "value_left_pct": (round(value_left / equity * 100.0, 2)
                           if equity else None),
        "limited_by": ("invested_target"
                       if spendable_by_target <= spendable_by_cash
                       else "cash_floor"),
        "position_ceiling": equity * (policy.max_position_pct / 100.0),
        "position_floor": equity * (policy.min_position_pct / 100.0),
        "sector_ceiling": equity * (policy.max_sector_pct / 100.0),
    }


def sector_room(policy, state, sector):
    """Names and rials one sector has left."""

    key = (str(sector).strip() or "?") if sector else "?"

    held = (state.get("by_sector") or {}).get(key) or {"count": 0, "value": 0.0}

    equity = _finite(state.get("equity")) or 0.0

    ceiling = equity * (policy.max_sector_pct / 100.0)

    return {
        "sector": key,
        "held_count": held["count"],
        "held_value": held["value"],
        "share_pct": held.get("share_pct"),
        "names_left": max(0, policy.max_per_sector - held["count"]),
        "value_left": max(0.0, ceiling - held["value"]),
        "full": held["count"] >= policy.max_per_sector,
    }


# ==============================
# ADMISSION
# ==============================

def admit(policy, state, candidate):
    """May this candidate be opened, and at what value? Returns a verdict dict.

    `allowed_value` is what survives every limit, which is often less than the
    candidate asked for -- and the caller must resize to it rather than treating
    the answer as a yes/no. A verdict that said only "yes" while the sector had
    room for a third of the position would be admitting a position that breaks
    the very limit that admitted it.
    """

    candidate = candidate or {}

    room = capacity(policy, state)

    code = candidate.get("ins_code")
    symbol = candidate.get("symbol")

    wanted = _finite(candidate.get("position_value")) or 0.0

    if ((code is not None and str(code) in state.get("held_codes", set()))
            or (symbol and symbol in state.get("held_symbols", set()))):
        return _refuse(REFUSE_DUPLICATE,
                       "این نماد از قبل در سبد است", room)

    if wanted <= 0:
        return _refuse(REFUSE_NO_SIZE,
                       "حجم پیشنهادی صفر است", room)

    if room["full"]:
        return _refuse(REFUSE_FULL,
                       "سبد پر است (%d از %d موقعیت)"
                       % (state.get("positions"), policy.max_positions), room)

    sector = sector_room(policy, state, candidate.get("sector")
                         or candidate.get("sector_name"))

    if sector["full"]:
        return _refuse(REFUSE_SECTOR_COUNT,
                       "صنعت %s از قبل %d نماد دارد (سقف %d)"
                       % (sector["sector"], sector["held_count"],
                          policy.max_per_sector), room, sector)

    allowed = min(wanted, room["position_ceiling"], room["value_left"],
                  sector["value_left"])

    if allowed <= 0:
        reason_key = (REFUSE_SECTOR_VALUE if sector["value_left"] <= 0
                      else REFUSE_CASH)
        return _refuse(reason_key,
                       ("سقف ارزشی صنعت %s پر است" % sector["sector"]
                        if reason_key == REFUSE_SECTOR_VALUE
                        else "نقدینگی آزاد به کف %.1f%% رسیده است"
                             % policy.min_cash_pct),
                       room, sector)

    if allowed < room["position_floor"]:
        return _refuse(REFUSE_TOO_SMALL,
                       "فضای باقی‌مانده %.2f%% سرمایه — زیر کف %.2f%%"
                       % (allowed / (state.get("equity") or 1) * 100.0,
                          policy.min_position_pct), room, sector)

    binding = "requested"

    if allowed < wanted:
        binding = min(
            (("position_cap", room["position_ceiling"]),
             ("portfolio_room", room["value_left"]),
             ("sector_value", sector["value_left"])),
            key=lambda pair: pair[1],
        )[0]

    return {
        "admitted": True,
        "reason_key": None,
        "reason": None,
        "requested_value": wanted,
        "allowed_value": allowed,
        "resized": allowed < wanted,
        "binding": binding,
        "capacity": room,
        "sector": sector,
    }


def _refuse(key, reason, room, sector=None):
    return {
        "admitted": False,
        "reason_key": key,
        "reason": reason,
        "requested_value": None,
        "allowed_value": 0.0,
        "resized": False,
        "binding": key,
        "capacity": room,
        "sector": sector,
    }


# ==============================
# ALLOCATION
# ==============================

def spread_ceiling(policy, room, candidates):
    """The most any ONE new position may take if the book is to fill its count.

    WHY THIS IS NEEDED, and it is not obvious until you watch the output.

    Position sizing is risk-based: risking 1% of equity against a 5.5% stop wants
    18% of equity, which the 15% per-position cap trims to 15%. Hand candidates
    to a greedy allocator with a 90% invested budget and the first six take
    15% each, the budget is gone, and a policy that says "up to ten positions"
    produces six -- every individual limit respected, and a book two thirds as
    diversified as it was configured to be. The concentration is invisible in the
    per-position numbers, which all look correct.

    So the budget is divided by however many names can actually be filled, and
    each position is capped at its share. Risk-based sizing still governs BELOW
    that ceiling: a name whose risk-based size is 4% stays at 4% rather than
    being inflated to the even share, so a volatile name still gets less money.
    The ceiling only ever cuts.

    Returns None when there is nothing to divide, and the caller then falls back
    to the per-position cap alone.
    """

    names = min(int(policy.max_positions) - 0, len(candidates or []))

    names = min(names, room.get("names_left") or 0)

    if names <= 0:
        return None

    value_left = _finite(room.get("value_left")) or 0.0

    if value_left <= 0:
        return None

    return value_left / float(names)


def allocate(policy, signals, equity, positions=None, cash=None, spread=True):
    """Fit a ranked list of candidates into the policy. Two passes, by sector.

    `signals` are trade_manager.plan() dicts in conviction order, best first.
    Returns the accepted list with `allocated_value` and `allocated_shares` set,
    the rejected list with a reason each, and the resulting book state.

    THE TWO PASSES ARE THE DIVERSIFICATION. First one name per sector, in rank
    order; then, if the book still has room, second names. Allocating strictly in
    rank order instead would fill the book from the strongest sector, which is
    how a ten-name basket becomes one bet -- and it happens quietly, because
    every individual pick was the best available at the time it was picked.

    `spread` divides the budget across the names that can be filled rather than
    letting the highest-ranked ones take the per-position cap until the money runs
    out. See spread_ceiling(). Pass False for the greedy behaviour -- which is the
    right choice when the intent is a concentrated book, and the wrong one by
    default.
    """

    state = state_of(positions=positions, equity=equity, cash=cash)

    ceiling = (spread_ceiling(policy, capacity(policy, state), signals)
               if spread else None)

    accepted = []
    rejected = []

    ranked = [s for s in (signals or []) if s]

    seen_sectors = set()

    # Supplied cash has to be SPENT as positions are added. Recomputing the
    # state with the original figure would leave the cash floor untouched no
    # matter how much was allocated, and every candidate after the first would
    # be judged against a book that still had all its money.
    opening_cash = _finite(cash)

    def rebuild():

        rows = [
            {
                "ins_code": a.get("ins_code"),
                "symbol": a.get("symbol"),
                "sector_name": a.get("sector") or a.get("sector_name"),
                "position_value": a["allocated_value"],
            }
            for a in accepted
        ] + list(positions or [])

        spent = sum(a["allocated_value"] for a in accepted)

        remaining = (None if opening_cash is None
                     else max(0.0, opening_cash - spent))

        state.clear()
        state.update(state_of(positions=rows, equity=equity, cash=remaining))

    # Candidates already tried and refused, by identity of the ORIGINAL dict.
    # Without this a name refused in pass 1 is refused again in pass 2 and the
    # rejection list double-counts it, which makes "how many candidates did the
    # caps throw away" -- the number the cash diagnostic rests on -- wrong.
    refused = set()

    def try_one(signal):

        request = signal

        if ceiling is not None:
            wanted = _finite(signal.get("position_value")) or 0.0
            if wanted > ceiling:
                # A shallow copy, so the caller's signal is not mutated. A
                # function that quietly rewrote the position size on the dict it
                # was handed would make a second allocate() call on the same
                # signals produce a different book.
                request = dict(signal)
                request["position_value"] = ceiling

        verdict = admit(policy, state, request)

        if not verdict["admitted"]:
            refused.add(id(signal))
            rejected.append({"signal": signal,
                             "reason": verdict["reason"],
                             "reason_key": verdict["reason_key"]})
            return False

        price = _finite(signal.get("entry_price"))

        if not price or price <= 0:
            refused.add(id(signal))
            rejected.append({"signal": signal,
                             "reason": "قیمت ورود مشخص نیست",
                             "reason_key": REFUSE_NO_SIZE})
            return False

        shares = int(math.floor(verdict["allowed_value"] / price))

        value = shares * price

        if shares <= 0 or value < verdict["capacity"]["position_floor"]:
            refused.add(id(signal))
            rejected.append({"signal": signal,
                             "reason": "پس از گرد کردن، موقعیت زیر کف می‌افتد",
                             "reason_key": REFUSE_TOO_SMALL})
            return False

        allocated = dict(signal)

        allocated["allocated_shares"] = shares
        allocated["allocated_value"] = round(value, 0)
        allocated["allocated_weight_pct"] = (round(value / equity * 100.0, 2)
                                             if equity else None)
        allocated["allocated_binding"] = verdict["binding"]

        # THE ALLOCATED NUMBERS BECOME THE CANONICAL ONES, and what the sizing
        # engine originally asked for is kept under `requested_*`.
        #
        # This is not tidiness. The risk engine sizes each candidate in isolation
        # and routinely wants the per-position cap; the allocator then cuts it to
        # fit the book. Leaving the requested figures in the fields a report reads
        # prints ten positions at "15% of equity" under a total of 90% -- three
        # numbers that cannot all be true, in the part of the output a user acts
        # on.
        allocated["requested_shares"] = signal.get("shares")
        allocated["requested_value"] = signal.get("position_value")
        allocated["requested_weight_pct"] = signal.get("weight_pct")

        allocated["shares"] = shares
        allocated["position_value"] = round(value, 0)
        allocated["weight_pct"] = allocated["allocated_weight_pct"]

        # The rials at risk follow the size that was actually taken. Recomputed
        # from the stop distance rather than scaled, so a rounded share count and
        # the risk it carries cannot drift apart.
        stop_pct = _finite(signal.get("stop_distance_pct"))

        if stop_pct is not None:
            risk_rials = shares * price * (stop_pct / 100.0)
            allocated["risk_rials"] = round(risk_rials, 0)
            allocated["risk_pct_of_equity"] = (
                round(risk_rials / equity * 100.0, 3) if equity else None)

        accepted.append(allocated)

        # The book moves as each position is added, so the next candidate is
        # judged against the book that will actually exist -- not against the
        # empty one. Recomputing state per admission is what keeps the sum of the
        # positions inside the portfolio cap.
        rebuild()

        return True

    def already_taken(signal):
        """Was this candidate accepted in pass 1?

        Matched on ins_code and symbol rather than on object identity: `accepted`
        holds copies, so `id()` never matches and an identity check would silently
        offer every pass-1 winner a second position.
        """

        code = signal.get("ins_code")
        symbol = signal.get("symbol")

        for row in accepted:
            if code is not None and str(code) == str(row.get("ins_code")):
                return True
            if symbol and symbol == row.get("symbol"):
                return True

        return False

    # ---- pass 1: one per sector ------------------------------------------
    for signal in ranked:

        sector = (signal.get("sector") or signal.get("sector_name") or "?")

        if sector in seen_sectors:
            continue

        if try_one(signal):
            seen_sectors.add(sector)

    # ---- pass 2: second names, still in rank order -----------------------
    for signal in ranked:

        if capacity(policy, state)["full"]:
            break

        if id(signal) in refused or already_taken(signal):
            continue

        try_one(signal)

    return {
        "accepted": accepted,
        "rejected": rejected,
        "state": state,
        "capacity": capacity(policy, state),
        "policy": policy.as_dict(),
        "spread": bool(spread),
        "spread_ceiling": ceiling,
    }


# ==============================
# REVIEW
# ==============================

def review(policy, positions=None, equity=None, cash=None):
    """Does the book as it stands break any limit? Returns the violations.

    Separate from admit() because a book can drift into a violation without any
    order being placed: one holding doubling while the others stay flat pushes a
    sector past its cap through price alone. A system that only checks limits at
    entry will hold that violation indefinitely without noticing.
    """

    state = state_of(positions=positions, equity=equity, cash=cash)

    violations = []

    equity = _finite(equity) or 0.0

    if state["positions"] > policy.max_positions:
        violations.append({
            "limit": "max_positions",
            "detail": "%d موقعیت در سبد، سقف %d"
                      % (state["positions"], policy.max_positions),
        })

    for position in positions or []:

        value = _finite(position.get("position_value")) or 0.0

        weight = (value / equity * 100.0) if equity else 0.0

        if weight > policy.max_position_pct:
            violations.append({
                "limit": "max_position_pct",
                "symbol": position.get("symbol"),
                "detail": "%s وزن %.2f%% دارد، سقف %.2f%%"
                          % (position.get("symbol") or "?", weight,
                             policy.max_position_pct),
            })

    for sector, entry in (state["by_sector"] or {}).items():

        if entry["count"] > policy.max_per_sector:
            violations.append({
                "limit": "max_per_sector",
                "sector": sector,
                "detail": "%s شامل %d نماد است، سقف %d"
                          % (sector, entry["count"], policy.max_per_sector),
            })

        share = entry.get("share_pct")

        if share is not None and share > policy.max_sector_pct:
            violations.append({
                "limit": "max_sector_pct",
                "sector": sector,
                "detail": "%s وزن %.2f%% دارد، سقف %.2f%%"
                          % (sector, share, policy.max_sector_pct),
            })

    cash_pct = state.get("cash_pct")

    if cash_pct is not None and cash_pct < policy.min_cash_pct:
        violations.append({
            "limit": "min_cash_pct",
            "detail": "نقد %.2f%% است، کف %.2f%%"
                      % (cash_pct, policy.min_cash_pct),
        })

    return {
        "compliant": not violations,
        "violations": violations,
        "state": state,
        "policy": policy.as_dict(),
    }
