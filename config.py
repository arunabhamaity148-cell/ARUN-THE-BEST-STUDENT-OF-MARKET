"""
config.py — Global configuration for the signal bot.

All tunable parameters live here so nothing is hard-coded in modules.
"""

# ── Symbols ──────────────────────────────────────────────────────────
SYMBOLS: tuple[str, ...] = ("BTCUSDT", "ETHUSDT", "SOLUSDT")

# ── WebSocket URLs ───────────────────────────────────────────────────
WS_BASE_URL: str = "wss://fstream.binance.com/stream"

# Per-symbol stream suffixes
PUBLIC_STREAMS: tuple[str, ...] = ("depth20@100ms", "bookTicker")
MARKET_STREAMS: tuple[str, ...] = ("aggTrade", "kline_1m")

# Global streams (not per-symbol, prefixed with !)
GLOBAL_MARKET_STREAMS: tuple[str, ...] = ("!forceOrder@arr",)

# ── Rolling Window Sizes ────────────────────────────────────────────
TRADE_WINDOW: int = 500       # recent trades kept per symbol
DEPTH_WINDOW: int = 100       # depth snapshots kept per symbol
SPREAD_WINDOW: int = 200      # spread samples kept per symbol

# ── Reconnection ────────────────────────────────────────────────────
RECONNECT_DELAY_INIT: float = 1.0    # seconds
RECONNECT_DELAY_MAX: float = 60.0    # seconds
RECONNECT_MULTIPLIER: float = 2.0    # exponential back-off factor

# ── Phase 2: Engine Constants ────────────────────────────────────────
OFI_WINDOW: int = 100            # recent OFI values per symbol
LIQ_EVENT_WINDOW: int = 2000     # max liquidation events per symbol
ABSORPTION_SNAP_WINDOW: int = 1000  # ~1/sec for 15 min + buffer
TRADE_DELTA_WINDOW: int = 500    # recent trade deltas for CVD direction
