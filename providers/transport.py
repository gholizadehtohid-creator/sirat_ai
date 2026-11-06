"""HTTP transport: sessions, retries, rate limiting, concurrency, probing.

Two details that matter for TSETMC specifically:

  * 403 is in the retry list. TSETMC returns 403 (not 429) when it sheds
    load, and every reference library omits it -- so their documented retry
    settings do nothing under exactly the condition retries exist for.
  * trust_env stays on, so HTTPS_PROXY/ALL_PROXY work with no code change,
    while an explicit SIRAT_PROXY_URL still overrides them.
"""

import socket
import ssl
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3 import exceptions as urllib3_exceptions

import settings

try:
    from urllib3.util.retry import Retry
except ImportError:                                   # very old urllib3
    Retry = None


# ==============================
# SOURCE-ADDRESS BINDING
# ==============================
#
# The mechanism that lets this app reach Iranian hosts while a TUN-mode VPN is
# running. Measured on the target machine:
#
#     cdn.tsetmc.com  bind=default (tunnel)   -> TLS refused
#     cdn.tsetmc.com  bind=192.168.0.106      -> TLS 1.3, HTTP 200
#     google.com      bind=192.168.0.106      -> timeout (ISP blocks it)
#
# So neither path alone works: the tunnel reaches the world but not Iran, the
# ISP reaches Iran but not the world. Binding per-connection uses the right
# one for each destination.

# Keyed by hostname, because different hosts genuinely need different routes.
# Measured on this machine with the tunnel up:
#
#     cdn.tsetmc.com  -> 192.168.1.14   (physical NIC; tunnel cannot reach it)
#     codal.ir        -> default route  (reachable through the tunnel)
#
# A single process-wide answer cannot serve both. Whichever host probed first
# would win, and the other's requests would be bound to an address that cannot
# reach it -- and worse, a re-probe triggered by one host would overwrite the
# other's working answer, so toggling a VPN fixed one API by breaking the
# other. Per-host is the only shape that is correct here.
_bind_cache = {}
_bind_lock = threading.Lock()

DEFAULT_HOST = "cdn.tsetmc.com"


def _host_key(host):
    return (host or urlparse(settings.TSETMC_BASE_URL).hostname
            or DEFAULT_HOST).strip().lower()


def local_addresses():
    """Every IPv4 address this machine holds, physical adapters first."""

    found = []

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = info[4][0]
            if address and address not in found and not address.startswith("127."):
                found.append(address)
    except Exception:
        pass

    # The address used to reach a LAN gateway is almost always the physical
    # NIC, even when a tunnel owns the default route. A UDP connect() assigns
    # a source address without sending anything, so this costs no traffic.
    # Several ranges are tried because the machine's own subnet is not known
    # in advance -- hardcoding one only ever works on one network.
    for gateway in ("192.168.0.1", "192.168.1.1", "10.0.0.1", "172.16.0.1",
                    "8.8.8.8"):
        try:
            probe_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            probe_sock.connect((gateway, 80))
            address = probe_sock.getsockname()[0]
            probe_sock.close()
            if address and not address.startswith("127.") and address not in found:
                found.insert(0, address)
        except Exception:
            continue

    def is_tunnel(address):
        return any(address.startswith(hint) for hint in settings.TUNNEL_IP_HINTS)

    # Physical addresses first; tunnel addresses last as a fallback.
    return sorted(found, key=is_tunnel)


def _reaches(host, port, source_ip, timeout=6.0):
    """Does a TLS handshake to `host` succeed from this source address?

    The TCP connect is not enough -- a tunnelled connection completes TCP and
    only fails at TLS, which is exactly the failure being diagnosed.
    """

    sock = None

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)

        if source_ip:
            sock.bind((source_ip, 0))

        sock.connect((host, port))

        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

        with context.wrap_socket(sock, server_hostname=host) as tls:
            tls.version()

        return True

    except Exception:
        return False

    finally:
        try:
            if sock is not None:
                sock.close()
        except Exception:
            pass


