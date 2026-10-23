"""
engine/alt_data_veto.py — Alternative Data & Institutional Macro Vetoes (Phase 6D).

Guards & Filters
----------------
1. DefiLlama Stablecoin Flow Veto:
   - Polls DefiLlama stablecoin supply data every 30 minutes.
   - Tracks 24-hour supply change across chains: Sui, Solana, Avalanche, Near, TON.
   - Rule: If chain stablecoin 24h change <= -1.5%, veto all LONG breakout signals
     for that chain's native token (SUIUSDT, SOLUSDT, AVAXUSDT, NEARUSDT, TONUSDT).

2. US Spot ETF AP Window Veto:
   - Deterministic UTC time-gate during Authorized Participant (AP) daily creation/redemption rebalance:
     15:45 ET - 16:15 ET.
     Summer (DST): 19:45 - 20:15 UTC.
     Winter (Standard): 20:45 - 21:15 UTC.
   - Rule: Block all momentum breakout signals unless Z_liq > 3.5 (liquidation climax override).

3. Matching-Engine Jitter Veto (E - T):
   - Monitors latency between exchange matching engine trade execution (T) and WebSocket dispatch (E).
   - Rule: If rolling average of last 5 jitters > 35ms, trigger engine congestion veto.
   - Unlock Rule: Requires jitter <= 10ms for 20 consecutive frames to unlock.

Strict Constraints:
  - Zero numpy / pandas / scipy.
  - aiohttp for async HTTP polling.
  - __slots__ on all classes.
"""

import asyncio
import logging
import os
import tempfile
import time
from collections import deque
from datetime import datetime, timezone
from typing import Sequence

import aiohttp
import orjson

logger = logging.getLogger("alt_data_veto")

DEFILLAMA_CHAINS_URL: str = "https://stablecoins.llama.fi/stablecoinchains"
STABLECOIN_POLL_INTERVAL: float = 1800.0  # 30 minutes in seconds
STABLECOIN_DRAIN_THRESHOLD: float = -1.5  # <= -1.5% 24h change triggers veto

# Mapping from symbol to DefiLlama chain name
SYMBOL_TO_CHAIN: dict[str, str] = {
    "SUIUSDT": "Sui",
    "SOLUSDT": "Solana",
    "AVAXUSDT": "Avalanche",
    "NEARUSDT": "Near",
    "TONUSDT": "TON",
}


# ─────────────────────────────────────────────────────────────────────
# Pure Helper Functions (Stateless Mathematical Functions)
# ─────────────────────────────────────────────────────────────────────

def is_us_dst(dt_utc: datetime) -> bool:
    """Return True if dt_utc falls within US Daylight Saving Time (summer).

    US DST: Begins 2nd Sunday in March (07:00 UTC), ends 1st Sunday in November (06:00 UTC).
    """
    year = dt_utc.year

    # Second Sunday in March:
    march_1 = datetime(year, 3, 1, tzinfo=timezone.utc)
    first_sun_march = 1 + (6 - march_1.weekday()) % 7
    second_sun_march = first_sun_march + 7
    dst_start = datetime(year, 3, second_sun_march, 7, 0, tzinfo=timezone.utc)

    # First Sunday in November:
    nov_1 = datetime(year, 11, 1, tzinfo=timezone.utc)
    first_sun_nov = 1 + (6 - nov_1.weekday()) % 7
    dst_end = datetime(year, 11, first_sun_nov, 6, 0, tzinfo=timezone.utc)

    return dst_start <= dt_utc < dst_end


def check_etf_ap_window(dt_utc: datetime, z_liq: float = 0.0) -> bool:
    """Check if current UTC time falls in 15:45 - 16:15 ET Spot ETF AP rebalance window.

    Summer: 19:45 - 20:15 UTC.
    Winter: 20:45 - 21:15 UTC.
    Exhaustion Override: Z_liq > 3.5 allows signal despite ETF window.
    """
    if is_us_dst(dt_utc):
        in_window = (dt_utc.hour == 19 and dt_utc.minute >= 45) or (dt_utc.hour == 20 and dt_utc.minute <= 15)
    else:
        in_window = (dt_utc.hour == 20 and dt_utc.minute >= 45) or (dt_utc.hour == 21 and dt_utc.minute <= 15)

    if not in_window:
        return False

    # Blocked unless Z_liq > 3.5
    return z_liq <= 3.5


def check_stablecoin_drain(change_24h_pct: float) -> bool:
    """Return True if 24h stablecoin supply change is <= -1.5% (draining)."""
    return change_24h_pct <= STABLECOIN_DRAIN_THRESHOLD


