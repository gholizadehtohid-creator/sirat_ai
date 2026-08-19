"""TSETMC CDN adapter (cdn.tsetmc.com JSON API).

Endpoint field mappings are carried over verbatim from the original working
tsetmc_data.py -- that mapping is the highest-trust thing here because it was
written against real responses.

The instrument-info and market-snapshot parsers are written against field
names attested by multiple independent client libraries, but could not be
executed from this machine (the host is filtered locally). They are defensive:
every field is read through a helper that returns None rather than raising, so
a schema drift degrades one column instead of killing a scan.
"""

import threading
from urllib.parse import urlparse

import pandas as pd

import settings
from providers.base import (
    CAP_CLIENT_TYPE,
    CAP_HISTORY,
    CAP_INDEX,
    CAP_INFO,
    CAP_SNAPSHOT,
    CLIENT_TYPE_COLUMNS,
    INDEX_COLUMNS,
    SNAPSHOT_COLUMNS,
    MarketDataProvider,
    ProviderResult,
)
from providers.transport import (
    RateLimiter,
    build_session,
    is_connection_error,
    map_concurrent,
    maybe_rebind,
)


def _num(value):
    """Tolerant numeric read: bad or missing -> None, never an exception."""

    if value is None:
        return None

    try:
        result = float(value)
    except (TypeError, ValueError):
        return None

    if result != result:                              # NaN
        return None

    return result


def _dig(payload, *path):
    """Walk nested dicts safely."""

    node = payload

    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)

    return node


def _repair_untraded_bars(df):
    """Give no-trade sessions a coherent OHLC instead of zeros.

    The daily feed emits a row for every session an instrument was *listed*,
    not every session it traded. When it was halted -- suspended, or frozen
    pending an announcement -- the row carries the previous close in pClosing
    and leaves priceFirst/Max/Min at literally 0.

    Those zeros are not prices, and they are poison downstream: a backtest
    reading `low` for the worst excursion of a trade sees 0 and reports a
    -100% drawdown; the 52-week range collapses to the whole distance from
    zero. The standard convention for a session with no trades is
    open = high = low = close, which is what this writes. Volume stays 0, so a
    no-trade session is still identifiable -- nothing is invented, the missing
    figures are just filled with the only price that session actually had.
    """

    if df.empty:
        return df

    untraded = (
        df["close"].gt(0)
        & df["open"].fillna(0).le(0)
        & df["high"].fillna(0).le(0)
        & df["low"].fillna(0).le(0)
    )

    if untraded.any():
        for column in ("open", "high", "low"):
            df.loc[untraded, column] = df.loc[untraded, "close"]

    return df