def discover_source_ip(host=None, port=443, force=False):
    """Find a local address that can actually reach `host`.

    Returns None when the default route already works (or nothing does), so
    the common case adds no binding at all. The answer is cached per host.
    """

    key = _host_key(host)

    if not force:
        with _bind_lock:
            if key in _bind_cache:
                return _bind_cache[key]

    setting = (settings.BIND_SOURCE_IP or "auto").strip().lower()

    def remember(value):
        with _bind_lock:
            _bind_cache[key] = value
        return value

    if setting == "off":
        return remember(None)

    if setting not in ("auto", ""):
        # An explicit address was configured; trust it for every host.
        return remember(settings.BIND_SOURCE_IP.strip())

    # If the default route already reaches the host, bind nothing.
    if _reaches(key, port, None):
        return remember(None)

    for address in local_addresses():
        if _reaches(key, port, address):
            return remember(address)

    return remember(None)


def reset_bind_cache(host=None):
    """Forget the cached route for one host, or for all of them."""

    with _bind_lock:
        if host is None:
            _bind_cache.clear()
        else:
            _bind_cache.pop(_host_key(host), None)


def bound_hosts():
    """What each host is currently bound to, for the diagnostics view."""

    with _bind_lock:
        return dict(_bind_cache)


def rebind(host=None):
    """Re-probe the route and report whether the answer changed.

    The bind address is discovered once and cached for the life of the
    process, which is right for a CLI run and wrong for a panel left open: the
    user toggles the VPN, the cached address stops working, and every request
    fails until a restart. Callers that see a total failure call this and try
    once more, so a route change costs one retry instead of a restart.
    """

    key = _host_key(host)

    with _bind_lock:
        previous = _bind_cache.get(key)

    reset_bind_cache(key)

    current = discover_source_ip(host=key, force=True)

    return {"host": key, "changed": current != previous,
            "was": previous, "now": current}


# Re-probing costs a handful of TLS handshakes, so it is worth doing when the
# route really has changed and wasteful when a single symbol happens to 404.
# One attempt per cooldown, PER HOST -- a shared cooldown meant a burst of
# TSETMC failures silently ate Codal's one chance to re-probe, so the API that
# was actually broken never got its retry.
REBIND_COOLDOWN_SECONDS = 60.0

_rebind_state = {}
_rebind_lock = threading.Lock()


def is_connection_error(exc):
    """Is this a route-level failure rather than the server saying no?

    An HTTP 404 or 403 means we reached the host and it answered; re-probing
    the network for that would be pointless churn during a bulk fetch where a
    few instruments legitimately fail. A refused TLS handshake or a connection
    timeout is the signature of the route itself having moved.

    HTTPError is rejected **first and explicitly**, because requests builds its
    whole exception tree on IOError -- `RequestException(IOError)` -- and
    IOError is OSError. So the broad OSError arm below, which is there to catch
    raw socket failures, silently swallowed every HTTP status error too: a 404
    on one symbol during a bulk fetch counted as "the network moved" and
    triggered a full re-probe. The behaviour contradicted this docstring for as
    long as both existed, and nothing failed loudly enough to show it.
    """

    if isinstance(exc, requests.exceptions.HTTPError):
        return False

    if isinstance(exc, (requests.exceptions.ConnectionError,
                        requests.exceptions.Timeout,
                        requests.exceptions.SSLError,
                        socket.timeout, ssl.SSLError, OSError)):
        return True

    return False


def maybe_rebind(host=None):
    """Re-probe one host's route, at most once per cooldown. True if changed."""

    key = _host_key(host)

    with _rebind_lock:

        now = time.monotonic()

        if now - _rebind_state.get(key, 0.0) < REBIND_COOLDOWN_SECONDS:
            return False

        _rebind_state[key] = now

    try:
        return rebind(host=key)["changed"]
    except Exception:
        return False


class RequestDeadlineExceeded(requests.exceptions.ReadTimeout):
    """The whole request ran past settings.REQUEST_DEADLINE."""


