"""tests/test_config.py — Validate config constants."""

import sys
import os

# Ensure project root is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import (
    SYMBOLS,
    WS_BASE_URL,
    WS_USDM_BASE_URL,
    WS_SPOT_BASE_URL,
    WS_COINM_BASE_URL,
    PUBLIC_STREAMS,
    MARKET_STREAMS,
    GLOBAL_MARKET_STREAMS,
    USDM_STREAMS,
    GLOBAL_USDM_STREAMS,
    SPOT_STREAMS,
    COINM_STREAMS,
    TRADE_WINDOW,
    DEPTH_WINDOW,
    SPREAD_WINDOW,
    SPOT_RING_WINDOW,
    COINM_OFI_WINDOW,
    JITTER_WINDOW,
    RECONNECT_DELAY_INIT,
    RECONNECT_DELAY_MAX,
    RECONNECT_MULTIPLIER,
)


class TestSymbols:
    """Verify the symbol list is well-formed for the 10-pair universe."""

    def test_symbols_is_tuple(self) -> None:
        assert isinstance(SYMBOLS, tuple)

    def test_symbols_count_is_ten(self) -> None:
        assert len(SYMBOLS) == 10

    def test_required_ten_symbols_present(self) -> None:
        expected = (
            "BTCUSDT",
            "ETHUSDT",
            "SOLUSDT",
            "SUIUSDT",
            "LINKUSDT",
            "TONUSDT",
            "AVAXUSDT",
            "NEARUSDT",
            "AAVEUSDT",
            "DOGEUSDT",
        )
        for sym in expected:
            assert sym in SYMBOLS

    def test_symbols_uppercase(self) -> None:
        for sym in SYMBOLS:
            assert sym == sym.upper(), f"{sym} is not uppercase"


class TestURLs:
    """Verify WebSocket URLs format for USD-M, Spot, and COIN-M."""

    def test_base_url_starts_with_wss(self) -> None:
        assert WS_BASE_URL.startswith("wss://")

    def test_base_url_is_binance_futures(self) -> None:
        assert "fstream.binance.com" in WS_BASE_URL

    def test_usdm_url(self) -> None:
        assert WS_USDM_BASE_URL.startswith("wss://")
        assert "fstream.binance.com" in WS_USDM_BASE_URL

    def test_spot_url(self) -> None:
        assert WS_SPOT_BASE_URL.startswith("wss://")
        assert "stream.binance.com:9443" in WS_SPOT_BASE_URL
        assert "stream?streams=" in WS_SPOT_BASE_URL

    def test_coinm_url(self) -> None:
        assert WS_COINM_BASE_URL.startswith("wss://")
        assert "dstream.binance.com" in WS_COINM_BASE_URL
        assert "stream?streams=" in WS_COINM_BASE_URL


class TestStreams:
    """Verify stream configurations across USD-M, Spot, and COIN-M."""

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

    def test_spot_streams(self) -> None:
        assert isinstance(SPOT_STREAMS, tuple)
        assert any("timeUnit=MICROSECOND" in s for s in SPOT_STREAMS)
        assert "bookTicker" in SPOT_STREAMS

    def test_coinm_streams(self) -> None:
        assert isinstance(COINM_STREAMS, tuple)
        assert "!bookTicker" in COINM_STREAMS
        assert "!forceOrder@arr" in COINM_STREAMS


class TestWindowSizes:
    """Verify rolling window sizes are positive integers."""

    def test_trade_window(self) -> None:
        assert isinstance(TRADE_WINDOW, int) and TRADE_WINDOW > 0

    def test_depth_window(self) -> None:
        assert isinstance(DEPTH_WINDOW, int) and DEPTH_WINDOW > 0

    def test_spread_window(self) -> None:
        assert isinstance(SPREAD_WINDOW, int) and SPREAD_WINDOW > 0

    def test_phase6_windows(self) -> None:
        assert isinstance(SPOT_RING_WINDOW, int) and SPOT_RING_WINDOW > 0
        assert isinstance(COINM_OFI_WINDOW, int) and COINM_OFI_WINDOW > 0
        assert isinstance(JITTER_WINDOW, int) and JITTER_WINDOW == 20


class TestReconnect:
    """Verify reconnect parameters are sane."""

    def test_init_delay_positive(self) -> None:
        assert RECONNECT_DELAY_INIT > 0

    def test_max_exceeds_init(self) -> None:
        assert RECONNECT_DELAY_MAX > RECONNECT_DELAY_INIT

    def test_multiplier_above_one(self) -> None:
        assert RECONNECT_MULTIPLIER > 1.0
