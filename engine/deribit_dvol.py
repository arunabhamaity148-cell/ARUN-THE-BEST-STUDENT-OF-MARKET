"""
engine/deribit_dvol.py — Deribit DVOL Options Gamma Unwind Veto (Phase 6D).

Architecture
------------
Connects to Deribit WebSocket v2 (wss://www.deribit.com/ws/api/v2) and subscribes
to BTC and ETH volatility indexes:
  - deribit_volatility_index.btc_usd
  - deribit_volatility_index.eth_usd

Logic:
  - Maintain btc_dvol_3m_ring (maxlen=180, 1-second ticks).
  - Calculate 3-minute Rate of Change:
      roc_pct = ((current_dvol - ring[0]) / ring[0]) * 100
  - If roc_pct >= 3.5%:
      gamma_unwind_until = time.monotonic() + 600.0 (10-minute veto).
  - Veto Method: is_gamma_unwind_active() -> bool

Strict Constraints:
  - Zero numpy / pandas / scipy.
  - websockets client with exponential back-off.
  - __slots__ on all classes.
"""

import asyncio
import logging
import time
from collections import deque
from typing import Callable, Awaitable

import orjson

try:
    import websockets
    import websockets.asyncio.client as ws_client
except ImportError:
    websockets = None        # type: ignore[assignment]
    ws_client = None         # type: ignore[assignment]

logger = logging.getLogger("deribit_dvol")

DERIBIT_WS_URL: str = "wss://www.deribit.com/ws/api/v2"
DVOL_RING_WINDOW: int = 180     # 3 minutes at 1 tick/second
DVOL_ROC_THRESHOLD: float = 3.5 # +3.5% spike triggers gamma unwind veto
GAMMA_UNWIND_DURATION: float = 600.0  # 10 minutes in seconds


# ─────────────────────────────────────────────────────────────────────
# Pure Helper Function
# ─────────────────────────────────────────────────────────────────────

def compute_dvol_roc(current_dvol: float, baseline_dvol: float) -> float:
    """Calculate percentage rate of change (ROC) for volatility index."""
    if baseline_dvol <= 0.0:
        return 0.0
    return ((current_dvol - baseline_dvol) / baseline_dvol) * 100.0


# ─────────────────────────────────────────────────────────────────────
# Deribit DVOL Veto Engine
# ─────────────────────────────────────────────────────────────────────

