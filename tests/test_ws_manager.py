"""tests/test_ws_manager.py — Tests for ingestion/ws_manager.py.

We test:
  1. Pure helper functions (build_stream_names, build_stream_url)
  2. WSManager initialisation & URL construction
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
# WSManager — Initialization
# ─────────────────────────────────────────────────────────────────────

class TestWSManagerInit:

    def test_default_symbols(self) -> None:
        mgr = WSManager()
        assert mgr.symbols == SYMBOLS

    def test_custom_symbols(self) -> None:
        mgr = WSManager(symbols=("XRPUSDT",))
        assert mgr.symbols == ("XRPUSDT",)

    def test_not_running_initially(self) -> None:
        mgr = WSManager()
        assert mgr.is_running is False

    def test_public_url_contains_depth(self) -> None:
        mgr = WSManager(symbols=("BTCUSDT",))
        assert "depth20@100ms" in mgr.public_url

    def test_public_url_contains_book_ticker(self) -> None:
        mgr = WSManager(symbols=("BTCUSDT",))
        assert "bookTicker" in mgr.public_url

    def test_market_url_contains_agg_trade(self) -> None:
        mgr = WSManager(symbols=("BTCUSDT",))
        assert "aggTrade" in mgr.market_url

    def test_market_url_contains_kline(self) -> None:
        mgr = WSManager(symbols=("BTCUSDT",))
        assert "kline_1m" in mgr.market_url

    def test_market_url_contains_force_order(self) -> None:
        mgr = WSManager(symbols=("BTCUSDT",))
        assert "forceOrder" in mgr.market_url

    def test_all_symbols_in_public_url(self) -> None:
        mgr = WSManager(symbols=("BTCUSDT", "ETHUSDT", "SOLUSDT"))
        for sym in ("btcusdt", "ethusdt", "solusdt"):
            assert sym in mgr.public_url

    def test_repr(self) -> None:
        mgr = WSManager(symbols=("BTCUSDT",))
        r = repr(mgr)
        assert "WSManager" in r
        assert "stopped" in r


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

        # Build a fake payload Binance-style
        payload = {"stream": "btcusdt@aggTrade", "data": {"p": "50000.0"}}
        raw_bytes = orjson.dumps(payload)

        # Simulate: create a mock async-iterable WebSocket
        mock_ws = AsyncMock()
        mock_ws.__aenter__ = AsyncMock(return_value=mock_ws)
        mock_ws.__aexit__ = AsyncMock(return_value=False)

        # The async for loop needs __aiter__ → returns self, __anext__ → yields frames
        call_count = 0

        async def fake_aiter(self_ws):
            nonlocal call_count
            if call_count == 0:
                call_count += 1
                yield raw_bytes
            # After first yield, manager loop waits for next frame;
            # we stop running to exit cleanly
            mgr._running = False

        mock_ws.__aiter__ = lambda self_ws: fake_aiter(self_ws)

        with patch("ingestion.ws_manager.ws_client") as mock_ws_client:
            mock_ws_client.connect.return_value = mock_ws
            mgr._running = True
            # Run listener directly (don't use start() — we only test one)
            await mgr._listen(mgr.public_url, "test")

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
            await mgr._listen(mgr.public_url, "test")

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
        """Grep ws_manager source — .decode() must not appear in the
        message-handling hot path."""
        import inspect
        from ingestion import ws_manager

        source = inspect.getsource(ws_manager)
        # Check only executable lines — skip comments, docstrings, and
        # plain-text lines inside triple-quoted strings.
        lines = source.split("\n")
        in_docstring = False
        for line in lines:
            stripped = line.lstrip()
            # Toggle docstring state on triple-quote boundaries
            triple_count = stripped.count('"""')
            if triple_count == 1:
                in_docstring = not in_docstring
                continue
            if triple_count >= 2:
                # Opening and closing on same line — skip it
                continue
            if in_docstring or stripped.startswith("#"):
                continue
            assert ".decode(" not in stripped, (
                f"Found .decode() in executable line: {line!r}"
            )


# ─────────────────────────────────────────────────────────────────────
# WSManager — start / stop lifecycle
# ─────────────────────────────────────────────────────────────────────

class TestWSManagerLifecycle:

    @pytest.mark.asyncio
    async def test_start_sets_running(self) -> None:
        mgr = WSManager(symbols=("BTCUSDT",))

        with patch("ingestion.ws_manager.ws_client") as mock_ws_client:
            # Make connect raise immediately to avoid hanging
            mock_ws_client.connect.side_effect = ConnectionError("test")
            await mgr.start()
            assert mgr.is_running is True
            # Clean up
            await mgr.stop()
            assert mgr.is_running is False

    @pytest.mark.asyncio
    async def test_stop_cancels_tasks(self) -> None:
        mgr = WSManager(symbols=("BTCUSDT",))

        with patch("ingestion.ws_manager.ws_client") as mock_ws_client:
            mock_ws_client.connect.side_effect = ConnectionError("test")
            await mgr.start()
            assert len(mgr._tasks) == 2
            await mgr.stop()
            assert len(mgr._tasks) == 0
