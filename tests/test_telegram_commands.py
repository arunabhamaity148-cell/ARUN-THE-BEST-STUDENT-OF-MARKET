"""
tests/test_telegram_commands.py — Comprehensive unit tests for Telegram Command Dashboard.

Verifies:
  1. All 12 slash commands (/help, /status, /ping, /pairs, /edges, /vetoes, /risk,
     /scan, /top_edges, /memory, /last_signal, /version).
  2. Security checks (rejection of unauthorized chat IDs).
  3. Error handling for missing arguments or invalid symbols.
  4. Memory compliance (__slots__ on TelegramCommandHandler & TelegramCommandPoller).
  5. Poller lifecycle (start and graceful stop).
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock
import pytest

from dispatch.telegram_commands import (
    TelegramCommandHandler,
    TelegramCommandPoller,
    format_duration,
    get_process_memory_mb,
    get_cpu_load_percent,
    get_git_commit_hash,
)
from main import SignalBotOrchestrator


class TestTelegramCommandSlots:
    """Verify slots enforcement on command dashboard classes."""

    def test_handler_slots(self) -> None:
        assert hasattr(TelegramCommandHandler, "__slots__")
        bot = SignalBotOrchestrator()
        handler = TelegramCommandHandler(bot, allowed_chat_id="123456")
        assert not hasattr(handler, "__dict__")

    def test_poller_slots(self) -> None:
        assert hasattr(TelegramCommandPoller, "__slots__")
        bot = SignalBotOrchestrator()
        handler = TelegramCommandHandler(bot, allowed_chat_id="123456")
        poller = TelegramCommandPoller(handler, bot.dispatcher, allowed_chat_id="123456")
        assert not hasattr(poller, "__dict__")
        assert "TelegramCommandPoller" in repr(poller)


class TestTelegramCommandHelpers:
    """Verify formatting helpers."""

    def test_format_duration(self) -> None:
        assert format_duration(65) == "1m 5s"
        assert format_duration(3665) == "1h 1m 5s"
        assert format_duration(90065) == "1d 1h 1m 5s"

    def test_memory_and_cpu_helpers(self) -> None:
        assert get_process_memory_mb() > 0.0
        assert isinstance(get_cpu_load_percent(), float)
        assert isinstance(get_git_commit_hash(), str)


class TestTelegramCommandsExecution:
    """Test all 12 slash commands."""

    @pytest.fixture
    def setup_bot_and_handler(self) -> tuple[SignalBotOrchestrator, TelegramCommandHandler]:
        bot = SignalBotOrchestrator()
        handler = TelegramCommandHandler(bot, allowed_chat_id="999")
        return bot, handler

    def test_command_1_help(self, setup_bot_and_handler) -> None:
        _, handler = setup_bot_and_handler
        res = handler.handle_command("/help", chat_id="999")
        assert "COMMAND DASHBOARD" in res
        assert "/status" in res
        assert "/pairs" in res
        assert "/edges" in res
        assert "/vetoes" in res
        assert "/risk" in res
        assert "/scan" in res
        assert "/top_edges" in res
        assert "/memory" in res
        assert "/last_signal" in res
        assert "/version" in res

    def test_command_2_status(self, setup_bot_and_handler) -> None:
        _, handler = setup_bot_and_handler
        res = handler.handle_command("/status", chat_id="999")
        assert "SYSTEM HEALTH & CONNECTION STATUS" in res
        assert "RAM Usage:" in res
        assert "USD-M Futures:" in res
        assert "Spot Lead-Lag:" in res
        assert "COIN-M Stress:" in res
        assert "Deribit DVOL:" in res

    def test_command_3_ping(self, setup_bot_and_handler) -> None:
        _, handler = setup_bot_and_handler
        res = handler.handle_command("/ping", chat_id="999")
        assert "EXCHANGE WEBSOCKET LATENCIES" in res
        assert "Binance Matching Jitter (E - T):" in res
        assert "Deribit DVOL Latency:" in res

    def test_command_4_pairs(self, setup_bot_and_handler) -> None:
        _, handler = setup_bot_and_handler
        res = handler.handle_command("/pairs", chat_id="999")
        assert "TRACKED ASSET UNIVERSE (10 PAIRS)" in res
        assert "#BTCUSDT" in res
        assert "#ETHUSDT" in res
        assert "#SOLUSDT" in res
        assert "#SUIUSDT" in res
        assert "P(Win):" in res

    def test_command_5_edges_valid_symbol(self, setup_bot_and_handler) -> None:
        _, handler = setup_bot_and_handler
        res = handler.handle_command("/edges SUIUSDT", chat_id="999")
        assert "ORDER FLOW ALPHA EDGES — #SUIUSDT" in res
        assert "L5 OFI" in res
        assert "CVD Absorption" in res
        assert "Hawkes Cascade" in res
        assert "Iceberg / Ghost" in res
        assert "Spot Lead-Lag" in res
        assert "COIN-M Stress" in res
        assert "Variance Ratio" in res
        assert "Total Active Edges:" in res

    def test_command_5_edges_missing_or_invalid_symbol(self, setup_bot_and_handler) -> None:
        _, handler = setup_bot_and_handler
        res_no_arg = handler.handle_command("/edges", chat_id="999")
        assert "Please specify a valid symbol" in res_no_arg

        res_invalid = handler.handle_command("/edges FAKEPAIR", chat_id="999")
        assert "Please specify a valid symbol" in res_invalid

    def test_command_6_vetoes(self, setup_bot_and_handler) -> None:
        _, handler = setup_bot_and_handler
        res = handler.handle_command("/vetoes", chat_id="999")
        assert "INSTITUTIONAL VETO MATRIX STATUS" in res
        assert "Spoofing Quote-Flicker" in res
        assert "Flash-Crash Knife-Catch" in res
        assert "Macro Calendar Blackout" in res
        assert "Real-Time RSS News Shock" in res
        assert "Deribit Options Gamma Unwind" in res
        assert "US Spot ETF AP Window" in res
        assert "Matching Engine Jitter Veto" in res
        assert "DefiLlama Stablecoin Flow" in res
        assert "OVERALL STATUS:" in res

    def test_command_7_risk(self, setup_bot_and_handler) -> None:
        _, handler = setup_bot_and_handler
        res = handler.handle_command("/risk", chat_id="999")
        assert "SPECTRAL RISK & PORTFOLIO ALLOCATION" in res
        assert "Absorption Ratio (AR):" in res
        assert "Market Regime:" in res
        assert "Max Simultaneous Positions:" in res
        assert "Quarter-Kelly Cap:" in res

    def test_command_8_scan(self, setup_bot_and_handler) -> None:
        _, handler = setup_bot_and_handler
        res = handler.handle_command("/scan SOLUSDT", chat_id="999")
        assert "DIAGNOSTIC SCAN — #SOLUSDT" in res
        assert "Bayesian P(Win):" in res
        assert "Edge Alignment:" in res
        assert "Status:" in res

    def test_command_9_top_edges(self, setup_bot_and_handler) -> None:
        _, handler = setup_bot_and_handler
        res = handler.handle_command("/top_edges", chat_id="999")
        assert "TOP 3 ORDER FLOW ALPHA CANDIDATES" in res
        assert "1." in res
        assert "2." in res
        assert "3." in res

    def test_command_10_memory(self, setup_bot_and_handler) -> None:
        _, handler = setup_bot_and_handler
        res = handler.handle_command("/memory", chat_id="999")
        assert "PYTHON RUNTIME MEMORY PROFILE" in res
        assert "Process RSS Memory:" in res
        assert "Garbage Collection Objects:" in res
        assert "Active Ring Buffer Slots:" in res
        assert "Zero NumPy / SciPy" in res

    def test_command_11_last_signal(self, setup_bot_and_handler) -> None:
        bot, handler = setup_bot_and_handler
        # When no signal has fired yet
        res_empty = handler.handle_command("/last_signal", chat_id="999")
        assert "No signals dispatched since last restart" in res_empty

        # Populate a signal
        bot.last_signal_payload = {
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "win_probability": 0.865,
            "entry_price": 64250.0,
            "stop_loss": 63910.0,
            "risk_r": 340.0,
            "tp1": 64760.0,
            "tp2": 65202.0,
            "tp3": 65780.0,
            "kelly_fraction": 0.0125,
            "spectral_ar": 0.42,
            "risk_multiplier": 0.95,
            "z_scores": {"z_ofi": 2.85, "z_cvd": 2.30, "z_liq": 3.42},
        }
        res_sig = handler.handle_command("/last_signal", chat_id="999")
        assert "ARUN MARKET ALPHA SIGNAL" in res_sig
        assert "BTCUSDT" in res_sig

    def test_command_12_version(self, setup_bot_and_handler) -> None:
        _, handler = setup_bot_and_handler
        res = handler.handle_command("/version", chat_id="999")
        assert "VERSION & BUILD" in res
        assert "v8.0.0-institutional" in res
        assert "Git Commit:" in res
        assert "0 NumPy, 0 Pandas, 0 SciPy" in res

    def test_security_unauthorized_chat_id(self, setup_bot_and_handler) -> None:
        _, handler = setup_bot_and_handler
        res = handler.handle_command("/status", chat_id="hacker_123")
        assert "ACCESS DENIED" in res

    def test_unknown_command(self, setup_bot_and_handler) -> None:
        _, handler = setup_bot_and_handler
        res = handler.handle_command("/unknown_xyz", chat_id="999")
        assert "Unknown command" in res


class TestTelegramCommandPollerLifecycle:
    """Verify poller start and stop methods."""

    @pytest.mark.asyncio
    async def test_poller_start_and_stop(self) -> None:
        bot = SignalBotOrchestrator()
        handler = TelegramCommandHandler(bot, allowed_chat_id="999")
        mock_disp = MagicMock()
        mock_disp.send_message_http = AsyncMock()

        poller = TelegramCommandPoller(
            handler=handler,
            dispatcher=mock_disp,
            bot_token="test_token",
            allowed_chat_id="999",
        )
        assert poller._running is False
        poller.start()
        assert poller._running is True
        assert poller._task is not None

        await poller.stop()
        assert poller._running is False
        assert poller._task is None
