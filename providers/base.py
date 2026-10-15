"""Provider interface.

The one contract that makes offline operation work: **no provider method ever
raises**. A dead network, an unsupported capability or a malformed response
all come back as a ProviderResult carrying an error string. Degradation is
decided in exactly one place (chain.py) instead of being re-invented at every
call site with a try/except.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone


# Canonical column set for a whole-market snapshot. Adapters must emit these
# names (missing ones as None) so downstream code never branches on source.
SNAPSHOT_COLUMNS = [
    "ins_code", "symbol_fa", "name_fa", "isin",
    "close", "last", "prev_close",
    "open", "high", "low",
    "volume", "value", "trades",
    "eps", "pe", "base_volume", "total_shares",
    "limit_up", "limit_down", "state",
]


# Canonical column set for an index series. Note what is NOT here: an index
# has no open and no volume -- the exchange publishes only the closing, low and
# high values of the day. Anything that wants a volume for an index has to say
# where it got one.
INDEX_COLUMNS = ["date", "close", "low", "high"]


# Canonical column set for client-type history (حقیقی / حقوقی).
#
# TSETMC splits every session's turnover by who was on each side: `individual`
# is a retail person (حقیقی), `institutional` is a legal entity (حقوقی) --
# funds, the company itself, market makers, state bodies. The count columns
# matter as much as the values: value tells you how much money moved, count
# tells you across how many participants, and the ratio between them is what
# separates one fund accumulating from a thousand retail buyers.
CLIENT_TYPE_COLUMNS = [
    "date",
    "individual_buy_value", "individual_sell_value",
    "institutional_buy_value", "institutional_sell_value",
    "individual_buy_volume", "individual_sell_volume",
    "institutional_buy_volume", "institutional_sell_volume",
    "individual_buy_count", "individual_sell_count",
    "institutional_buy_count", "institutional_sell_count",
]

# The INTRADAY whole-market flow snapshot, which is a different shape from the
# history above and must not be confused with it. Two differences, both of
# which bite silently:
#
#   NO VALUE COLUMNS. GetClientTypeAll publishes volumes and participant
#   counts only. Anything needing rials must take them from the per-symbol
#   history at end of day, or go without.
#
#   ONE ROW PER INSTRUMENT, not per date. It is the running total of the
#   session in progress, reset by the exchange at each session's start.
#
# قدرت خریدار survives the missing values. Measured over 57,649 stored rows,
# the per-capita ratio computed from volumes matches the one computed from
# values with a median relative difference of 0.0135% and a correlation of
# 0.99999999 -- because both legs of one instrument trade at nearly one price
# in one session, so price cancels in the ratio.
CLIENT_TYPE_LIVE_COLUMNS = [
    "ins_code",
    "individual_buy_volume", "individual_sell_volume",
    "institutional_buy_volume", "institutional_sell_volume",
    "individual_buy_count", "individual_sell_count",
    "institutional_buy_count", "institutional_sell_count",
]


# Capabilities an adapter may advertise.
CAP_UNIVERSE = "universe"
CAP_SNAPSHOT = "snapshot"
CAP_HISTORY = "history"
CAP_INFO = "instrument_info"
CAP_INDEX = "index"
CAP_CLIENT_TYPE = "client_type"

# Provenance labels surfaced in the UI.
LIVE = "live"
CACHE = "cache"
STALE = "stale-cache"
SEED = "seed"
UNAVAILABLE = "unavailable"


@dataclass
class ProviderResult:
    """Uniform return type. `ok` means the caller got usable data."""

    data: object = None
    source: str = ""
    provenance: str = UNAVAILABLE
    fetched_at: datetime = None
    error: str = None
    warnings: list = field(default_factory=list)

    @property
    def ok(self):
        return self.data is not None and self.error is None

    @classmethod
    def failure(cls, source, error, warnings=None):
        return cls(
            data=None,
            source=source,
            provenance=UNAVAILABLE,
            error=str(error),
            warnings=list(warnings or [])
        )

    @classmethod
    def success(cls, data, source, provenance=LIVE, warnings=None):
        return cls(
            data=data,
            source=source,
            provenance=provenance,
            fetched_at=datetime.now(timezone.utc),
            warnings=list(warnings or [])
        )

    def describe(self):
        return {
            "source": self.source,
            "provenance": self.provenance,
            "ok": self.ok,
            "error": self.error,
            "warnings": self.warnings,
            "fetched_at": (
                self.fetched_at.isoformat(timespec="seconds")
                if self.fetched_at else None
            ),
        }


class MarketDataProvider(ABC):
    """Base adapter. Subclasses override only what they can actually serve."""

    name = "base"
    capabilities = frozenset()

    def supports(self, capability):
        return capability in self.capabilities

    @abstractmethod
    def is_available(self):
        """Cheap liveness check. Must never raise."""

    def health(self):
        """Status row for the panel's provider table."""

        try:
            available = self.is_available()
            error = None
        except Exception as exc:                      # defensive: contract says never raise
            available = False
            error = f"{type(exc).__name__}: {exc}"

        return {
            "name": self.name,
            "available": available,
            "capabilities": sorted(self.capabilities),
            "error": error,
        }

    # ---- data methods: default to "not supported" -------------------------

    def _unsupported(self, capability):
        return ProviderResult.failure(
            self.name, f"{self.name} does not provide {capability}"
        )

    def get_universe(self):
        return self._unsupported(CAP_UNIVERSE)

    def get_market_snapshot(self):
        return self._unsupported(CAP_SNAPSHOT)

    def get_history(self, ins_code, count=300):
        return self._unsupported(CAP_HISTORY)

    def get_instrument_info(self, ins_code):
        return self._unsupported(CAP_INFO)

    def get_index_history(self, ins_code):
        return self._unsupported(CAP_INDEX)

    def get_client_type_history(self, ins_code):
        return self._unsupported(CAP_CLIENT_TYPE)

    def get_client_type_histories(self, ins_codes, progress=None):
        """Bulk client-type. Adapters with real concurrency override this."""

        out = {}

        for index, ins_code in enumerate(ins_codes):
            out[ins_code] = self.get_client_type_history(ins_code)
            if progress:
                progress(index + 1, len(ins_codes), ins_code)

        return out

    def get_histories(self, ins_codes, count=300, progress=None):
        """Bulk history. Adapters with real concurrency override this."""

        out = {}

        for index, ins_code in enumerate(ins_codes):
            out[ins_code] = self.get_history(ins_code, count)
            if progress:
                progress(index + 1, len(ins_codes), ins_code)

        return out

    def get_instrument_infos(self, ins_codes, progress=None):
        """Bulk instrument info. Adapters with real concurrency override this."""

        out = {}

        for index, ins_code in enumerate(ins_codes):
            out[ins_code] = self.get_instrument_info(ins_code)
            if progress:
                progress(index + 1, len(ins_codes), ins_code)

        return out