class DeribitDVOLVeto:
    """Monitors Deribit DVOL (implied volatility index) for rapid spikes
    indicating dealer gamma hedging scrambles and liquidity cascades.
    """

    __slots__ = (
        "ws_url",
        "btc_dvol_3m_ring",
        "eth_dvol_3m_ring",
        "gamma_unwind_until",
        "latest_btc_dvol",
        "latest_eth_dvol",
        "latest_roc",
        "_running",
        "_task",
    )

    def __init__(self, ws_url: str = DERIBIT_WS_URL) -> None:
        self.ws_url: str = ws_url
        self.btc_dvol_3m_ring: deque[float] = deque(maxlen=DVOL_RING_WINDOW)
        self.eth_dvol_3m_ring: deque[float] = deque(maxlen=DVOL_RING_WINDOW)
        self.gamma_unwind_until: float = 0.0
        self.latest_btc_dvol: float = 0.0
        self.latest_eth_dvol: float = 0.0
        self.latest_roc: float = 0.0
        self._running: bool = False
        self._task: asyncio.Task | None = None

    # ── Core Update Logic ─────────────────────────────────────────────

    def update_dvol(
        self,
        dvol: float,
        channel: str = "btc",
        now_mono: float | None = None,
    ) -> tuple[float, bool]:
        """Update rolling DVOL, compute 3m ROC, and activate veto if spike detected.

        Parameters
        ----------
        dvol : float
            Current DVOL reading (e.g. 52.4).
        channel : str
            "btc" or "eth".
        now_mono : float | None
            Monotonic timestamp for testing / determinism.

        Returns
        -------
        roc_pct : float
            3-minute Rate of Change in percentage.
        triggered : bool
            True if gamma unwind veto was triggered.
        """
        now = time.monotonic() if now_mono is None else now_mono
        ring = self.btc_dvol_3m_ring if "btc" in channel.lower() else self.eth_dvol_3m_ring

        if "btc" in channel.lower():
            self.latest_btc_dvol = dvol
        else:
            self.latest_eth_dvol = dvol

        roc_pct = 0.0
        if len(ring) >= 1:
            baseline = ring[0]
            roc_pct = compute_dvol_roc(dvol, baseline)
            self.latest_roc = roc_pct

            # Threshold: >= 3.5% spike triggers 10m veto
            if roc_pct >= DVOL_ROC_THRESHOLD:
                self.gamma_unwind_until = max(self.gamma_unwind_until, now + GAMMA_UNWIND_DURATION)
                logger.warning(
                    "DERIBIT DVOL GAMMA UNWIND DETECTED! Channel=%s DVOL=%.2f ROC=%.2f%% (Veto for 10m)",
                    channel, dvol, roc_pct,
                )

        ring.append(dvol)
        triggered = self.is_gamma_unwind_active(now_mono=now)
        return roc_pct, triggered

    def process_message(self, data: dict, now_mono: float | None = None) -> None:
        """Parse raw Deribit WS JSON-RPC notification."""
        params = data.get("params", {})
        channel = params.get("channel", "")
        payload = params.get("data", {})

        dvol_val = None
        if isinstance(payload, (int, float)):
            dvol_val = float(payload)
        elif isinstance(payload, dict):
            for key in ("volatility", "dvol", "price", "val", "value"):
                if key in payload and payload[key] is not None:
                    dvol_val = float(payload[key])
                    break

        if dvol_val is not None:
            self.update_dvol(dvol_val, channel=channel, now_mono=now_mono)

    # ── Veto Checker ──────────────────────────────────────────────────

    def is_gamma_unwind_active(self, now_mono: float | None = None) -> bool:
        """Return True if options gamma unwind veto is currently active."""
        now = time.monotonic() if now_mono is None else now_mono
        return now < self.gamma_unwind_until

    # ── WebSocket Lifecycle ───────────────────────────────────────────

    async def _listen(self) -> None:
        """Listen to Deribit WebSocket with exponential back-off."""
        if ws_client is None:
            logger.warning("websockets client not available — Deribit DVOL WS disabled")
            return

        delay = 1.0
        sub_msg = orjson.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "public/subscribe",
            "params": {
                "channels": [
                    "deribit_volatility_index.btc_usd",
                    "deribit_volatility_index.eth_usd",
                ]
            },
        })

        while self._running:
            try:
                async with ws_client.connect(
                    self.ws_url,
                    ping_interval=20,
                    ping_timeout=10,
                ) as ws:
                    logger.info("Connected to Deribit DVOL WS → %s", self.ws_url)
                    delay = 1.0
                    await ws.send(sub_msg.decode("utf-8"))

                    async for raw in ws:
                        if not self._running:
                            break
                        data = orjson.loads(raw)
                        self.process_message(data)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                if not self._running:
                    break
                logger.warning("Deribit DVOL WS disconnected (%s), retry in %.1fs", exc, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2.0, 60.0)

    def start(self) -> None:
        """Start Deribit WebSocket background listener."""
        self._running = True
        self._task = asyncio.create_task(self._listen(), name="ws-deribit-dvol")

    async def stop(self) -> None:
        """Stop Deribit WebSocket listener cleanly."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    def __repr__(self) -> str:
        active = self.is_gamma_unwind_active()
        return (
            f"DeribitDVOLVeto(active={active}, "
            f"BTC_DVOL={self.latest_btc_dvol:.2f}, ROC={self.latest_roc:.2f}%)"
        )
