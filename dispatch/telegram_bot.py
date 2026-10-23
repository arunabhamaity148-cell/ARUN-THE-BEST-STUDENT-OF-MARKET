"""
dispatch/telegram_bot.py — Asynchronous Token-Bucket Telegram Dispatcher.

Dispatches formatted signals to Telegram with strict rate-limiting, HTML
parsing, and automatic HTTP 429 (Too Many Requests) back-off.

Rate Limits:
- Max 1 message per 1.05 seconds per chat.
- Max 18 messages per 60 seconds per group.
- asyncio.Queue(maxsize=100) buffer.

Strict Constraints:
- Zero NumPy / Pandas.
- Load TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID securely from environment variables.
- Use orjson for JSON payload serialization.
- __slots__ on all classes.
"""

import asyncio
import os
import time
from collections import deque
from typing import Any, Mapping

import aiohttp
import orjson

# ─────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────

TELEGRAM_API_BASE: str = "https://api.telegram.org"
MIN_MESSAGE_INTERVAL_S: float = 1.05      # 1 message per 1.05 seconds
MAX_MESSAGES_PER_MINUTE: int = 18         # 18 messages per 60 seconds
RATE_LIMIT_WINDOW_S: float = 60.0
MAX_QUEUE_SIZE: int = 100


# ─────────────────────────────────────────────────────────────────────
# HTML Signal Formatter
# ─────────────────────────────────────────────────────────────────────

def format_signal_html(signal: Mapping[str, Any]) -> str:
    """Format trade signal parameters into clean HTML for Telegram.

    Uses HTML formatting (bold <b>, code <code>) to avoid MarkdownV2 escaping bugs.
    Displays 11 institutional order flow edges, Spectral Risk AR, and Kelly sizing.
    """
    symbol = signal.get("symbol", "UNKNOWN")
    direction = signal.get("direction", "LONG").upper()
    dir_emoji = "🟢 LONG" if direction == "LONG" else "🔴 SHORT"

    p_win = signal.get("win_probability", 0.0) * 100.0
    entry = signal.get("entry_price", 0.0)
    sl = signal.get("stop_loss", 0.0)
    risk_r = signal.get("risk_r", abs(entry - sl))

    tp1 = signal.get("tp1", 0.0)
    tp2 = signal.get("tp2", 0.0)
    tp3 = signal.get("tp3", 0.0)
    kelly = signal.get("kelly_fraction", 0.0) * 100.0
    mult = signal.get("risk_multiplier", signal.get("risk_mult", 1.0))
    ar = signal.get("spectral_ar", signal.get("ar", 0.0))

    z_scores = signal.get("z_scores", {})

    lines = [
        "🚨 <b>ARUN MARKET ALPHA SIGNAL</b> 🚨",
        "",
        f"🪙 <b>Asset:</b> #{symbol} | <b>Direction:</b> {dir_emoji}",
        f"🎯 <b>Bayesian P(Win):</b> <code>{p_win:.1f}%</code> | <b>AR:</b> <code>{ar:.2f}</code>",
        "",
        f"💰 <b>Entry Price:</b> <code>${entry:,.2f}</code>",
        f"🛑 <b>Stop Loss:</b> <code>${sl:,.2f}</code> (Risk: <code>${risk_r:,.2f}</code>)",
        "",
        "🎯 <b>Targets:</b>",
        f"• <b>TP1 (40%):</b> <code>${tp1:,.2f}</code> (+1.5R)",
        f"• <b>TP2 (40%):</b> <code>${tp2:,.2f}</code> (+2.8R)",
        f"• <b>TP3 (20%):</b> <code>${tp3:,.2f}</code> (+4.5R)",
        "",
        "🛡️ <b>Vetoes:</b> CLEARED (0 Active)",
        f"⚖️ <b>Kelly Size:</b> <code>{kelly:.2f}%</code> (Mult: <code>{mult:.2f}</code>)",
    ]

    if z_scores:
        lines.append("")
        lines.append("🧠 <b>Edge Z-Scores:</b>")
        edge_names = {
            "z_ofi": "L5 OFI",
            "z_cvd": "CVD Absorption",
            "z_liq": "Liq Exhaustion",
            "z_oi": "OI & Basis",
            "z_smt": "SMT Divergence",
            "z_micro": "Micro-Price",
            "z_hawkes": "Hawkes Cascade",
            "z_iceberg": "Iceberg / Ghost",
            "z_lead_lag": "Spot Lead-Lag",
            "z_coinm_stress": "COIN-M Stress",
            "z_vr": "Variance Ratio",
        }
        for k, name in edge_names.items():
            if k in z_scores:
                val = z_scores[k]
                sign = "+" if val >= 0 else ""
                lines.append(f"• {name}: <code>{sign}{val:.2f}</code>")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────
