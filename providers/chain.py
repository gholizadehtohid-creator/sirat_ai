"""Chain provider: try adapters in order, degrade to the offline one.

All fallback logic lives here. Call sites just ask for data and read the
provenance off the result.
"""

from providers.base import MarketDataProvider, ProviderResult


class ChainProvider(MarketDataProvider):

    name = "auto"

    def __init__(self, providers):
        self.providers = [p for p in providers if p is not None]

    @property
    def capabilities(self):
        caps = set()
        for provider in self.providers:
            caps |= set(provider.capabilities)
        return frozenset(caps)

    def is_available(self):
        return any(p.is_available() for p in self.providers)

    def health(self):
        return {
            "name": self.name,
            "available": self.is_available(),
            "capabilities": sorted(self.capabilities),
            "error": None,
            "members": [p.health() for p in self.providers],
        }

    def _try(self, capability, call):
        """Run `call` against each capable provider until one succeeds."""

        warnings = []
        attempted = False

        for provider in self.providers:

            if not provider.supports(capability):
                continue

            attempted = True

            try:
                result = call(provider)
            except Exception as exc:                  # a provider broke its contract
                warnings.append(f"{provider.name}: {type(exc).__name__}: {exc}")
                continue

            if result.ok:
                result.warnings = warnings + list(result.warnings)
                return result

            warnings.append(f"{provider.name}: {result.error}")

        if not attempted:
            return ProviderResult.failure(
                self.name, f"no configured provider offers {capability}", warnings
            )

        return ProviderResult.failure(
            self.name, f"all providers failed for {capability}", warnings
        )

    def get_universe(self):
        return self._try("universe", lambda p: p.get_universe())

    def get_market_snapshot(self):
        return self._try("snapshot", lambda p: p.get_market_snapshot())

    def get_history(self, ins_code, count=300):
        return self._try("history", lambda p: p.get_history(ins_code, count))

    def get_instrument_info(self, ins_code):
        return self._try("instrument_info", lambda p: p.get_instrument_info(ins_code))

    def get_index_history(self, ins_code):
        return self._try("index", lambda p: p.get_index_history(ins_code))

    def get_client_type_history(self, ins_code):
        return self._try(
            "client_type", lambda p: p.get_client_type_history(ins_code)
        )

    def get_client_type_histories(self, ins_codes, progress=None):
        """Bulk client-type from the first capable provider, gaps to the next."""

        remaining = list(ins_codes)
        collected = {}

        for provider in self.providers:

            if not remaining or not provider.supports("client_type"):
                continue

            try:
                results = provider.get_client_type_histories(
                    remaining, progress=progress
                )
            except Exception:                          # contract says never raise
                continue

            for ins_code, result in results.items():
                if result is not None and result.ok:
                    collected[ins_code] = result

            remaining = [c for c in remaining if c not in collected]

        for ins_code in remaining:
            collected[ins_code] = ProviderResult.failure(
                self.name, f"no provider returned client-type data for {ins_code}"
            )

        return collected

    def get_instrument_infos(self, ins_codes, progress=None):
        """Bulk info from the first capable provider, gaps left to the next."""

        remaining = list(ins_codes)
        collected = {}

        for provider in self.providers:

            if not remaining or not provider.supports("instrument_info"):
                continue

            try:
                batch = provider.get_instrument_infos(remaining, progress=progress)
            except Exception:
                continue

            still_missing = []

            for ins_code in remaining:
                result = batch.get(ins_code)
                if result is not None and result.ok:
                    collected[ins_code] = result
                else:
                    still_missing.append(ins_code)

            remaining = still_missing

        for ins_code in remaining:
            collected[ins_code] = ProviderResult.failure(
                self.name, "no provider returned instrument info"
            )

        return collected

    def get_histories(self, ins_codes, count=300, progress=None):
        """Bulk fetch from the first live provider, filling gaps from the rest.

        A partial success is the normal case: the network provider returns
        most symbols and the offline one covers whatever it missed.
        """

        remaining = list(ins_codes)
        collected = {}

        for provider in self.providers:

            if not remaining or not provider.supports("history"):
                continue

            try:
                batch = provider.get_histories(remaining, count, progress=progress)
            except Exception:
                continue

            still_missing = []

            for ins_code in remaining:
                result = batch.get(ins_code)
                if result is not None and result.ok:
                    collected[ins_code] = result
                else:
                    still_missing.append(ins_code)

            remaining = still_missing

        for ins_code in remaining:
            collected[ins_code] = ProviderResult.failure(
                self.name, "no provider returned history"
            )

        return collected
