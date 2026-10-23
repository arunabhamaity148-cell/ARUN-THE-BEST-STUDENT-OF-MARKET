"""
ingestion/ws_manager.py — Triple-WebSocket manager for Binance (USD-M, Spot, COIN-M).

Architecture
------------
Three independent WebSocket connections with exponential back-off reconnect:

  1. USD-M  → depth20@100ms, aggTrade, kline_1m, !bookTicker, !forceOrder@arr
  2. Spot   → aggTrade?timeUnit=MICROSECOND, bookTicker (lead-lag discovery)
  3. COIN-M → !bookTicker, !forceOrder@arr (cross-margin collateral stress)

Raw bytes are passed directly to orjson.loads() — no .decode('utf-8').
"""

import asyncio
import logging
from typing import Callable, Awaitable

import orjson

try:
    import websockets
    import websockets.asyncio.client as ws_client
except ImportError:          # allow import in test envs without websockets
    websockets = None        # type: ignore[assignment]
    ws_client = None         # type: ignore[assignment]

from config import (
    SYMBOLS,
    WS_BASE_URL,
    WS_SPOT_BASE_URL,
    WS_COINM_BASE_URL,
    RECONNECT_DELAY_INIT,
    RECONNECT_DELAY_MAX,
    RECONNECT_MULTIPLIER,
)

logger = logging.getLogger(__name__)

# Type alias for the async callback that receives parsed dicts
MessageCallback = Callable[[dict], Awaitable[None]]


# ─────────────────────────────────────────────────────────────────────
# URL helpers (pure functions — easy to unit-test)
# ─────────────────────────────────────────────────────────────────────

def build_stream_names(
    symbols: tuple[str, ...],
    stream_types: tuple[str, ...],
) -> list[str]:
    """Build per-symbol stream names.

    >>> build_stream_names(("BTCUSDT",), ("aggTrade",))
    ['btcusdt@aggTrade']
    """
    return [f"{sym.lower()}@{st}" for sym in symbols for st in stream_types]


def build_stream_url(
    base_url: str,
    stream_names: list[str],
) -> str:
    """Combine stream names into a single combined-stream URL.

    >>> build_stream_url("wss://example.com/stream", ["a@b", "c@d"])
    'wss://example.com/stream?streams=a@b/c@d'
    """
    joined = "/".join(stream_names)
    if base_url.endswith("?streams="):
        return f"{base_url}{joined}"
    elif "?streams=" in base_url:
        sep = "/" if not base_url.endswith("/") and not base_url.endswith("=") else ""
        return f"{base_url}{sep}{joined}"
    return f"{base_url}?streams={joined}"


# ─────────────────────────────────────────────────────────────────────
# Triple-WebSocket Manager (USD-M, Spot, COIN-M)
# ─────────────────────────────────────────────────────────────────────

