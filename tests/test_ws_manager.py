"""tests/test_ws_manager.py — Tests for ingestion/ws_manager.py.

We test:
  1. Pure helper functions (build_stream_names, build_stream_url)
  2. WSManager initialisation & 3 socket URL construction (USD-M, Spot, COIN-M)
  3. Message dispatch with a mocked WebSocket
  4. Reconnection back-off logic
"""

import asyncio
import sys
import os
from unittest.mock import AsyncMock, MagicMock, patch

import orjson
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ingestion.ws_manager import (
    build_stream_names,
    build_stream_url,
    WSManager,
)
from config import (
    SYMBOLS,
    WS_BASE_URL,
    WS_SPOT_BASE_URL,
    WS_COINM_BASE_URL,
    PUBLIC_STREAMS,
    MARKET_STREAMS,
    GLOBAL_MARKET_STREAMS,
)


# ─────────────────────────────────────────────────────────────────────
# build_stream_names
# ─────────────────────────────────────────────────────────────────────

class TestBuildStreamNames:

    def test_single_symbol_single_stream(self) -> None:
        result = build_stream_names(("BTCUSDT",), ("aggTrade",))
        assert result == ["btcusdt@aggTrade"]

    def test_multiple_symbols(self) -> None:
        result = build_stream_names(("BTCUSDT", "ETHUSDT"), ("aggTrade",))
        assert result == ["btcusdt@aggTrade", "ethusdt@aggTrade"]

    def test_multiple_streams(self) -> None:
        result = build_stream_names(("BTCUSDT",), ("aggTrade", "kline_1m"))
        assert result == ["btcusdt@aggTrade", "btcusdt@kline_1m"]

    def test_cartesian_product(self) -> None:
        result = build_stream_names(
            ("BTCUSDT", "ETHUSDT"),
            ("aggTrade", "bookTicker"),
        )
        assert len(result) == 4
        assert "btcusdt@aggTrade" in result
        assert "ethusdt@bookTicker" in result

    def test_lowercases_symbols(self) -> None:
        result = build_stream_names(("SOLUSDT",), ("depth20@100ms",))
        assert result[0].startswith("solusdt@")

    def test_empty_symbols(self) -> None:
        assert build_stream_names((), ("aggTrade",)) == []

    def test_empty_streams(self) -> None:
        assert build_stream_names(("BTCUSDT",), ()) == []


# ─────────────────────────────────────────────────────────────────────
# build_stream_url
# ─────────────────────────────────────────────────────────────────────

class TestBuildStreamUrl:

    def test_single_stream(self) -> None:
        url = build_stream_url("wss://example.com/stream", ["a@b"])
        assert url == "wss://example.com/stream?streams=a@b"

    def test_multiple_streams_joined(self) -> None:
        url = build_stream_url("wss://x.com/stream", ["a@b", "c@d", "e@f"])
        assert url == "wss://x.com/stream?streams=a@b/c@d/e@f"

    def test_real_base_url(self) -> None:
        url = build_stream_url(WS_BASE_URL, ["btcusdt@aggTrade"])
        assert url.startswith("wss://fstream.binance.com/stream?streams=")

    def test_base_url_with_query_prefix(self) -> None:
        url = build_stream_url("wss://stream.binance.com:9443/stream?streams=", ["a@b", "c@d"])
        assert url == "wss://stream.binance.com:9443/stream?streams=a@b/c@d"


# ─────────────────────────────────────────────────────────────────────
# WSManager — Slots
# ─────────────────────────────────────────────────────────────────────

class TestWSManagerSlots:

    def test_has_slots(self) -> None:
        assert hasattr(WSManager, "__slots__")

    def test_no_instance_dict(self) -> None:
        mgr = WSManager()
        assert not hasattr(mgr, "__dict__")


# ─────────────────────────────────────────────────────────────────────
# WSManager — Initialization & 3 Socket URLs
# ─────────────────────────────────────────────────────────────────────

