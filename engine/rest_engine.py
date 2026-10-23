"""
engine/rest_engine.py — Lazy on-demand REST confirmation engine.

Queries Binance Futures REST endpoints for Open Interest, Funding Rates,
and Premium Index ONLY when local microstructure triggers fire.

Strict Constraints:
- Zero NumPy / Pandas.
- Rate Limit Guard: Tracks 'X-MBX-USED-WEIGHT-1M' header. If used weight > 1800,
  returns neutral fallback (0.0, 0.0, 0.0) without issuing HTTP calls until
  the minute resets.
- Caching: All fetched results cached for 180 seconds in self.oi_cache.
- Uses aiohttp for async HTTP queries.
"""

import time
from typing import Any

import aiohttp
import orjson

# ─────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────

OPEN_INTEREST_HIST_URL: str = (
    "https://fapi.binance.com/futures/data/openInterestHist"
)
PREMIUM_INDEX_URL: str = (
    "https://fapi.binance.com/fapi/v1/premiumIndex"
)

MAX_USED_WEIGHT_1M: int = 1800           # 1800 out of 2400 shared limit
CACHE_TTL_SECONDS: float = 180.0         # 180-second TTL
WEIGHT_HEADER: str = "X-MBX-USED-WEIGHT-1M"


# ─────────────────────────────────────────────────────────────────────
# Lazy REST Confirmation Engine
# ─────────────────────────────────────────────────────────────────────

class LazyRestConfirmedEngine:
    """On-demand REST validation engine with caching and rate limit defense.

    Attributes
    ----------
    oi_cache : dict[str, tuple[float, float, float, float]]
        Stores `{symbol: (timestamp, delta_oi_pct, funding_rate, basis_bps)}`.
    next_funding_times : dict[str, int]
        Stores `{symbol: next_funding_time_ms}`.
    current_weight : int
        Current 1-minute used weight reported by Binance.
    weight_reset_time : float
        Timestamp when the current 1-minute rate-limit window expires.
    """

    __slots__ = (
        "_session",
        "_own_session",
        "oi_cache",
        "next_funding_times",
        "current_weight",
        "weight_reset_time",
    )

    def __init__(self, session: aiohttp.ClientSession | None = None) -> None:
        self._session: aiohttp.ClientSession | None = session
        self._own_session: bool = session is None
        self.oi_cache: dict[str, tuple[float, float, float, float]] = {}
        self.next_funding_times: dict[str, int] = {}
        self.current_weight: int = 0
        self.weight_reset_time: float = 0.0

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or (isinstance(self._session.closed, bool) and self._session.closed):
            self._session = aiohttp.ClientSession()
            self._own_session = True
        return self._session

    async def close(self) -> None:
        """Close internally managed aiohttp session."""
        if self._own_session and self._session is not None and not self._session.closed:
            await self._session.close()

    def _update_weight_from_headers(self, headers: Any, now_ts: float) -> None:
        """Parse Binance used-weight header and track minute reset time."""
        raw_weight = headers.get(WEIGHT_HEADER) or headers.get(WEIGHT_HEADER.lower())
        if raw_weight is not None:
            try:
                self.current_weight = int(raw_weight)
            except (ValueError, TypeError):
                pass

        if now_ts >= self.weight_reset_time:
            self.weight_reset_time = now_ts + 60.0

    def is_rate_limited(self, now_ts: float) -> bool:
        """Return True if used weight exceeds 1800 and minute has not reset."""
        if now_ts >= self.weight_reset_time:
            # New minute window: reset weight counter
            self.current_weight = 0
            return False
        return self.current_weight > MAX_USED_WEIGHT_1M

    async def get_derivatives_data(
        self,
        symbol: str,
        now_ts: float | None = None,
    ) -> tuple[float, float, float]:
        """Fetch or return cached (delta_oi_pct, funding_rate, basis_bps).

        Returns
        -------
        tuple[float, float, float]
            (delta_oi_pct, funding_rate, basis_bps)
            Returns (0.0, 0.0, 0.0) fallback if rate limit is exceeded.
        """
        now = time.time() if now_ts is None else now_ts

        # 1. Check 180-second Cache
        cached = self.oi_cache.get(symbol)
        if cached is not None:
            cached_ts, delta_oi_pct, funding_rate, basis_bps = cached
            if now - cached_ts < CACHE_TTL_SECONDS:
                return delta_oi_pct, funding_rate, basis_bps

        # 2. Check Rate Limit Guard (used_weight_1m > 1800)
        if self.is_rate_limited(now):
            return 0.0, 0.0, 0.0

        session = await self._get_session()

        # 3. Query Open Interest History (period=5m, limit=4)
        oi_params = {"symbol": symbol, "period": "5m", "limit": "4"}
        delta_oi_pct = 0.0
        try:
            async with session.get(OPEN_INTEREST_HIST_URL, params=oi_params) as resp:
                self._update_weight_from_headers(resp.headers, now)
                if resp.status == 200:
                    raw_body = await resp.read()
                    oi_data = orjson.loads(raw_body)
                    if isinstance(oi_data, list) and len(oi_data) >= 4:
                        oi_3_periods_ago = float(oi_data[0].get("sumOpenInterest", 0.0))
                        oi_now = float(oi_data[-1].get("sumOpenInterest", 0.0))
                        if oi_3_periods_ago > 0.0:
                            delta_oi_pct = (
                                (oi_now - oi_3_periods_ago) / oi_3_periods_ago
                            ) * 100.0
        except Exception:
            delta_oi_pct = 0.0

        # Check rate limit again before second call
        if self.is_rate_limited(now):
            return 0.0, 0.0, 0.0

        # 4. Query Premium Index
        prem_params = {"symbol": symbol}
        funding_rate = 0.0
        basis_bps = 0.0
        try:
            async with session.get(PREMIUM_INDEX_URL, params=prem_params) as resp:
                self._update_weight_from_headers(resp.headers, now)
                if resp.status == 200:
                    raw_body = await resp.read()
                    prem_data = orjson.loads(raw_body)
                    if isinstance(prem_data, dict):
                        mark_price = float(prem_data.get("markPrice", 0.0))
                        index_price = float(prem_data.get("indexPrice", 0.0))
                        funding_rate = float(prem_data.get("lastFundingRate", 0.0))
                        next_funding = int(prem_data.get("nextFundingTime", 0))
                        self.next_funding_times[symbol] = next_funding

                        if index_price > 0.0:
                            basis_bps = (
                                (mark_price - index_price) / index_price
                            ) * 10000.0
        except Exception:
            funding_rate = 0.0
            basis_bps = 0.0

        # 5. Populate 180s Cache
        self.oi_cache[symbol] = (now, delta_oi_pct, funding_rate, basis_bps)
        return delta_oi_pct, funding_rate, basis_bps

    def get_next_funding_time(self, symbol: str) -> int:
        """Return the next funding timestamp in ms for *symbol*, or 0 if unknown."""
        return self.next_funding_times.get(symbol, 0)