# Telegram Signal Dispatcher Class
# ─────────────────────────────────────────────────────────────────────

class TelegramSignalDispatcher:
    """Token-bucket asynchronous Telegram queue worker with 429 retry back-off."""

    __slots__ = (
        "bot_token",
        "chat_id",
        "queue",
        "_session",
        "_own_session",
        "_worker_task",
        "_running",
        "_last_sent_time",
        "_sent_timestamps_60s",
        "_send_url",
    )

    def __init__(
        self,
        bot_token: str | None = None,
        chat_id: str | None = None,
        session: aiohttp.ClientSession | None = None,
        max_queue_size: int = MAX_QUEUE_SIZE,
    ) -> None:
        self.bot_token: str = bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id: str = chat_id or os.getenv("TELEGRAM_CHAT_ID", "")
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=max_queue_size)
        self._session: aiohttp.ClientSession | None = session
        self._own_session: bool = session is None
        self._worker_task: asyncio.Task | None = None
        self._running: bool = False
        self._last_sent_time: float = 0.0
        self._sent_timestamps_60s: deque = deque(maxlen=30)
        self._send_url: str = f"{TELEGRAM_API_BASE}/bot{self.bot_token}/sendMessage"

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or (isinstance(self._session.closed, bool) and self._session.closed):
            self._session = aiohttp.ClientSession()
            self._own_session = True
        return self._session

    async def enqueue_signal(self, signal: Mapping[str, Any] | str) -> bool:
        """Enqueue formatted HTML or raw signal dictionary for dispatch.

        Returns True if enqueued, False if queue is full.
        """
        html_msg = format_signal_html(signal) if isinstance(signal, Mapping) else str(signal)
        try:
            self.queue.put_nowait(html_msg)
            return True
        except asyncio.QueueFull:
            return False

    async def _wait_for_rate_limits(self) -> None:
        """Enforce 1.05s per-chat delay and 18 msgs / 60s group rate limits."""
        now = time.time()

        # 1. Per-chat minimum interval (1.05s)
        elapsed = now - self._last_sent_time
        if elapsed < MIN_MESSAGE_INTERVAL_S:
            await asyncio.sleep(MIN_MESSAGE_INTERVAL_S - elapsed)
            now = time.time()

        # 2. Group 60s rolling window (max 18 msgs)
        cutoff = now - RATE_LIMIT_WINDOW_S
        while self._sent_timestamps_60s and self._sent_timestamps_60s[0] < cutoff:
            self._sent_timestamps_60s.popleft()

        if len(self._sent_timestamps_60s) >= MAX_MESSAGES_PER_MINUTE:
            oldest = self._sent_timestamps_60s[0]
            wait_time = (oldest + RATE_LIMIT_WINDOW_S) - now + 0.05
            if wait_time > 0:
                await asyncio.sleep(wait_time)

    async def send_message_http(self, text: str) -> bool:
        """Send message via Telegram Bot API with HTTP 429 retry back-off."""
        if not self.bot_token or not self.chat_id:
            # Simulation / credentials not provided
            return False

        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
        }
        body_bytes = orjson.dumps(payload)
        headers = {"Content-Type": "application/json"}

        session = await self._get_session()
        max_retries = 3

        for _ in range(max_retries):
            try:
                await self._wait_for_rate_limits()
                async with session.post(self._send_url, data=body_bytes, headers=headers) as resp:
                    now = time.time()
                    if resp.status == 200:
                        self._last_sent_time = now
                        self._sent_timestamps_60s.append(now)
                        return True
                    elif resp.status == 429:
                        # Too Many Requests — read retry_after
                        raw = await resp.read()
                        data = orjson.loads(raw)
                        retry_after = float(data.get("parameters", {}).get("retry_after", 1.5))
                        await asyncio.sleep(retry_after + 0.1)
                        continue
                    else:
                        # Client/server error
                        return False
            except Exception:
                await asyncio.sleep(0.5)

        return False

    async def _worker_loop(self) -> None:
        """Background worker consuming messages from the queue."""
        while self._running:
            try:
                text = await self.queue.get()
                await self.send_message_http(text)
                self.queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(0.5)

    def start(self) -> None:
        """Launch the background queue worker task."""
        if not self._running:
            self._running = True
            self._worker_task = asyncio.create_task(self._worker_loop())

    async def stop(self) -> None:
        """Stop worker task and close session cleanly."""
        self._running = False
        if self._worker_task is not None:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None

        if self._own_session and self._session is not None and not self._session.closed:
            await self._session.close()

    def __repr__(self) -> str:
        return (
            f"TelegramSignalDispatcher(chat_id={self.chat_id}, "
            f"queue_size={self.queue.qsize()}, running={self._running})"
        )