class DeadlineSession(requests.Session):
    """A Session whose every non-streamed request has a wall-clock ceiling.

    requests' `timeout` bounds each socket wait, not the response: a body
    arriving a few bytes at a time never trips it. So the body is read here
    in chunks against a deadline counted from the start of the request. The
    phase before the body (connect, retries, Retry-After) is bounded by the
    retry policy -- see CappedRetry. A caller that asks for stream=True gets
    the plain behaviour and owns its own reading.
    """

    deadline = None                     # seconds; None -> settings value

    def request(self, method, url, *args, **kwargs):
        if kwargs.get("stream") or len(args) > 10:
            return super().request(method, url, *args, **kwargs)

        limit = self.deadline or settings.REQUEST_DEADLINE
        started = time.monotonic()
        kwargs["stream"] = True
        response = super().request(method, url, *args, **kwargs)

        # read1, not iter_content: iter_content's read(n) blocks until n
        # bytes or the end, so a trickle could hold one chunk past any
        # deadline. read1 returns what has arrived, so the check below runs
        # at least once per socket read (each bounded by the read timeout).
        raw = response.raw
        chunks = []
        try:
            while True:
                try:
                    chunk = raw.read1(64 * 1024, decode_content=True)
                except urllib3_exceptions.ProtocolError as exc:
                    raise requests.exceptions.ChunkedEncodingError(exc) from exc
                except urllib3_exceptions.DecodeError as exc:
                    raise requests.exceptions.ContentDecodingError(exc) from exc
                except urllib3_exceptions.ReadTimeoutError as exc:
                    raise requests.exceptions.ConnectionError(exc) from exc
                except urllib3_exceptions.SSLError as exc:
                    raise requests.exceptions.SSLError(exc) from exc
                if not chunk:
                    break
                chunks.append(chunk)
                if time.monotonic() - started > limit:
                    raise RequestDeadlineExceeded(
                        "%s took over %.0fs in all (deadline)" % (url, limit))
        except Exception:
            response.close()
            raise

        response._content = b"".join(chunks)
        response._content_consumed = True
        raw.release_conn()
        return response


if Retry is not None:

    class CappedRetry(Retry):
        """Retry that never sleeps longer than settings.RETRY_AFTER_CAP.

        urllib3 obeys a server's Retry-After verbatim before each retry, with
        no upper bound, inside a call that no read timeout can interrupt.
        """

        def get_retry_after(self, response):
            value = super().get_retry_after(response)
            if value is None:
                return None
            return min(value, settings.RETRY_AFTER_CAP)

else:
    CappedRetry = None


class SourceBoundAdapter(HTTPAdapter):
    """HTTPAdapter that forces every connection out of one local address."""

    def __init__(self, source_ip, **kwargs):
        self._source_address = (source_ip, 0)
        super().__init__(**kwargs)

    def init_poolmanager(self, *args, **kwargs):
        kwargs["source_address"] = self._source_address
        return super().init_poolmanager(*args, **kwargs)

    def proxy_manager_for(self, *args, **kwargs):
        kwargs.setdefault("source_address", self._source_address)
        return super().proxy_manager_for(*args, **kwargs)


def build_session(headers=None, bind_source=True, host=None):
    """A configured requests.Session honouring proxy/TLS/retry settings.

    `host` selects which host's route this session is bound for. It matters
    because the answer differs per host on this machine -- TSETMC needs the
    physical NIC while Codal is reachable through the tunnel -- and a session
    built without it silently inherits TSETMC's binding. Callers that talk to
    something else pass their own host.
    """

    session = DeadlineSession()

    session.headers.update({
        "User-Agent": settings.USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://main.tsetmc.com/",
    })

    if headers:
        session.headers.update(headers)

    if settings.proxies():
        session.proxies.update(settings.proxies())
    elif bind_source:
        # A VPN's system proxy would put us back inside the tunnel, defeating
        # the whole point of binding to the physical adapter.
        session.trust_env = False

    session.verify = settings.VERIFY_TLS

    if Retry is not None:
        retry = CappedRetry(
            total=settings.RETRIES,
            connect=settings.RETRIES,
            read=settings.RETRIES,
            backoff_factor=0.4,
            status_forcelist=settings.RETRY_STATUS,
            allowed_methods=frozenset(["GET"]),
            respect_retry_after_header=True,
            raise_on_status=False,
        )
        adapter = HTTPAdapter(
            max_retries=retry,
            pool_connections=settings.MAX_WORKERS,
            pool_maxsize=settings.MAX_WORKERS,
        )
    else:
        adapter = HTTPAdapter(
            pool_connections=settings.MAX_WORKERS,
            pool_maxsize=settings.MAX_WORKERS,
        )

    source_ip = (discover_source_ip(host=host)
                 if (bind_source and not settings.proxies()) else None)

    if source_ip:
        retries = getattr(adapter, "max_retries", None)
        adapter = SourceBoundAdapter(
            source_ip,
            max_retries=retries,
            pool_connections=settings.MAX_WORKERS,
            pool_maxsize=settings.MAX_WORKERS,
        )

    session.mount("https://", adapter)
    session.mount("http://", adapter)

    return session