class TsetmcCdnProvider(MarketDataProvider):

    name = "tsetmc-cdn"
    capabilities = frozenset({
        CAP_CLIENT_TYPE, CAP_HISTORY, CAP_INDEX, CAP_INFO, CAP_SNAPSHOT
    })

    def __init__(self, session=None):
        self.base_url = settings.TSETMC_BASE_URL.rstrip("/")
        self.session = session or build_session()
        self.limiter = RateLimiter(settings.RATE_LIMIT_DELAY)
        self._available = None
        self._session_lock = threading.Lock()

    # ---- requests ---------------------------------------------------------

    def _get(self, url, timeout=None):
        """GET, re-probing the route once if the network moved underneath us.

        The provider is a cached singleton holding a session bound to one
        local address. That address is correct until the user toggles their
        VPN, at which point every request fails and stays failing for the life
        of the process. Rather than making the user restart the panel, a
        route-level failure triggers one re-probe and one retry on a rebuilt
        session. Server-side refusals do not qualify -- see is_connection_error.
        """

        timeout = timeout or settings.timeout()

        try:
            response = self.session.get(url, timeout=timeout)
            response.raise_for_status()
            return response
        except Exception as exc:
            if not is_connection_error(exc):
                raise
            first_error = exc

        if not maybe_rebind(urlparse(self.base_url).hostname):
            raise first_error

        with self._session_lock:
            self.session = build_session()
            self._available = None

        response = self.session.get(url, timeout=timeout)
        response.raise_for_status()
        return response

    # ---- liveness ---------------------------------------------------------

    def is_available(self):
        """One cheap request, cached for the life of the provider."""

        if self._available is not None:
            return self._available

        try:
            response = self.session.get(
                f"{self.base_url}/api/StaticData/GetTime",
                timeout=(settings.TIMEOUT_CONNECT, 8.0)
            )
            self._available = response.status_code < 500
        except Exception:
            self._available = False

        return self._available

    # ---- daily history ----------------------------------------------------

    def get_history(self, ins_code, count=300):

        url = (
            f"{self.base_url}/api/ClosingPrice/"
            f"GetClosingPriceDailyList/{ins_code}/{count}"
        )

        try:
            records = self._get(url).json().get("closingPriceDaily", [])
        except Exception as exc:
            return ProviderResult.failure(
                self.name, f"{type(exc).__name__}: {str(exc)[:160]}"
            )

        if not records:
            return ProviderResult.failure(
                self.name, f"no history returned for {ins_code}"
            )

        rows = [
            {
                "date": str(item.get("dEven")),
                "open": item.get("priceFirst"),
                "high": item.get("priceMax"),
                "low": item.get("priceMin"),
                "close": item.get("pClosing"),
                "volume": item.get("qTotTran5J"),
                "value": item.get("qTotCap"),
                "trades": item.get("zTotTran"),
            }
            for item in records
        ]

        df = pd.DataFrame(rows)

        df["date"] = pd.to_datetime(df["date"], format="%Y%m%d", errors="coerce")

        for column in ["open", "high", "low", "close", "volume", "value", "trades"]:
            df[column] = pd.to_numeric(df[column], errors="coerce")

        df = df.dropna(subset=["date", "close"])

        if df.empty:
            return ProviderResult.failure(
                self.name, f"history for {ins_code} parsed to zero usable rows"
            )

        df = df.sort_values("date").reset_index(drop=True)

        df = _repair_untraded_bars(df)

        return ProviderResult.success(df, self.name)

    def get_histories(self, ins_codes, count=300, progress=None):
        """Concurrent bulk fetch, rate-limited across threads."""

        def one(ins_code):
            return self.get_history(ins_code, count)

        raw = map_concurrent(
            one,
            ins_codes,
            max_workers=settings.MAX_WORKERS,
            limiter=self.limiter,
            progress=progress,
        )

        return {
            code: (
                result if isinstance(result, ProviderResult)
                else ProviderResult.failure(self.name, str(result))
            )
            for code, result in raw.items()
        }

    # ---- index history ----------------------------------------------------

    def get_index_history(self, ins_code):
        """Full published history of one index (شاخص).

        Indices live on a different endpoint from shares and carry a different
        shape: no open, no volume, and the whole series in one response with no
        count parameter. Field names verified against live responses for
        شاخص کل (4,259 sessions from 2008-12-04) and شاخص کل هم‌وزن (2,980
        sessions from its 10,000 base on 2014-03-19):

            xNivInuClMresIbs   closing value
            xNivInuPbMresIbs   lowest value of the day
            xNivInuPhMresIbs   highest value of the day
        """

        url = f"{self.base_url}/api/Index/GetIndexB2History/{ins_code}"

        try:
            records = self._get(
                url, timeout=(settings.TIMEOUT_CONNECT, 45.0)
            ).json().get("indexB2", [])
        except Exception as exc:
            return ProviderResult.failure(
                self.name, f"{type(exc).__name__}: {str(exc)[:160]}"
            )

        if not records:
            return ProviderResult.failure(
                self.name, f"no index history returned for {ins_code}"
            )

        df = pd.DataFrame([
            {
                "date": str(item.get("dEven")),
                "close": item.get("xNivInuClMresIbs"),
                "low": item.get("xNivInuPbMresIbs"),
                "high": item.get("xNivInuPhMresIbs"),
            }
            for item in records
        ])

        df["date"] = pd.to_datetime(df["date"], format="%Y%m%d", errors="coerce")

        for column in ("close", "low", "high"):
            df[column] = pd.to_numeric(df[column], errors="coerce")

        df = df.dropna(subset=["date", "close"])
        df = df[df["close"] > 0]

        if df.empty:
            return ProviderResult.failure(
                self.name, f"index {ins_code} parsed to zero usable rows"
            )

        df = df.sort_values("date").reset_index(drop=True)

        return ProviderResult.success(df[INDEX_COLUMNS], self.name)

    # ---- client type (حقیقی / حقوقی) --------------------------------------

    def get_client_type_history(self, ins_code):
        """Daily split of turnover between retail and institutional money.

        The single most Tehran-specific signal available, and one nothing in
        this project consumed before. TSETMC reports, for every session, how
        much each side bought and sold and across how many participants.

        Field names use the exchange's own abbreviations. `I` is individual
        (حقیقی), `N` is non-individual (حقوقی):

            buy_I_Value / buy_N_Value      value bought by each side
            sell_I_Value / sell_N_Value    value sold by each side
            buy_I_Volume / buy_N_Volume    shares bought
            buy_I_Count / buy_N_Count      how many distinct buyers
            sell_I_Count / sell_N_Count    how many distinct sellers

        The count fields follow `<side>_<who>_Count`, matching the volume and
        value fields -- not the `buy_CountI` shape they were first written as.
        Getting that wrong is silent: the value columns parse, the counts all
        become NaN, قدرت خریدار reports "unavailable" for every symbol in the
        market, and the score carries on using the two components that are
        left. Check a raw record before changing any of these.

        The counts are what stop this being a coin flip. One institution buying
        what four thousand retail holders are selling reads very differently
        from four thousand retail buyers absorbing one institution's exit, and
        the value columns alone cannot tell those apart.
        """

        url = f"{self.base_url}/api/ClientType/GetClientTypeHistory/{ins_code}"

        try:
            records = self._get(url).json().get("clientType", [])
        except Exception as exc:
            return ProviderResult.failure(
                self.name, f"{type(exc).__name__}: {str(exc)[:160]}"
            )

        if not records:
            return ProviderResult.failure(
                self.name, f"no client-type history returned for {ins_code}"
            )

        rows = [
            {
                "date": str(item.get("recDate")),
                "individual_buy_value": item.get("buy_I_Value"),
                "individual_sell_value": item.get("sell_I_Value"),
                "institutional_buy_value": item.get("buy_N_Value"),
                "institutional_sell_value": item.get("sell_N_Value"),
                "individual_buy_volume": item.get("buy_I_Volume"),
                "individual_sell_volume": item.get("sell_I_Volume"),
                "institutional_buy_volume": item.get("buy_N_Volume"),
                "institutional_sell_volume": item.get("sell_N_Volume"),
                "individual_buy_count": item.get("buy_I_Count"),
                "individual_sell_count": item.get("sell_I_Count"),
                "institutional_buy_count": item.get("buy_N_Count"),
                "institutional_sell_count": item.get("sell_N_Count"),
            }
            for item in records
        ]

        df = pd.DataFrame(rows)

        df["date"] = pd.to_datetime(df["date"], format="%Y%m%d", errors="coerce")

        for column in CLIENT_TYPE_COLUMNS[1:]:
            df[column] = pd.to_numeric(df[column], errors="coerce")

        df = df.dropna(subset=["date"])

        # A session where nobody traded carries zeros on both sides. Keeping it
        # would drag every flow average toward zero and read as "balanced",
        # which is the opposite of "no information".
        traded = (
            df["individual_buy_value"].fillna(0)
            + df["institutional_buy_value"].fillna(0)
        )

        df = df[traded > 0]

        if df.empty:
            return ProviderResult.failure(
                self.name,
                f"client-type history for {ins_code} parsed to zero traded rows"
            )

        df = df.sort_values("date").reset_index(drop=True)

        # If the exchange ever renames the count fields, every value column
        # still parses and every count column silently becomes NaN. Downstream
        # that reads as "قدرت خریدار unavailable for this symbol" -- a
        # plausible per-symbol gap rather than a broken adapter, which is how
        # the first version of this method shipped with all four count names
        # wrong. Warn where the counts are wholly absent.
        warnings = []

        if not df["individual_buy_count"].notna().any():
            warnings.append(
                f"no participant counts in the response for {ins_code}; "
                "قدرت خریدار cannot be computed (field names may have changed)"
            )

        return ProviderResult.success(
            df[CLIENT_TYPE_COLUMNS], self.name, warnings=warnings
        )

    def get_client_type_histories(self, ins_codes, progress=None):
        """Concurrent bulk fetch, rate-limited across threads."""

        raw = map_concurrent(
            self.get_client_type_history,
            ins_codes,
            max_workers=settings.MAX_WORKERS,
            limiter=self.limiter,
            progress=progress,
        )

        return {
            code: (
                result if isinstance(result, ProviderResult)
                else ProviderResult.failure(self.name, str(result))
            )
            for code, result in raw.items()
        }

    # ---- per-instrument fundamentals -------------------------------------

    def get_instrument_info(self, ins_code):
        """EPS, sector P/E, shares outstanding, free float, base volume."""

        url = f"{self.base_url}/api/Instrument/GetInstrumentInfo/{ins_code}"

        try:
            payload = self._get(url).json()
        except Exception as exc:
            return ProviderResult.failure(
                self.name, f"{type(exc).__name__}: {str(exc)[:160]}"
            )

        info = payload.get("instrumentInfo") or payload

        # Verified against a live FOLAD response. Note epsValue is often null
        # while estimatedEPS carries a string -- neither is TTM earnings, so
        # the field keeps the word "estimated" all the way downstream.
        record = {
            "ins_code": str(ins_code),
            "eps": _num(_dig(info, "eps", "epsValue")),
            "eps_estimated": _num(_dig(info, "eps", "estimatedEPS")),
            "sector_pe": _num(_dig(info, "eps", "sectorPE")),
            # psr is sales-per-share on this feed, not a ratio.
            "sales_per_share": _num(_dig(info, "eps", "psr")),
            "total_shares": _num(info.get("zTitad")),
            "base_volume": _num(info.get("baseVol")),
            "free_float_pct": _num(info.get("kAjCapValCpsIdx")),
            "sector_code": (_dig(info, "sector", "cSecVal") or "").strip() or None,
            "sector_name": (_dig(info, "sector", "lSecVal") or "").strip() or None,
            "week52_high": _num(info.get("maxWeek")),
            "week52_low": _num(info.get("minWeek")),
            "year_high": _num(info.get("maxYear")),
            "year_low": _num(info.get("minYear")),
            "month_avg_volume": _num(info.get("qTotTran5JAvg")),
            "isin": (info.get("cIsin") or info.get("instrumentID") or "").strip() or None,
            "symbol_fa": (info.get("lVal18AFC") or "").strip() or None,
            "name_fa": (info.get("lVal30") or "").strip() or None,
            "board": (info.get("cgrValCotTitle") or "").strip() or None,
            "market": (info.get("flowTitle") or "").strip() or None,
            "yval": (info.get("yVal") or "").strip() or None,
        }

        return ProviderResult.success(record, self.name)

    def get_instrument_infos(self, ins_codes, progress=None):
        """Concurrent bulk info fetch, rate-limited across threads."""

        raw = map_concurrent(
            self.get_instrument_info,
            ins_codes,
            max_workers=settings.MAX_WORKERS,
            limiter=self.limiter,
            progress=progress,
        )

        return {
            code: (
                result if isinstance(result, ProviderResult)
                else ProviderResult.failure(self.name, str(result))
            )
            for code, result in raw.items()
        }

    # ---- whole-market snapshot -------------------------------------------

    def get_market_snapshot(self):
        """One call covering every instrument, for the screening stage."""

        url = (
            f"{self.base_url}/api/ClosingPrice/GetMarketWatch"
            "?market=0&paperTypes[0]=1&showTraded=false&withBestLimits=false"
        )

        try:
            payload = self._get(
                url, timeout=(settings.TIMEOUT_CONNECT, 45.0)).json()
        except Exception as exc:
            return ProviderResult.failure(
                self.name, f"{type(exc).__name__}: {str(exc)[:160]}"
            )

        records = (
            payload.get("marketwatch")
            or payload.get("marketWatch")
            or payload.get("closingPriceWatch")
            or []
        )

        if not records:
            return ProviderResult.failure(
                self.name, "market snapshot returned no rows"
            )

        # Field names verified against a live response (983 instruments).
        # Two traps confirmed there:
        #   * pClosing is 0.0 in this feed -- the usable close is `pcl`.
        #   * qtc is traded VALUE in rials, not volume. Volume is qtj /
        #     qTotTran5J. Swapping them silently inflates every liquidity
        #     figure by roughly the share price.
        rows = []

        for item in records:
            rows.append({
                "ins_code": str(item.get("insCode") or ""),
                "symbol_fa": (item.get("lva") or "").strip(),
                "name_fa": (item.get("lvc") or "").strip(),
                "isin": (item.get("insID") or "").strip() or None,
                "close": _num(item.get("pcl")),
                "last": _num(item.get("pdv") or item.get("pDrCotVal")),
                "prev_close": _num(item.get("py") or item.get("yClose")),
                "open": _num(item.get("pf")),
                "high": _num(item.get("pmx")),
                "low": _num(item.get("pmn")),
                "volume": _num(item.get("qTotTran5J") or item.get("qtj")),
                "value": _num(item.get("qTotCap") or item.get("qtc")),
                "trades": _num(item.get("zTotTran") or item.get("ztt")),
                "eps": _num(item.get("eps")),
                # `pe` arrives as a string like "5.9"; _num tolerates that.
                "pe": _num(item.get("pe")),
                "base_volume": _num(item.get("bv")),
                "total_shares": _num(item.get("ztd")),
                "limit_up": _num(item.get("pMax")),
                "limit_down": _num(item.get("pMin")),
                "state": (item.get("cs") or None),
            })

        df = pd.DataFrame(rows)

        for column in SNAPSHOT_COLUMNS:
            if column not in df.columns:
                df[column] = None

        df = df[SNAPSHOT_COLUMNS]

        df = df[df["ins_code"].astype(str).str.isdigit()]

        if df.empty:
            return ProviderResult.failure(
                self.name, "market snapshot parsed to zero usable rows"
            )

        return ProviderResult.success(df.reset_index(drop=True), self.name)