class WSManager:
    """Manages three concurrent WebSocket connections to Binance.

    Parameters
    ----------
    symbols : tuple[str, ...]
        Uppercase symbol names (e.g. ``("BTCUSDT", ...)``).
    base_url : str
        USD-M WebSocket base URL.
    on_message : MessageCallback | None
        Async callable invoked with each parsed JSON dict.
    spot_base_url : str
        Spot WebSocket base URL.
    coinm_base_url : str
        COIN-M WebSocket base URL.
    """

    __slots__ = (
        "_symbols",
        "_base_url",
        "_spot_base_url",
        "_coinm_base_url",
        "_usdm_url",
        "_spot_url",
        "_coinm_url",
        "_public_url",
        "_market_url",
        "_on_message",
        "_running",
        "_tasks",
    )

    def __init__(
        self,
        symbols: tuple[str, ...] = SYMBOLS,
        base_url: str = WS_BASE_URL,
        on_message: MessageCallback | None = None,
        spot_base_url: str = WS_SPOT_BASE_URL,
        coinm_base_url: str = WS_COINM_BASE_URL,
    ) -> None:
        self._symbols = symbols
        self._base_url = base_url
        self._spot_base_url = spot_base_url
        self._coinm_base_url = coinm_base_url
        self._on_message: MessageCallback = on_message or self._noop

        # 1. USD-M Socket: @depth20@100ms, @aggTrade, @kline_1m, !bookTicker, !forceOrder@arr
        usdm_names: list[str] = []
        for sym in symbols:
            s_low = sym.lower()
            usdm_names.append(f"{s_low}@depth20@100ms")
            usdm_names.append(f"{s_low}@aggTrade")
            usdm_names.append(f"{s_low}@kline_1m")
        usdm_names.append("!bookTicker")
        usdm_names.append("!forceOrder@arr")

        self._usdm_url: str = build_stream_url(base_url, usdm_names)
        # Legacy aliases for backward compatibility
        self._public_url: str = self._usdm_url
        self._market_url: str = self._usdm_url

        # 2. Spot Socket: @aggTrade (?timeUnit=MICROSECOND) and @bookTicker
        spot_names: list[str] = []
        for sym in symbols:
            s_low = sym.lower()
            spot_names.append(f"{s_low}@aggTrade?timeUnit=MICROSECOND")
            spot_names.append(f"{s_low}@bookTicker")

        self._spot_url: str = build_stream_url(spot_base_url, spot_names)

        # 3. COIN-M Socket: !bookTicker and !forceOrder@arr
        coinm_names: list[str] = ["!bookTicker", "!forceOrder@arr"]
        self._coinm_url: str = build_stream_url(coinm_base_url, coinm_names)

        self._running: bool = False
        self._tasks: list[asyncio.Task] = []

    # ── static fallback ─────────────────────────────────────────────

    @staticmethod
    async def _noop(data: dict) -> None:  # pragma: no cover
        """Default no-op callback when none is provided."""

    # ── core listen loop ────────────────────────────────────────────

    async def _listen(self, url: str, label: str) -> None:
        """Connect, receive, parse, dispatch — with exponential back-off.

        Raw frames are fed directly to ``orjson.loads()`` (no decode).
        """
        if ws_client is None:
            raise RuntimeError(
                "websockets library is not installed — "
                "run `pip install websockets`"
            )

        delay = RECONNECT_DELAY_INIT

        while self._running:
            try:
                async with ws_client.connect(
                    url,
                    ping_interval=20,
                    ping_timeout=10,
                ) as ws:
                    logger.info("[%s] connected → %s", label, url)
                    delay = RECONNECT_DELAY_INIT        # reset on success

                    async for raw in ws:
                        if not self._running:
                            break
                        # ── KEY RULE: raw bytes → orjson directly ──
                        # orjson.loads() accepts both bytes and str;
                        # Binance sends text frames so websockets may
                        # hand us a str — we never call .decode().
                        data: dict = orjson.loads(raw)
                        await self._on_message(data)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                if not self._running:
                    break
                logger.warning(
                    "[%s] disconnected (%s), retrying in %.1fs …",
                    label, exc, delay,
                )
                await asyncio.sleep(delay)
                delay = min(delay * RECONNECT_MULTIPLIER, RECONNECT_DELAY_MAX)

        logger.info("[%s] listener stopped", label)

    # ── lifecycle ───────────────────────────────────────────────────

    async def start(self) -> None:
        """Launch all three WebSocket listeners as background tasks."""
        self._running = True
        self._tasks = [
            asyncio.create_task(
                self._listen(self._usdm_url, "usdm"),
                name="ws-usdm",
            ),
            asyncio.create_task(
                self._listen(self._spot_url, "spot"),
                name="ws-spot",
            ),
            asyncio.create_task(
                self._listen(self._coinm_url, "coinm"),
                name="ws-coinm",
            ),
        ]
        logger.info(
            "WSManager started — %d symbols, 3 connections (USD-M, Spot, COIN-M)",
            len(self._symbols),
        )

    async def stop(self) -> None:
        """Cancel all 3 listeners and wait for clean shutdown."""
        self._running = False
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        logger.info("WSManager stopped")

    # ── read-only properties ────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def usdm_url(self) -> str:
        return self._usdm_url

    @property
    def spot_url(self) -> str:
        return self._spot_url

    @property
    def coinm_url(self) -> str:
        return self._coinm_url

    @property
    def public_url(self) -> str:
        return self._public_url

    @property
    def market_url(self) -> str:
        return self._market_url

    @property
    def symbols(self) -> tuple[str, ...]:
        return self._symbols

    def __repr__(self) -> str:
        status = "running" if self._running else "stopped"
        return f"WSManager({status}, symbols={self._symbols}, 3 sockets)"
