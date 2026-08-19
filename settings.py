"""Runtime settings, driven by environment variables.

This is the ONLY module that reads os.environ. Everything that varies per
machine -- which provider to use, proxy, TLS verification, timeouts,
concurrency -- lives here so the data layer never hardcodes a host.

Why this matters on this machine: cdn.tsetmc.com, old.tsetmc.com, codal.ir and
fipiran.ir all completed a TCP handshake in 1.6-2.7ms and then had their TLS
handshake killed ("UNEXPECTED_EOF_WHILE_READING"). The sub-3ms handshake was
read at first as a local middlebox intercepting by SNI. It was not. A VPN in
TUN mode answers every connection locally, which is where the 2ms came from --
google.com measured the same. The tunnel's exit is abroad, and Iranian hosts
geo-block foreign IPs, so the connection died at the Iranian end while
everything non-Iranian worked fine through the same tunnel.

That is why BIND_SOURCE_IP below, not SIRAT_PROXY_URL, is the setting that
actually fixes this: no proxy configured inside the process can escape a TUN
adapter that owns the default route, but binding the socket to the physical
NIC's address sends that one connection over the local ISP instead.

Set these in the shell, or in run.bat, before starting the panel:

    set SIRAT_BIND_SOURCE_IP=auto
    set SIRAT_PROVIDER=auto
"""

import os


def _str(name, default):
    value = os.environ.get(name)
    return value.strip() if value and value.strip() else default


def _int(name, default):
    try:
        return int(os.environ.get(name, "").strip())
    except (TypeError, ValueError):
        return default


def _float(name, default):
    try:
        return float(os.environ.get(name, "").strip())
    except (TypeError, ValueError):
        return default


def _bool(name, default):
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


# ==============================
# PROVIDER SELECTION
# ==============================

# auto | cdn | offline    ("auto" tries the chain below, then offline)
PROVIDER = _str("SIRAT_PROVIDER", "auto")

PROVIDER_CHAIN = [
    p.strip()
    for p in _str("SIRAT_PROVIDER_CHAIN", "cdn,offline").split(",")
    if p.strip()
]


# ==============================
# ENDPOINTS
# ==============================

TSETMC_BASE_URL = _str("SIRAT_TSETMC_BASE_URL", "https://cdn.tsetmc.com")

TSETMC_LEGACY_BASE_URL = _str(
    "SIRAT_TSETMC_LEGACY_BASE_URL", "http://old.tsetmc.com"
)

# The universe list is mirrored on GitHub, which -- unlike every tsetmc host --
# is reachable from this machine. This is what makes a full-market universe
# possible with no proxy at all.
UNIVERSE_URL = _str(
    "SIRAT_UNIVERSE_URL",
    "https://raw.githubusercontent.com/Glyphack/pytse-client/master/"
    "pytse_client/data/symbols_name.json"
)


# ==============================
# TRANSPORT
# ==============================

# http://host:port, or socks5://host:port (socks needs: pip install pysocks)
PROXY_URL = _str("SIRAT_PROXY_URL", "")

# Source-address binding -- the setting that makes Iranian hosts reachable
# while a VPN is running.
#
# A TUN-mode VPN (V2Ray/sing-box/Happ) owns the default route, so every
# connection is tunnelled and exits abroad. Iranian sites geo-block foreign
# exits, so tsetmc fails even though the machine's own ISP could reach it
# perfectly well. Binding the socket to the PHYSICAL adapter's address routes
# that one connection outside the tunnel, over the local ISP, where Iranian
# hosts are reachable -- while everything else keeps using the VPN.
#
#   auto  discover a local address that reaches tsetmc and use it (default)
#   off   never bind; use the default route
#   <ip>  bind to this address explicitly, e.g. 192.168.0.106
BIND_SOURCE_IP = _str("SIRAT_BIND_SOURCE_IP", "auto")

# Interface prefixes that are virtual/tunnel adapters rather than real NICs.
TUNNEL_IP_HINTS = ("172.16.", "172.17.", "172.18.", "172.19.", "172.2", "172.3",
                   "10.", "198.18.", "169.254.")

VERIFY_TLS = _bool("SIRAT_VERIFY_TLS", True)

TIMEOUT_CONNECT = _float("SIRAT_TIMEOUT_CONNECT", 6.0)

TIMEOUT_READ = _float("SIRAT_TIMEOUT_READ", 20.0)

RETRIES = _int("SIRAT_RETRIES", 3)

# TSETMC returns 403 under load, not just 429 -- both must be retried.
RETRY_STATUS = (403, 429, 500, 502, 503, 504)

MAX_WORKERS = _int("SIRAT_MAX_WORKERS", 8)

# Seconds between requests, shared across worker threads.
RATE_LIMIT_DELAY = _float("SIRAT_RATE_LIMIT_DELAY", 0.3)

USER_AGENT = _str(
    "SIRAT_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


# ==============================
# SCAN SCALE
# ==============================

# Fundamental screening runs over the whole universe (cheap, one snapshot).
# Only this many survivors get per-symbol history fetched (expensive).
#
# This cap is a selection bias, not just a speed limit, and the size matters
# more than it looks. Survivors are truncated by FUNDAMENTAL score, so a share
# with ordinary fundamentals and an excellent technical setup is never scored
# at all -- it is cut before the technical stage runs. At 60 out of ~420
# survivors, the portfolio was choosing from the cheapest seventh of the
# eligible market and calling it the best of the market. 300 covers the
# realistic survivor count on a full snapshot; history is already cached by
# the fundamental stage, so the extra cost is indicator computation, not I/O.
MAX_TECHNICAL_SYMBOLS = _int("SIRAT_MAX_TECHNICAL", 300)

# Guard against a runaway full-market scan in the web request path.
FULL_SCAN_ENABLED = _bool("SIRAT_FULL_SCAN", True)


def proxies():
    """requests-style proxies dict, or None to use the environment's own."""

    if not PROXY_URL:
        return None

    return {"http": PROXY_URL, "https": PROXY_URL}


def timeout():
    return (TIMEOUT_CONNECT, TIMEOUT_READ)


def describe():
    """Snapshot of the effective settings, for the panel's health view."""

    return {
        "provider": PROVIDER,
        "provider_chain": PROVIDER_CHAIN,
        "tsetmc_base_url": TSETMC_BASE_URL,
        "universe_url": UNIVERSE_URL,
        "proxy_url": PROXY_URL or None,
        "verify_tls": VERIFY_TLS,
        "timeout_connect": TIMEOUT_CONNECT,
        "timeout_read": TIMEOUT_READ,
        "retries": RETRIES,
        "max_workers": MAX_WORKERS,
        "rate_limit_delay": RATE_LIMIT_DELAY,
        "max_technical_symbols": MAX_TECHNICAL_SYMBOLS,
    }