class RateLimiter:
    """Minimum spacing between requests, shared across worker threads."""

    def __init__(self, min_interval):
        self.min_interval = max(0.0, float(min_interval or 0))
        self._lock = threading.Lock()
        self._next_at = 0.0

    def wait(self):
        if self.min_interval <= 0:
            return

        with self._lock:
            now = time.monotonic()
            sleep_for = self._next_at - now
            if sleep_for < 0:
                sleep_for = 0.0
            self._next_at = now + sleep_for + self.min_interval

        if sleep_for:
            time.sleep(sleep_for)


def map_concurrent(fn, items, max_workers=None, limiter=None, progress=None):
    """Run fn over items concurrently. Exceptions are captured, not raised."""

    items = list(items)

    if not items:
        return {}

    workers = max(1, min(max_workers or settings.MAX_WORKERS, len(items)))

    results = {}
    done = 0

    def wrapped(item):
        if limiter:
            limiter.wait()
        return fn(item)

    with ThreadPoolExecutor(max_workers=workers) as pool:

        futures = {pool.submit(wrapped, item): item for item in items}

        for future in as_completed(futures):
            item = futures[future]
            try:
                results[item] = future.result()
            except Exception as exc:
                results[item] = exc

            done += 1
            if progress:
                progress(done, len(items), item)

    return results


def probe(url, session=None, timeout=6.0):
    """Diagnose one endpoint: DNS, TCP timing, then the HTTP request.

    TCP timing is reported because it is diagnostic here. A host that
    completes a TCP handshake in under ~5ms but then fails TLS is not a
    remote host refusing you -- it is something local answering on its
    behalf and cutting the TLS handshake by SNI.
    """

    from urllib.parse import urlparse

    parsed = urlparse(url)
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    report = {
        "url": url,
        "host": host,
        "dns": None,
        "tcp_ms": None,
        "status": None,
        "error": None,
        "note": None,
    }

    try:
        report["dns"] = socket.gethostbyname(host)
    except Exception as exc:
        report["error"] = f"DNS failed: {type(exc).__name__}"
        return report

    try:
        start = time.perf_counter()
        sock = socket.create_connection((host, port), timeout=timeout)
        report["tcp_ms"] = round((time.perf_counter() - start) * 1000, 2)
        sock.close()
    except Exception as exc:
        report["error"] = f"TCP failed: {type(exc).__name__}"
        return report

    close_after = session is None
    session = session or build_session()

    try:
        response = session.get(url, timeout=timeout)
        report["status"] = response.status_code
        if response.status_code >= 400:
            report["error"] = f"HTTP {response.status_code}"
    except Exception as exc:
        message = str(exc)
        if "UNEXPECTED_EOF" in message or "SSL" in type(exc).__name__:
            report["error"] = "TLS handshake refused"
            report["note"] = (
                "TCP connects but TLS is cut mid-handshake — the signature of "
                "an SNI filter, not of a server refusing you. Set "
                "SIRAT_PROXY_URL to a proxy outside this network."
            )
            if report["tcp_ms"] is not None and report["tcp_ms"] < 5:
                report["note"] += (
                    f" (TCP answered in {report['tcp_ms']}ms — far too fast to "
                    "be a real round trip, so the interception is local.)"
                )
        else:
            report["error"] = f"{type(exc).__name__}: {message[:120]}"
    finally:
        if close_after:
            session.close()

    return report


if __name__ == "__main__":

    targets = [
        f"{settings.TSETMC_BASE_URL}/api/ClosingPrice/"
        f"GetClosingPriceDailyList/46348559193224090/5",
        settings.UNIVERSE_URL,
    ]

    print(f"{'HOST':<32}{'DNS':<17}{'TCP ms':>8}  {'HTTP':<6} NOTE")
    print("-" * 100)

    for target in targets:
        row = probe(target)
        print(
            f"{row['host']:<32}{str(row['dns'] or '-'):<17}"
            f"{str(row['tcp_ms'] or '-'):>8}  {str(row['status'] or '-'):<6} "
            f"{row['error'] or 'OK'}"
        )
        if row["note"]:
            print(f"    -> {row['note']}")
