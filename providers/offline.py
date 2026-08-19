"""Offline adapter: serves whatever the local database already holds.

This is the provider that must always work. It is last in every chain and is
the reason a dead network degrades the panel instead of breaking it. It never
reaches the network and never raises.
"""

import settings
from providers.base import (
    CAP_HISTORY,
    CAP_SNAPSHOT,
    CAP_UNIVERSE,
    SEED,
    MarketDataProvider,
    ProviderResult,
)


class OfflineProvider(MarketDataProvider):

    name = "offline"
    capabilities = frozenset({CAP_UNIVERSE, CAP_HISTORY, CAP_SNAPSHOT})

    def is_available(self):
        return True

    def get_universe(self):

        import database

        try:
            rows = database.load_instruments()
        except Exception as exc:
            return ProviderResult.failure(self.name, f"{type(exc).__name__}: {exc}")

        if not rows:
            return ProviderResult.failure(
                self.name,
                "no instruments stored yet -- run: python seed_import.py --universe"
            )

        return ProviderResult.success(rows, self.name, provenance=SEED)

    def get_history(self, ins_code, count=300):

        import database

        try:
            df = database.load_history_by_inscode(ins_code, limit=count)
        except Exception as exc:
            return ProviderResult.failure(self.name, f"{type(exc).__name__}: {exc}")

        if df is None or df.empty:
            return ProviderResult.failure(
                self.name, f"no cached bars for insCode {ins_code}"
            )

        return ProviderResult.success(df, self.name, provenance=SEED)

    def get_market_snapshot(self):

        import database

        try:
            df = database.load_snapshot()
        except Exception as exc:
            return ProviderResult.failure(self.name, f"{type(exc).__name__}: {exc}")

        if df is None or df.empty:
            return ProviderResult.failure(
                self.name,
                "no market snapshot stored -- the fundamental screen needs one "
                "(fetch it with a working provider, or import a seed bundle)"
            )

        return ProviderResult.success(df, self.name, provenance=SEED)
