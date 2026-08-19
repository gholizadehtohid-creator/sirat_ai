"""Provider registry.

    from providers import get_provider
    provider = get_provider()          # honours SIRAT_PROVIDER
    result = provider.get_history("46348559193224090")
    if result.ok:
        ...                            # result.provenance says where it came from
"""

import settings
from providers.base import (  # noqa: F401  (re-exported for callers)
    CACHE,
    CAP_HISTORY,
    CAP_INFO,
    CAP_SNAPSHOT,
    CAP_UNIVERSE,
    LIVE,
    SEED,
    SNAPSHOT_COLUMNS,
    STALE,
    UNAVAILABLE,
    MarketDataProvider,
    ProviderResult,
)
from providers.chain import ChainProvider
from providers.offline import OfflineProvider
from providers.tsetmc_cdn import TsetmcCdnProvider

_SINGLE = {
    "cdn": TsetmcCdnProvider,
    "tsetmc": TsetmcCdnProvider,
    "offline": OfflineProvider,
}

_cached = {}


def build_provider(name=None, fresh=False):
    """Build a provider by name. 'auto' assembles the configured chain."""

    name = (name or settings.PROVIDER or "auto").strip().lower()

    if not fresh and name in _cached:
        return _cached[name]

    if name in ("auto", "chain"):
        members = []
        for member in settings.PROVIDER_CHAIN:
            factory = _SINGLE.get(member.strip().lower())
            if factory:
                members.append(factory())
        # The offline provider is the floor: without it a dead network is fatal.
        if not any(isinstance(m, OfflineProvider) for m in members):
            members.append(OfflineProvider())
        provider = ChainProvider(members)

    else:
        factory = _SINGLE.get(name)
        if factory is None:
            raise ValueError(
                f"Unknown provider '{name}'. Known: {', '.join(sorted(_SINGLE))}, auto"
            )
        provider = factory()

    _cached[name] = provider

    return provider


def get_provider(name=None):
    return build_provider(name)


def reset():
    """Drop cached providers so new settings take effect."""

    _cached.clear()


def available_providers():
    """Health rows for every known provider, for the panel."""

    rows = []

    for name in ("cdn", "offline"):
        try:
            rows.append(_SINGLE[name]().health())
        except Exception as exc:
            rows.append({
                "name": name, "available": False,
                "capabilities": [], "error": f"{type(exc).__name__}: {exc}"
            })

    return rows