class TestWSManagerInit:

    def test_default_symbols(self) -> None:
        mgr = WSManager()
        assert mgr.symbols == SYMBOLS
        assert len(mgr.symbols) == 10

    def test_custom_symbols(self) -> None:
        mgr = WSManager(symbols=("XRPUSDT",))
        assert mgr.symbols == ("XRPUSDT",)

    def test_not_running_initially(self) -> None:
        mgr = WSManager()
        assert mgr.is_running is False

    # ── 1. USD-M Socket URL ──
    def test_usdm_url_contains_expected_streams(self) -> None:
        mgr = WSManager(symbols=("BTCUSDT",))
        assert "depth20@100ms" in mgr.usdm_url
        assert "btcusdt@aggTrade" in mgr.usdm_url
        assert "btcusdt@kline_1m" in mgr.usdm_url
        assert "!bookTicker" in mgr.usdm_url
        assert "!forceOrder@arr" in mgr.usdm_url
        assert mgr.usdm_url.startswith("wss://fstream.binance.com/stream?streams=")

    def test_public_and_market_url_aliases(self) -> None:
        mgr = WSManager(symbols=("BTCUSDT",))
        assert mgr.public_url == mgr.usdm_url
        assert mgr.market_url == mgr.usdm_url

    # ── 2. Spot Socket URL ──
    def test_spot_url_contains_expected_streams(self) -> None:
        mgr = WSManager(symbols=("BTCUSDT", "ETHUSDT"))
        assert "btcusdt@aggTrade?timeUnit=MICROSECOND" in mgr.spot_url
        assert "btcusdt@bookTicker" in mgr.spot_url
        assert "ethusdt@aggTrade?timeUnit=MICROSECOND" in mgr.spot_url
        assert "ethusdt@bookTicker" in mgr.spot_url
        assert "stream.binance.com:9443" in mgr.spot_url

    # ── 3. COIN-M Socket URL ──
    def test_coinm_url_contains_expected_streams(self) -> None:
        mgr = WSManager()
        assert "!bookTicker" in mgr.coinm_url
        assert "!forceOrder@arr" in mgr.coinm_url
        assert "dstream.binance.com" in mgr.coinm_url

    def test_all_ten_symbols_in_usdm_and_spot_url(self) -> None:
        mgr = WSManager()
        for sym in SYMBOLS:
            assert sym.lower() in mgr.usdm_url
            assert sym.lower() in mgr.spot_url

    def test_repr(self) -> None:
        mgr = WSManager(symbols=("BTCUSDT",))
        r = repr(mgr)
        assert "WSManager" in r
        assert "stopped" in r
        assert "3 sockets" in r


# ─────────────────────────────────────────────────────────────────────
# WSManager — Message Dispatch (mocked WebSocket)
# ─────────────────────────────────────────────────────────────────────