def check_jitter_congestion(
    last_5_jitters: Sequence[float],
    is_currently_vetoed: bool,
    consecutive_low_count: int,
) -> tuple[bool, int]:
    """Pure state-transition function for matching-engine jitter congestion.

    - Trigger: avg(last_5) > 35ms -> VETO.
    - Unlock: 20 consecutive frames <= 10ms -> CLEAR.
    """
    if len(last_5_jitters) >= 5:
        avg_5 = sum(last_5_jitters[-5:]) / 5.0
        if avg_5 > 35.0:
            return True, 0

    if is_currently_vetoed and last_5_jitters:
        latest = last_5_jitters[-1]
        if latest <= 10.0:
            consecutive_low_count += 1
            if consecutive_low_count >= 20:
                return False, consecutive_low_count
        else:
            consecutive_low_count = 0

    return is_currently_vetoed, consecutive_low_count


# ─────────────────────────────────────────────────────────────────────
# AltDataVetoEngine Class
# ─────────────────────────────────────────────────────────────────────

class AltDataVetoEngine:
    """Institutional alternative data and micro-latency veto engine."""

    __slots__ = (
        "chain_24h_changes",
        "symbol_to_chain",
        "prev_circulating",
        "last_poll_ts",
        "cache_file",
        "is_jitter_vetoed",
        "consecutive_low_jitter_count",
        "jitter_history",
        "_session",
        "_poll_task",
        "_running",
    )

    def __init__(self, cache_dir: str | None = None) -> None:
        self.chain_24h_changes: dict[str, float] = {}
        self.symbol_to_chain: dict[str, str] = dict(SYMBOL_TO_CHAIN)
        self.prev_circulating: dict[str, float] = {}
        self.last_poll_ts: float = 0.0

        # Safe cache file path
        cdir = cache_dir or tempfile.gettempdir()
        self.cache_file: str = os.path.join(cdir, "defillama_stablecoin_cache.json")

        # Jitter state
        self.is_jitter_vetoed: bool = False
        self.consecutive_low_jitter_count: int = 0
        self.jitter_history: deque[float] = deque(maxlen=30)

        self._session: aiohttp.ClientSession | None = None
        self._poll_task: asyncio.Task | None = None
        self._running: bool = False

        self._load_cache()

    # ── 1. DefiLlama Stablecoin Flow ──────────────────────────────────

    def set_chain_change(self, chain: str, change_pct: float) -> None:
        """Manually set or update chain 24h change for testing or overrides."""
        self.chain_24h_changes[chain.lower()] = float(change_pct)

    def is_stablecoin_draining(self, symbol: str) -> bool:
        """Return True if chain stablecoin supply is draining (24h change <= -1.5%)."""
        chain = self.symbol_to_chain.get(symbol.upper())
        if chain is None:
            return False

        change_pct = self.chain_24h_changes.get(chain.lower(), 0.0)
        return check_stablecoin_drain(change_pct)

    def _load_cache(self) -> None:
        """Load cached DefiLlama data from disk if available and fresh (<30m)."""
        try:
            if os.path.exists(self.cache_file):
                with open(self.cache_file, "rb") as f:
                    data = orjson.loads(f.read())
                    saved_ts = data.get("ts", 0)
                    self.prev_circulating = data.get("prev_circulating", {})
                    if time.time() - saved_ts < STABLECOIN_POLL_INTERVAL:
                        self.chain_24h_changes = data.get("changes", {})
                        self.last_poll_ts = saved_ts
        except Exception as exc:
            logger.debug("Could not load stablecoin cache: %s", exc)

    def _save_cache(self) -> None:
        """Persist current chain changes and circulating supply to disk."""
        try:
            payload = {
                "ts": self.last_poll_ts,
                "changes": self.chain_24h_changes,
                "prev_circulating": self.prev_circulating,
            }
            with open(self.cache_file, "wb") as f:
                f.write(orjson.dumps(payload))
        except Exception as exc:
            logger.debug("Could not save stablecoin cache: %s", exc)

    async def fetch_defillama_data(self) -> None:
        """Fetch chain stablecoin stats from DefiLlama REST API."""
        now = time.time()
        if now - self.last_poll_ts < STABLECOIN_POLL_INTERVAL and self.chain_24h_changes:
            return

        session = self._session or aiohttp.ClientSession()
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with session.get(DEFILLAMA_CHAINS_URL, timeout=timeout) as resp:
                if resp.status != 200:
                    logger.warning("DefiLlama API returned HTTP %d", resp.status)
                    return

                raw_bytes = await resp.read()
                try:
                    data = orjson.loads(raw_bytes)
                except Exception as json_err:
                    logger.error(
                        "Failed to decode DefiLlama JSON: %s (raw sample: %s)",
                        json_err,
                        raw_bytes[:300],
                    )
                    return

                if not isinstance(data, list):
                    logger.warning("DefiLlama API returned non-list response: %s", type(data))
                    return

                parsed_count = 0
                for item in data:
                    try:
                        name = item.get("name") or item.get("gecko_id") or item.get("chain", "")
                        if not name or not isinstance(name, str):
                            continue

                        name_clean = name.strip()
                        name_key = name_clean.lower()

                        # 1. Direct 24h change fields (if returned by API or mock)
                        change_1d = None
                        if "change_1d" in item and item["change_1d"] is not None:
                            change_1d = float(item["change_1d"])
                        elif "change_24h" in item and item["change_24h"] is not None:
                            change_1d = float(item["change_24h"])
                        elif "totalCirculatingUSDPrevDay" in item:
                            tot_curr = item.get("totalCirculatingUSD")
                            tot_prev = item.get("totalCirculatingUSDPrevDay")
                            curr_val = (
                                float(tot_curr.get("peggedUSD", 0.0))
                                if isinstance(tot_curr, dict)
                                else float(tot_curr or 0.0)
                            )
                            prev_val = (
                                float(tot_prev.get("peggedUSD", 0.0))
                                if isinstance(tot_prev, dict)
                                else float(tot_prev or 0.0)
                            )
                            if prev_val > 0.0:
                                change_1d = ((curr_val - prev_val) / prev_val) * 100.0

                        # 2. Extract from totalCirculatingUSD against rolling cached supply
                        if change_1d is None and "totalCirculatingUSD" in item:
                            tot = item.get("totalCirculatingUSD")
                            curr_circ = 0.0
                            if isinstance(tot, dict):
                                curr_circ = float(tot.get("peggedUSD", 0.0))
                            elif isinstance(tot, (int, float)):
                                curr_circ = float(tot)

                            if curr_circ > 0.0:
                                prev_circ = self.prev_circulating.get(name_key)
                                if prev_circ is not None and prev_circ > 0.0:
                                    change_1d = ((curr_circ - prev_circ) / prev_circ) * 100.0
                                else:
                                    change_1d = 0.0
                                self.prev_circulating[name_key] = curr_circ

                        if change_1d is not None:
                            self.chain_24h_changes[name_key] = change_1d
                            parsed_count += 1

                    except Exception as item_err:
                        logger.debug("Failed parsing chain item %s: %s", item, item_err)
                        continue

                self.last_poll_ts = now
                self._save_cache()
                logger.info(
                    "Updated DefiLlama stablecoin 24h changes for %d chains (parsed %d)",
                    len(self.chain_24h_changes),
                    parsed_count,
                )

        except Exception as exc:
            logger.error("Failed fetching DefiLlama stablecoins: %s", exc)
            logger.warning("DefiLlama stablecoins poll failed (%s) — using cached data", exc)
        finally:
            if self._session is None:
                await session.close()

    # ── 2. US Spot ETF AP Window Veto ──────────────────────────────────

    def is_etf_window(self, z_liq: float = 0.0, dt_utc: datetime | None = None) -> bool:
        """Return True if inside 15:45 - 16:15 ET Spot ETF AP rebalance window without exhaustion."""
        now_dt = dt_utc if dt_utc is not None else datetime.now(timezone.utc)
        return check_etf_ap_window(now_dt, z_liq)

    # ── 3. Matching-Engine Jitter Veto (E - T) ─────────────────────────

    def update_jitter(self, jitter_ms: float) -> bool:
        """Incorporate new jitter frame and update congestion veto status."""
        self.jitter_history.append(float(jitter_ms))
        is_vetoed, count = check_jitter_congestion(
            list(self.jitter_history),
            self.is_jitter_vetoed,
            self.consecutive_low_jitter_count,
        )
        self.is_jitter_vetoed = is_vetoed
        self.consecutive_low_jitter_count = count
        return self.is_jitter_vetoed

    def is_engine_congested(self, jitter_ring: deque | None = None) -> bool:
        """Check if engine is congested (rolling 5-frame average > 35ms)."""
        ring = jitter_ring if jitter_ring is not None else self.jitter_history
        if not ring:
            return self.is_jitter_vetoed

        is_vetoed, count = check_jitter_congestion(
            list(ring),
            self.is_jitter_vetoed,
            self.consecutive_low_jitter_count,
        )
        self.is_jitter_vetoed = is_vetoed
        self.consecutive_low_jitter_count = count
        return self.is_jitter_vetoed

    # ── Lifecycle ─────────────────────────────────────────────────────

    async def _poll_loop(self) -> None:
        """Background periodic poller for DefiLlama."""
        while self._running:
            await self.fetch_defillama_data()
            await asyncio.sleep(STABLECOIN_POLL_INTERVAL)

    def start(self) -> None:
        """Start async background poller."""
        self._running = True
        self._poll_task = asyncio.create_task(self._poll_loop(), name="alt-data-poller")

    async def stop(self) -> None:
        """Stop background poller cleanly."""
        self._running = False
        if self._poll_task is not None:
            self._poll_task.cancel()
            await asyncio.gather(self._poll_task, return_exceptions=True)
            self._poll_task = None
        if self._session is not None:
            await self._session.close()
            self._session = None

    def __repr__(self) -> str:
        return (
            f"AltDataVetoEngine(chains={len(self.chain_24h_changes)}, "
            f"congested={self.is_jitter_vetoed})"
        )
