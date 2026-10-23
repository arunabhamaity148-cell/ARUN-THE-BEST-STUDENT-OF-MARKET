"""
core/state.py — In-memory microstructure state per symbol.

Uses __slots__ everywhere and collections.deque for bounded buffers
to stay well under the 150 MB memory target.

Includes Welford's online algorithm for streaming z-score calculation.
"""

import math
from array import array
from collections import deque

from config import (
    TRADE_WINDOW, DEPTH_WINDOW, SPREAD_WINDOW,
    OFI_WINDOW, LIQ_EVENT_WINDOW, ABSORPTION_SNAP_WINDOW,
    TRADE_DELTA_WINDOW,
    SPOT_RING_WINDOW, COINM_OFI_WINDOW, JITTER_WINDOW,
)


# ─────────────────────────────────────────────────────────────────────
# Welford's Online Normalizer
# ─────────────────────────────────────────────────────────────────────

class OnlineNormalizer:
    """Streaming mean / variance / z-score via Welford's algorithm.

    Numerically stable, O(1) per update, zero allocations.

    Reference
    ---------
    Welford, B.P. (1962). "Note on a method for calculating corrected
    sums of squares and products". *Technometrics*, 4(3), 419–420.
    """

    __slots__ = ("_count", "_mean", "_m2")

    def __init__(self) -> None:
        self._count: int = 0
        self._mean: float = 0.0
        self._m2: float = 0.0          # sum of squared differences

    # ── public API ──────────────────────────────────────────────────

    def update(self, value: float) -> None:
        """Incorporate a new observation."""
        self._count += 1
        delta = value - self._mean
        self._mean += delta / self._count
        delta2 = value - self._mean     # uses updated mean
        self._m2 += delta * delta2

    @property
    def count(self) -> int:
        return self._count

    @property
    def mean(self) -> float:
        return self._mean

    @property
    def variance(self) -> float:
        """Population variance (σ²)."""
        if self._count < 2:
            return 0.0
        return self._m2 / self._count

    @property
    def std_dev(self) -> float:
        """Population standard deviation (σ)."""
        return math.sqrt(self.variance)

    def z_score(self, value: float) -> float:
        """Return the z-score of *value* w.r.t. current statistics.

        Returns 0.0 when insufficient data (< 2 samples) or zero
        variance, preventing divide-by-zero.
        """
        sd = self.std_dev
        if sd == 0.0:
            return 0.0
        return (value - self._mean) / sd

    def reset(self) -> None:
        """Clear all accumulated statistics."""
        self._count = 0
        self._mean = 0.0
        self._m2 = 0.0

    def __repr__(self) -> str:
        return (
            f"OnlineNormalizer(count={self._count}, "
            f"mean={self._mean:.6f}, std={self.std_dev:.6f})"
        )


# ─────────────────────────────────────────────────────────────────────
# Per-Symbol Microstructure State
# ─────────────────────────────────────────────────────────────────────

