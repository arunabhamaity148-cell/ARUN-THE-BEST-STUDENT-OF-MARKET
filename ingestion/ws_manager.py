"""
ingestion/ws_manager.py — Dual-WebSocket manager for Binance Futures.

Architecture
------------
Two independent WebSocket connections with exponential back-off reconnect:

  Public  → depth20@100ms, bookTicker   (order-book state)
  Market  → aggTrade, kline_1m, !forceOrder@arr  (trade / liquidation flow)

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
    PUBLIC_STREAMS,
    MARKET_STREAMS,
    GLOBAL_MARKET_STREAMS,
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
    return f"{base_url}?streams={joined}"


# ─────────────────────────────────────────────────────────────────────
# Dual-WebSocket Manager
# ─────────────────────────────────────────────────────────────────────

class WSManager:
    """Manages two concurrent WebSocket connections to Binance Futures.

    Parameters
    ----------
    symbols : tuple[str, ...]
        Uppercase symbol names (e.g. ``("BTCUSDT",)``).
    base_url : str
        WebSocket base URL (``wss://fstream.binance.com/stream``).
    on_message : MessageCallback
        Async callable invoked with each parsed JSON dict.
    """

    __slots__ = (
        "_symbols",
        "_base_url",
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
    ) -> None:
        self._symbols = symbols
        self._base_url = base_url
        self._on_message: MessageCallback = on_message or self._noop

        # Build the two combined-stream URLs
        public_names = build_stream_names(symbols, PUBLIC_STREAMS)
        market_names = build_stream_names(symbols, MARKET_STREAMS)
        market_names.extend(GLOBAL_MARKET_STREAMS)

        self._public_url: str = build_stream_url(base_url, public_names)
        self._market_url: str = build_stream_url(base_url, market_names)

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
        """Launch both WebSocket listeners as background tasks."""
        self._running = True
        self._tasks = [
            asyncio.create_task(
                self._listen(self._public_url, "public"),
                name="ws-public",
            ),
            asyncio.create_task(
                self._listen(self._market_url, "market"),
                name="ws-market",
            ),
        ]
        logger.info(
            "WSManager started — %d symbols, 2 connections",
            len(self._symbols),
        )

    async def stop(self) -> None:
        """Cancel both listeners and wait for clean shutdown."""
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
        return f"WSManager({status}, symbols={self._symbols})"
