"""tests/test_config.py — Validate config constants."""

import sys
import os

# Ensure project root is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import (
    SYMBOLS,
    WS_BASE_URL,
    PUBLIC_STREAMS,
    MARKET_STREAMS,
    GLOBAL_MARKET_STREAMS,
    TRADE_WINDOW,
    DEPTH_WINDOW,
    SPREAD_WINDOW,
    RECONNECT_DELAY_INIT,
    RECONNECT_DELAY_MAX,
    RECONNECT_MULTIPLIER,
)


class TestSymbols:
    """Verify the symbol list is well-formed."""

    def test_symbols_is_tuple(self) -> None:
        assert isinstance(SYMBOLS, tuple)

    def test_symbols_not_empty(self) -> None:
        assert len(SYMBOLS) >= 1

    def test_required_symbols_present(self) -> None:
        for sym in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
            assert sym in SYMBOLS

    def test_symbols_uppercase(self) -> None:
        for sym in SYMBOLS:
            assert sym == sym.upper(), f"{sym} is not uppercase"


class TestURLs:
    """Verify WebSocket URL format."""

    def test_base_url_starts_with_wss(self) -> None:
        assert WS_BASE_URL.startswith("wss://")

    def test_base_url_is_binance_futures(self) -> None:
        assert "fstream.binance.com" in WS_BASE_URL


class TestStreams:
    """Verify stream configurations."""

    def test_public_streams_tuple(self) -> None:
        assert isinstance(PUBLIC_STREAMS, tuple)

    def test_public_has_depth(self) -> None:
        assert any("depth" in s for s in PUBLIC_STREAMS)

    def test_public_has_book_ticker(self) -> None:
        assert "bookTicker" in PUBLIC_STREAMS

    def test_market_streams_tuple(self) -> None:
        assert isinstance(MARKET_STREAMS, tuple)

    def test_market_has_agg_trade(self) -> None:
        assert "aggTrade" in MARKET_STREAMS

    def test_market_has_kline(self) -> None:
        assert any("kline" in s for s in MARKET_STREAMS)

    def test_global_streams_has_force_order(self) -> None:
        assert any("forceOrder" in s for s in GLOBAL_MARKET_STREAMS)


class TestWindowSizes:
    """Verify rolling window sizes are positive integers."""

    def test_trade_window(self) -> None:
        assert isinstance(TRADE_WINDOW, int) and TRADE_WINDOW > 0

    def test_depth_window(self) -> None:
        assert isinstance(DEPTH_WINDOW, int) and DEPTH_WINDOW > 0

    def test_spread_window(self) -> None:
        assert isinstance(SPREAD_WINDOW, int) and SPREAD_WINDOW > 0


class TestReconnect:
    """Verify reconnect parameters are sane."""

    def test_init_delay_positive(self) -> None:
        assert RECONNECT_DELAY_INIT > 0

    def test_max_exceeds_init(self) -> None:
        assert RECONNECT_DELAY_MAX > RECONNECT_DELAY_INIT

    def test_multiplier_above_one(self) -> None:
        assert RECONNECT_MULTIPLIER > 1.0