class SymbolMicroState:
    """Live microstructure snapshot for a single futures symbol.

    All rolling buffers are bounded deques so memory stays constant
    regardless of uptime.
    """

    __slots__ = (
        "symbol",
        # ── top-of-book ──
        "best_bid",
        "best_bid_qty",
        "best_ask",
        "best_ask_qty",
        # ── cumulative volume delta ──
        "cvd",
        # ── last trade info ──
        "last_price",
        "last_trade_time",
        # ── rolling buffers ──
        "trade_prices",
        "trade_volumes",
        "bid_depths",
        "ask_depths",
        "spread_history",
        # ── online normalizers ──
        "cvd_normalizer",
        "spread_normalizer",
        "volume_normalizer",
        # ── L5 depth levels (Phase 2) ──
        "prev_bid_prices",
        "prev_bid_qtys",
        "prev_ask_prices",
        "prev_ask_qtys",
        # ── OFI tracking (Phase 2) ──
        "ofi_history",
        "ofi_sign_streak",
        "last_ofi_sign",
        # ── trade CVD deltas (Phase 2) ──
        "trade_deltas",
        # ── liquidation tracking (Phase 2) ──
        "liq_events",
        "liq_normalizer",
        "liq_velocity_peak",
        # ── absorption divergence (Phase 2) ──
        "absorption_snapshots",
        "last_absorption_snap_time",
        # ── Phase 6: Spot, COIN-M & Jitter Rings ──
        "spot_cvd_lm",
        "spot_price_ring",
        "coin_m_ofi_ring",
        "matching_engine_jitter_ring",
    )

    def __init__(self, symbol: str) -> None:
        self.symbol: str = symbol

        # top-of-book
        self.best_bid: float = 0.0
        self.best_bid_qty: float = 0.0
        self.best_ask: float = 0.0
        self.best_ask_qty: float = 0.0

        # cumulative volume delta (buy vol − sell vol)
        self.cvd: float = 0.0

        # last trade
        self.last_price: float = 0.0
        self.last_trade_time: int = 0

        # bounded rolling buffers
        self.trade_prices: deque = deque(maxlen=TRADE_WINDOW)
        self.trade_volumes: deque = deque(maxlen=TRADE_WINDOW)
        self.bid_depths: deque = deque(maxlen=DEPTH_WINDOW)
        self.ask_depths: deque = deque(maxlen=DEPTH_WINDOW)
        self.spread_history: deque = deque(maxlen=SPREAD_WINDOW)

        # streaming normalizers for z-scores
        self.cvd_normalizer: OnlineNormalizer = OnlineNormalizer()
        self.spread_normalizer: OnlineNormalizer = OnlineNormalizer()
        self.volume_normalizer: OnlineNormalizer = OnlineNormalizer()

        # L5 depth levels — C-double arrays for memory efficiency
        self.prev_bid_prices: array = array("d", [0.0] * 5)
        self.prev_bid_qtys: array = array("d", [0.0] * 5)
        self.prev_ask_prices: array = array("d", [0.0] * 5)
        self.prev_ask_qtys: array = array("d", [0.0] * 5)

        # OFI tracking
        self.ofi_history: deque = deque(maxlen=OFI_WINDOW)
        self.ofi_sign_streak: int = 0
        self.last_ofi_sign: int = 0

        # per-trade CVD deltas for direction analysis
        self.trade_deltas: deque = deque(maxlen=TRADE_DELTA_WINDOW)

        # liquidation tracking
        self.liq_events: deque = deque(maxlen=LIQ_EVENT_WINDOW)
        self.liq_normalizer: OnlineNormalizer = OnlineNormalizer()
        self.liq_velocity_peak: float = 0.0

        # absorption divergence snapshots  (timestamp_ms, price, cvd)
        self.absorption_snapshots: deque = deque(maxlen=ABSORPTION_SNAP_WINDOW)
        self.last_absorption_snap_time: int = 0

        # Phase 6: Spot and COIN-M rolling deques & Jitter ring (max 20)
        self.spot_cvd_lm: deque = deque(maxlen=SPOT_RING_WINDOW)
        self.spot_price_ring: deque = deque(maxlen=SPOT_RING_WINDOW)
        self.coin_m_ofi_ring: deque = deque(maxlen=COINM_OFI_WINDOW)
        self.matching_engine_jitter_ring: deque = deque(maxlen=JITTER_WINDOW)

    # ── convenience properties ──────────────────────────────────────

    @property
    def spot_cvd_1m(self) -> deque:
        """Alias for spot_cvd_lm supporting both 1m and lm conventions."""
        return self.spot_cvd_lm

    @spot_cvd_1m.setter
    def spot_cvd_1m(self, value: deque) -> None:
        self.spot_cvd_lm = value

    # ── convenience mutators ────────────────────────────────────────

    def update_book_ticker(
        self,
        bid: float,
        bid_qty: float,
        ask: float,
        ask_qty: float,
    ) -> None:
        """Update top-of-book from a bookTicker event."""
        self.best_bid = bid
        self.best_bid_qty = bid_qty
        self.best_ask = ask
        self.best_ask_qty = ask_qty

        spread = ask - bid
        self.spread_history.append(spread)
        self.spread_normalizer.update(spread)

    def update_trade(
        self,
        price: float,
        qty: float,
        is_buyer_maker: bool,
        trade_time: int,
        event_time: int | None = None,
    ) -> None:
        """Update state from an aggTrade event.

        Parameters
        ----------
        is_buyer_maker : bool
            True  → seller-initiated (taker sold into bid)
            False → buyer-initiated (taker bought at ask)
        trade_time : int
            Matching engine timestamp T.
        event_time : int | None
            WebSocket event timestamp E (tracks E - T jitter).
        """
        self.last_price = price
        self.last_trade_time = trade_time

        self.trade_prices.append(price)
        self.trade_volumes.append(qty)

        # CVD: buyer-initiated adds, seller-initiated subtracts
        delta = -qty if is_buyer_maker else qty
        self.cvd += delta
        self.trade_deltas.append((trade_time, delta))

        # record absorption snapshot at ~1 s intervals
        if trade_time - self.last_absorption_snap_time >= 1000:
            self.absorption_snapshots.append((trade_time, price, self.cvd))
            self.last_absorption_snap_time = trade_time

        self.cvd_normalizer.update(self.cvd)
        self.volume_normalizer.update(qty)

        # Track matching engine jitter E - T if event_time provided
        if event_time is not None:
            self.record_jitter(event_time, trade_time)

    def update_depth(
        self,
        total_bid_depth: float,
        total_ask_depth: float,
    ) -> None:
        """Update rolling depth sums from a depth20 snapshot."""
        self.bid_depths.append(total_bid_depth)
        self.ask_depths.append(total_ask_depth)

    # ── Phase 2 mutators ──────────────────────────────────────────────

    def add_liquidation(self, timestamp_ms: int, notional: float) -> None:
        """Record a forced-liquidation event."""
        self.liq_events.append((timestamp_ms, notional))

    # ── Phase 6 mutators ──────────────────────────────────────────────

    def record_jitter(self, event_time: float, trade_time: float) -> float:
        """Record matching engine latency jitter (E - T)."""
        jitter = float(event_time - trade_time)
        self.matching_engine_jitter_ring.append(jitter)
        return jitter

    def update_jitter(self, jitter: float) -> None:
        """Record pre-computed jitter float into ring."""
        self.matching_engine_jitter_ring.append(float(jitter))

    def update_spot_trade(
        self,
        price: float,
        qty: float,
        is_buyer_maker: bool,
        trade_time: int,
    ) -> None:
        """Update spot price and spot CVD."""
        self.spot_price_ring.append(price)
        delta = -qty if is_buyer_maker else qty
        self.spot_cvd_lm.append((trade_time, delta))

    def update_coin_m_ofi(self, ofi_val: float) -> None:
        """Record COIN-M OFI reading."""
        self.coin_m_ofi_ring.append(float(ofi_val))

    # ── derived metrics ─────────────────────────────────────────────

    @property
    def spread(self) -> float:
        """Current spread (ask − bid)."""
        return self.best_ask - self.best_bid

    @property
    def mid_price(self) -> float:
        """Mid-price of current top-of-book."""
        return (self.best_bid + self.best_ask) / 2.0

    @property
    def cvd_z(self) -> float:
        """Z-score of latest CVD reading."""
        return self.cvd_normalizer.z_score(self.cvd)

    @property
    def spread_z(self) -> float:
        """Z-score of latest spread."""
        return self.spread_normalizer.z_score(self.spread)

    def __repr__(self) -> str:
        return (
            f"SymbolMicroState({self.symbol}, "
            f"bid={self.best_bid}, ask={self.best_ask}, "
            f"cvd={self.cvd:.4f}, trades={len(self.trade_prices)})"
        )
