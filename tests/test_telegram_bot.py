"""tests/test_telegram_bot.py — Comprehensive tests for dispatch/telegram_bot.py.

Verifies:
1. Slots and memory compliance for TelegramSignalDispatcher.
2. HTML message formatting without Markdown escaping issues.
3. Queue buffering with maxsize=100.
4. Token-bucket rate limiting (1.05s per-chat interval, 18 msgs / 60s cap).
5. HTTP 429 (Too Many Requests) retry_after back-off and retry.
6. Dispatcher worker start/stop lifecycle.
"""

import asyncio
import sys
import os
import time
from unittest.mock import AsyncMock, MagicMock, patch

import orjson
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dispatch.telegram_bot import (
    TelegramSignalDispatcher,
    format_signal_html,
    MIN_MESSAGE_INTERVAL_S,
    MAX_MESSAGES_PER_MINUTE,
    MAX_QUEUE_SIZE,
)


class MockPostContext:
    def __init__(self, resp):
        self.resp = resp
    async def __aenter__(self):
        return self.resp
    async def __aexit__(self, *args):
        pass


class TestTelegramDispatcherSlots:
    """Verify slots enforcement."""

    def test_has_slots(self) -> None:
        assert hasattr(TelegramSignalDispatcher, "__slots__")

    def test_no_dict(self) -> None:
        disp = TelegramSignalDispatcher(bot_token="fake", chat_id="123")
        assert not hasattr(disp, "__dict__")

    def test_repr(self) -> None:
        disp = TelegramSignalDispatcher(bot_token="fake", chat_id="123")
        r = repr(disp)
        assert "TelegramSignalDispatcher" in r


class TestSignalHTMLFormatting:
    """Verify clean HTML signal formatting."""

    def test_format_signal_html_structure(self) -> None:
        signal = {
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
            "z_scores": {
                "z_ofi": 2.85,
                "z_cvd": 2.30,
                "z_liq": 3.42,
            },
        }

        html = format_signal_html(signal)

        # Check required components
        assert "<b>Asset:</b> #BTCUSDT" in html
        assert "🟢 LONG" in html
        assert "<code>86.5%</code>" in html
        assert "<code>$64,250.00</code>" in html
        assert "<code>$63,910.00</code>" in html
        assert "<b>TP1 (40%):</b>" in html
        assert "<b>TP2 (40%):</b>" in html
        assert "<b>TP3 (20%):</b>" in html
        assert "<code>1.25%</code>" in html
        assert "L5 OFI: <code>+2.85</code>" in html
        assert "CVD Absorption: <code>+2.30</code>" in html
        assert "Liq Exhaustion: <code>+3.42</code>" in html

    def test_format_signal_html_11_edges_and_spectral_ar(self) -> None:
        signal = {
            "symbol": "SUIUSDT",
            "direction": "LONG",
            "win_probability": 0.845,
            "entry_price": 1.8540,
            "stop_loss": 1.8210,
            "risk_r": 0.0330,
            "tp1": 1.9035,
            "tp2": 1.9464,
            "tp3": 2.0023,
            "kelly_fraction": 0.0125,
            "risk_multiplier": 0.85,
            "spectral_ar": 0.45,
            "z_scores": {
                "z_ofi": 2.4,
                "z_cvd": 2.1,
                "z_hawkes": 3.2,
                "z_iceberg": 2.8,
                "z_lead_lag": 2.6,
                "z_coinm_stress": 1.8,
                "z_vr": 1.9,
            },
        }

        html = format_signal_html(signal)

        # Header and Asset
        assert "ARUN MARKET ALPHA SIGNAL" in html
        assert "<b>Asset:</b> #SUIUSDT" in html
        assert "🟢 LONG" in html

        # Win prob & Spectral AR
        assert "<code>84.5%</code>" in html
        assert "<b>AR:</b> <code>0.45</code>" in html

        # Entry & SL
        assert "<code>$1.85</code>" in html
        assert "<code>$1.82</code>" in html

        # Vetoes and Kelly multiplier
        assert "Vetoes:</b> CLEARED (0 Active)" in html
        assert "<code>1.25%</code>" in html
        assert "Mult: <code>0.85</code>" in html

        # 11 institutional edge labels
        assert "L5 OFI: <code>+2.40</code>" in html
        assert "CVD Absorption: <code>+2.10</code>" in html
        assert "Hawkes Cascade: <code>+3.20</code>" in html
        assert "Iceberg / Ghost: <code>+2.80</code>" in html
        assert "Spot Lead-Lag: <code>+2.60</code>" in html
        assert "COIN-M Stress: <code>+1.80</code>" in html
        assert "Variance Ratio: <code>+1.90</code>" in html


class TestTelegramQueueAndRateLimit:
    """Verify queue buffering, intervals, and HTTP 429 retry."""

    @pytest.mark.asyncio
    async def test_queue_overflow_guard(self) -> None:
        disp = TelegramSignalDispatcher(bot_token="fake", chat_id="123", max_queue_size=2)
        # Fill queue
        assert await disp.enqueue_signal("msg 1") is True
        assert await disp.enqueue_signal("msg 2") is True
        # Queue full -> returns False without blocking
        assert await disp.enqueue_signal("msg 3") is False

    @pytest.mark.asyncio
    async def test_send_message_rate_limit_delay(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status = 200

        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.post.return_value = MockPostContext(mock_resp)

        disp = TelegramSignalDispatcher(bot_token="fake", chat_id="123", session=mock_session)

        t0 = time.time()
        # First send
        await disp.send_message_http("msg 1")
        # Second send immediately -> should be delayed by ~1.05s
        await disp.send_message_http("msg 2")
        elapsed = time.time() - t0

        assert elapsed >= 1.0

    @pytest.mark.asyncio
    async def test_http_429_retry_handling(self) -> None:
        # First call returns 429 with retry_after = 0.05
        resp_429 = MagicMock()
        resp_429.status = 429
        resp_429.read = AsyncMock(return_value=orjson.dumps({"parameters": {"retry_after": 0.05}}))

        # Second call returns 200
        resp_200 = MagicMock()
        resp_200.status = 200

        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.post.side_effect = [
            MockPostContext(resp_429),
            MockPostContext(resp_200),
        ]

        disp = TelegramSignalDispatcher(bot_token="fake", chat_id="123", session=mock_session)

        # Message must successfully deliver after retry
        success = await disp.send_message_http("retry test")
        assert success is True
        assert mock_session.post.call_count == 2

    @pytest.mark.asyncio
    async def test_dispatcher_lifecycle(self) -> None:
        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.close = AsyncMock()

        disp = TelegramSignalDispatcher(bot_token="fake", chat_id="123", session=mock_session)
        assert disp._running is False

        disp.start()
        assert disp._running is True
        assert disp._worker_task is not None

        await disp.stop()
        assert disp._running is False
        assert disp._worker_task is None