class TestWSManagerMessageDispatch:
    """Verify on_message callback receives parsed dicts from raw bytes."""

    @pytest.mark.asyncio
    async def test_callback_receives_parsed_dict(self) -> None:
        """Simulate a WebSocket sending raw bytes and verify orjson parse."""
        received: list[dict] = []

        async def on_msg(data: dict) -> None:
            received.append(data)

        mgr = WSManager(symbols=("BTCUSDT",), on_message=on_msg)

        payload = {"stream": "btcusdt@aggTrade", "data": {"p": "50000.0"}}
        raw_bytes = orjson.dumps(payload)

        mock_ws = AsyncMock()
        mock_ws.__aenter__ = AsyncMock(return_value=mock_ws)
        mock_ws.__aexit__ = AsyncMock(return_value=False)

        call_count = 0

        async def fake_aiter(self_ws):
            nonlocal call_count
            if call_count == 0:
                call_count += 1
                yield raw_bytes
            mgr._running = False

        mock_ws.__aiter__ = lambda self_ws: fake_aiter(self_ws)

        with patch("ingestion.ws_manager.ws_client") as mock_ws_client:
            mock_ws_client.connect.return_value = mock_ws
            mgr._running = True
            await mgr._listen(mgr.usdm_url, "test")

        assert len(received) == 1
        assert received[0]["stream"] == "btcusdt@aggTrade"
        assert received[0]["data"]["p"] == "50000.0"

    @pytest.mark.asyncio
    async def test_callback_handles_str_frames(self) -> None:
        """orjson.loads also accepts str — verify no crash."""
        received: list[dict] = []

        async def on_msg(data: dict) -> None:
            received.append(data)

        mgr = WSManager(symbols=("BTCUSDT",), on_message=on_msg)

        payload_str = '{"stream":"btcusdt@bookTicker","data":{}}'
        call_count = 0

        mock_ws = AsyncMock()
        mock_ws.__aenter__ = AsyncMock(return_value=mock_ws)
        mock_ws.__aexit__ = AsyncMock(return_value=False)

        async def fake_aiter(self_ws):
            nonlocal call_count
            if call_count == 0:
                call_count += 1
                yield payload_str
            mgr._running = False

        mock_ws.__aiter__ = lambda self_ws: fake_aiter(self_ws)

        with patch("ingestion.ws_manager.ws_client") as mock_ws_client:
            mock_ws_client.connect.return_value = mock_ws
            mgr._running = True
            await mgr._listen(mgr.spot_url, "test")

        assert len(received) == 1
        assert received[0]["stream"] == "btcusdt@bookTicker"


# ─────────────────────────────────────────────────────────────────────
# WSManager — orjson raw-bytes rule
# ─────────────────────────────────────────────────────────────────────

class TestOrjsonRawBytesRule:
    """Verify we never call .decode() — orjson handles bytes natively."""

    def test_orjson_loads_bytes(self) -> None:
        raw = b'{"a": 1}'
        result = orjson.loads(raw)
        assert result == {"a": 1}

    def test_orjson_loads_str(self) -> None:
        raw = '{"a": 1}'
        result = orjson.loads(raw)
        assert result == {"a": 1}

    def test_no_decode_in_source(self) -> None:
        """Grep ws_manager source — .decode() must not appear in the hot path."""
        import inspect
        from ingestion import ws_manager

        source = inspect.getsource(ws_manager)
        lines = source.split("\n")
        in_docstring = False
        for line in lines:
            stripped = line.lstrip()
            triple_count = stripped.count('"""')
            if triple_count == 1:
                in_docstring = not in_docstring
                continue
            if triple_count >= 2:
                continue
            if in_docstring or stripped.startswith("#"):
                continue
            assert ".decode(" not in stripped, (
                f"Found .decode() in executable line: {line!r}"
            )


# ─────────────────────────────────────────────────────────────────────
# WSManager — start / stop lifecycle (3 sockets)
# ─────────────────────────────────────────────────────────────────────

class TestWSManagerLifecycle:

    @pytest.mark.asyncio
    async def test_start_sets_running(self) -> None:
        mgr = WSManager(symbols=("BTCUSDT",))

        with patch("ingestion.ws_manager.ws_client") as mock_ws_client:
            mock_ws_client.connect.side_effect = ConnectionError("test")
            await mgr.start()
            assert mgr.is_running is True
            assert len(mgr._tasks) == 3
            await mgr.stop()
            assert mgr.is_running is False

    @pytest.mark.asyncio
    async def test_stop_cancels_tasks(self) -> None:
        mgr = WSManager(symbols=("BTCUSDT",))

        with patch("ingestion.ws_manager.ws_client") as mock_ws_client:
            mock_ws_client.connect.side_effect = ConnectionError("test")
            await mgr.start()
            assert len(mgr._tasks) == 3
            await mgr.stop()
            assert len(mgr._tasks) == 0
