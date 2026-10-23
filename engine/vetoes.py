"""
engine/vetoes.py — Risk-management vetoes for false-positive suppression.

Two guards that prevent the engine from acting on spoofed or
crash-related microstructure anomalies.

1. **Spoofing Quote-Flicker Veto** — requires OFI persistence AND
   CVD corroboration before any signal is trusted.
2. **Flash-Crash Knife-Catch Veto** — bars counter-trend mean-reversion
   while the liquidation cascade is still accelerating.

Both classes use ``__slots__`` and operate purely on the in-memory
``SymbolMicroState`` instances.
"""


from core.state import OnlineNormalizer

# ─────────────────────────────────────────────────────────────────────
# Pre-computed Constants
# ─────────────────────────────────────────────────────────────────────

OFI_MIN_PERSISTENCE: int = 5             # 5 × 100 ms = 500 ms minimum
CVD_LOOKBACK_MS: int = 500               # window for CVD direction check
FLASH_CRASH_DECEL_THRESHOLD: float = 0.6  # 60 % deceleration to unlock
LIQ_VELOCITY_WINDOW_MS: int = 10_000      # 10-second rolling window

# Phase 3 Veto Constants
DELEVERAGING_OI_THRESHOLD: float = -2.5    # OI contracting by > 2.5%
NEUTRAL_FUNDING_MAX: float = 0.0001        # ±0.01% in decimal
DELEVERAGING_ZLIQ_OVERRIDE: float = 3.5    # Z_liq > 3.5 overrides deleveraging veto
FUNDING_BOUNDARY_MS: int = 5 * 60 * 1000   # ±5 minutes around settlement
EXTREME_FUNDING_THRESHOLD: float = 0.0005  # ±0.05% in decimal

# Phase 4 Veto Constants
MAX_SPREAD_BPS_THRESHOLD: float = 3.5      # max allowed spread in bps
DEPTH_VACUUM_STD_MULT: float = 1.5         # depth drop threshold: mean - 1.5 * std


# ─────────────────────────────────────────────────────────────────────
# 1.  Spoofing Quote-Flicker Veto
# ─────────────────────────────────────────────────────────────────────

class SpoofingQuoteFlickerVeto:
    """Veto OFI anomalies that are not corroborated by taker flow.

    An OFI signal is vetoed when **either** condition fails:

    1. The OFI sign has not persisted for at least
       ``min_persistence`` consecutive 100 ms depth updates (≥ 500 ms).
    2. The realised ``@aggTrade`` taker flow (short-term CVD direction)
       does not align with the OFI sign.

    Parameters
    ----------
    min_persistence : int
        Minimum consecutive same-sign OFI updates required.
        Default ``5`` (500 ms at 100 ms per update).
    cvd_lookback_ms : int
        Millisecond window over which to measure CVD direction.
    """

    __slots__ = ("_min_persistence", "_cvd_lookback_ms")

    def __init__(
        self,
        min_persistence: int = OFI_MIN_PERSISTENCE,
        cvd_lookback_ms: int = CVD_LOOKBACK_MS,
    ) -> None:
        self._min_persistence = min_persistence
        self._cvd_lookback_ms = cvd_lookback_ms

    # ── helpers ─────────────────────────────────────────────────────

    def _recent_cvd_direction(self, state, now_ms: int) -> int:
        """Return +1 / −1 / 0 for net CVD direction over lookback window."""
        cutoff = now_ms - self._cvd_lookback_ms
        net = 0.0
        for ts, delta in state.trade_deltas:
            if ts >= cutoff:
                net += delta
        if net > 0.0:
            return 1
        if net < 0.0:
            return -1
        return 0

    # ── public API ──────────────────────────────────────────────────

    def should_veto(
        self,
        state,
        ofi: float,
        now_ms: int,
    ) -> bool:
        """Return ``True`` if the OFI signal should be **vetoed**.

        Parameters
        ----------
        state : SymbolMicroState
        ofi : float
            Current L5 OFI value.
        now_ms : int
            Current timestamp in milliseconds.
        """
        ofi_sign = 1 if ofi > 0 else (-1 if ofi < 0 else 0)
        if ofi_sign == 0:
            return True            # no directional signal

        # Rule 1: persistence check
        if state.ofi_sign_streak < self._min_persistence:
            return True

        # Rule 2: CVD corroboration
        cvd_dir = self._recent_cvd_direction(state, now_ms)
        if cvd_dir != ofi_sign:
            return True

        return False

    def __repr__(self) -> str:
        return (
            f"SpoofingQuoteFlickerVeto("
            f"min_persistence={self._min_persistence}, "
            f"cvd_lookback_ms={self._cvd_lookback_ms})"
        )


# ─────────────────────────────────────────────────────────────────────
# 2.  Flash-Crash Knife-Catch Veto
# ─────────────────────────────────────────────────────────────────────

class FlashCrashKnifeCatchVeto:
    """Veto counter-trend signals while liquidations are accelerating.

    A counter-trend (mean-reversion) signal is **barred** when:

    - The 10-second rolling liquidation notional velocity
      ``dV_liq / dt`` is **positive** (still accelerating).
    - The velocity has **not yet** decelerated by > 60 % from its
      local cascade peak.

    The veto auto-resets once the cascade subsides (velocity drops
    below 40 % of the peak).

    Parameters
    ----------
    decel_threshold : float
        Fractional deceleration required to unlock signals (0.6 = 60 %).
    velocity_window_ms : int
        Rolling window for the liquidation velocity measurement.
    """

    __slots__ = ("_decel_threshold", "_velocity_window_ms")

    def __init__(
        self,
        decel_threshold: float = FLASH_CRASH_DECEL_THRESHOLD,
        velocity_window_ms: int = LIQ_VELOCITY_WINDOW_MS,
    ) -> None:
        self._decel_threshold = decel_threshold
        self._velocity_window_ms = velocity_window_ms

    # ── helpers ─────────────────────────────────────────────────────

    def compute_liq_velocity(self, state, now_ms: int) -> float:
        """10-second rolling liquidation notional (acts as velocity proxy).

        Since we sample at roughly fixed intervals, total notional
        within the window is proportional to velocity.
        """
        cutoff = now_ms - self._velocity_window_ms
        total = 0.0
        for ts, notional in state.liq_events:
            if ts >= cutoff:
                total += notional
        return total

    # ── public API ──────────────────────────────────────────────────

    def should_veto(
        self,
        state,
        is_counter_trend: bool,
        now_ms: int,
    ) -> bool:
        """Return ``True`` if the counter-trend signal should be **vetoed**.

        Parameters
        ----------
        state : SymbolMicroState
        is_counter_trend : bool
            True when the proposed signal is a mean-reversion / counter-trend.
        now_ms : int
            Current timestamp in milliseconds.
        """
        if not is_counter_trend:
            return False           # only veto counter-trend signals

        velocity = self.compute_liq_velocity(state, now_ms)

        # update peak tracker
        if velocity > state.liq_velocity_peak:
            state.liq_velocity_peak = velocity

        peak = state.liq_velocity_peak
        if peak <= 0.0:
            return False           # no cascade on record → allow

        # deceleration ratio: how much velocity has dropped from peak
        decel_ratio = 1.0 - (velocity / peak)

        if decel_ratio >= self._decel_threshold:
            # cascade has subsided — reset peak and allow
            state.liq_velocity_peak = 0.0
            return False

        # still accelerating or not decelerated enough → veto
        return True

    def __repr__(self) -> str:
        return (
            f"FlashCrashKnifeCatchVeto("
            f"decel_threshold={self._decel_threshold}, "
            f"velocity_window_ms={self._velocity_window_ms})"
        )


# ─────────────────────────────────────────────────────────────────────
# 3.  Phase 3: Deleveraging Veto
# ─────────────────────────────────────────────────────────────────────

class DeleveragingVeto:
    """Veto mean-reversion long signals during spot/perp deleveraging.

    Condition
    ---------
    If 15m price decline + contracting OI (delta_oi_pct < -2.5%) +
    neutral funding (|FR| <= 0.01% / 0.0001), veto mean-reversion longs,
    UNLESS Z_liq > 3.5 (liquidation cascade exhaustion overrides).
    """

    __slots__ = (
        "_oi_threshold",
        "_neutral_funding_max",
        "_zliq_override",
    )

    def __init__(
        self,
        oi_threshold: float = DELEVERAGING_OI_THRESHOLD,
        neutral_funding_max: float = NEUTRAL_FUNDING_MAX,
        zliq_override: float = DELEVERAGING_ZLIQ_OVERRIDE,
    ) -> None:
        self._oi_threshold = oi_threshold
        self._neutral_funding_max = neutral_funding_max
        self._zliq_override = zliq_override

    def should_veto(
        self,
        is_long: bool,
        is_counter_trend: bool,
        price_change_pct: float,
        delta_oi_pct: float,
        funding_rate: float,
        z_liq: float = 0.0,
    ) -> bool:
        """Return True if the mean-reversion long signal should be vetoed.

        Parameters
        ----------
        is_long : bool
            True if signal is buying/long.
        is_counter_trend : bool
            True if signal is mean-reversion / counter-trend.
        price_change_pct : float
            15-minute price return (negative = decline).
        delta_oi_pct : float
            Open interest change percentage.
        funding_rate : float
            Current funding rate (decimal or percentage).
        z_liq : float
            Current liquidation cascade exhaustion z-score.
        """
        # Only applies to mean-reversion long signals
        if not (is_long and is_counter_trend):
            return False

        # Cascade exhaustion override: massive liquidation capitulation allows catch
        if z_liq > self._zliq_override:
            return False

        # Check normalize funding rate
        fr = funding_rate / 100.0 if abs(funding_rate) >= 0.005 else funding_rate

        is_price_decline = price_change_pct < 0.0
        is_contracting_oi = delta_oi_pct < self._oi_threshold
        is_neutral_funding = abs(fr) <= self._neutral_funding_max

        return is_price_decline and is_contracting_oi and is_neutral_funding

    def __repr__(self) -> str:
        return (
            f"DeleveragingVeto(oi_threshold={self._oi_threshold}, "
            f"neutral_funding_max={self._neutral_funding_max}, "
            f"zliq_override={self._zliq_override})"
        )


# ─────────────────────────────────────────────────────────────────────
# 4.  Phase 3: Funding Settlement Boundary Veto
# ─────────────────────────────────────────────────────────────────────

class FundingSettlementBoundaryVeto:
    """Suppress signals requiring paying extreme funding near settlement.

    Condition
    ---------
    Inspect nextFundingTime from premiumIndex. Suppress signals within
    ±5 minutes of settlement when the position would pay extreme funding
    (e.g., long signal when funding > +0.05%, or short when funding < -0.05%).
    """

    __slots__ = (
        "_extreme_funding_threshold",
        "_boundary_ms",
    )

    def __init__(
        self,
        extreme_funding_threshold: float = EXTREME_FUNDING_THRESHOLD,
        boundary_ms: int = FUNDING_BOUNDARY_MS,
    ) -> None:
        self._extreme_funding_threshold = extreme_funding_threshold
        self._boundary_ms = boundary_ms

    def should_veto(
        self,
        is_long: bool,
        funding_rate: float,
        next_funding_time_ms: int,
        now_ms: int,
    ) -> bool:
        """Return True if signal would incur punitive funding at settlement boundary.

        Parameters
        ----------
        is_long : bool
            True for long signal, False for short signal.
        funding_rate : float
            Current funding rate (decimal or percentage).
        next_funding_time_ms : int
            Next funding settlement timestamp in milliseconds.
        now_ms : int
            Current timestamp in milliseconds.
        """
        if next_funding_time_ms <= 0:
            return False

        # Check if within ±5 minutes of boundary
        time_to_settlement = abs(now_ms - next_funding_time_ms)
        if time_to_settlement > self._boundary_ms:
            return False

        # Normalize funding rate to decimal
        fr = funding_rate / 100.0 if abs(funding_rate) >= 0.005 else funding_rate

        # Longs pay funding when funding_rate > 0
        if is_long and fr > self._extreme_funding_threshold:
            return True

        # Shorts pay funding when funding_rate < 0
        if (not is_long) and fr < -self._extreme_funding_threshold:
            return True

        return False

    def __repr__(self) -> str:
        return (
            f"FundingSettlementBoundaryVeto("
            f"extreme_threshold={self._extreme_funding_threshold}, "
            f"boundary_ms={self._boundary_ms})"
        )


# ─────────────────────────────────────────────────────────────────────
# 5.  Phase 4: Liquidity Vacuum Veto
# ─────────────────────────────────────────────────────────────────────

def calculate_spread_bps(bid: float, ask: float) -> float:
    """Calculate spread in basis points: ((ask - bid) / mid) * 10000."""
    mid = (ask + bid) / 2.0
    if mid <= 0.0:
        return 0.0
    return ((ask - bid) / mid) * 10000.0


def compute_top5_depth_notional(bids: list, asks: list) -> float:
    """Sum notional depth across the top 5 bid and ask levels."""
    d5 = 0.0
    for i in range(min(5, len(bids))):
        d5 += float(bids[i][0]) * float(bids[i][1])
    for i in range(min(5, len(asks))):
        d5 += float(asks[i][0]) * float(asks[i][1])
    return d5


class LiquidityVacuumVeto:
    """Vetoes signals during order book liquidity vacuums or spread blowouts.

    Rules:
    1. Spread Veto: S_bps = ((ask - bid) / mid) * 10000. If S_bps > 3.5, veto.
    2. Depth Veto: D_5 < (mean_D5 - 1.5 * std_D5), veto (protects against thin book).
    """

    __slots__ = (
        "_max_spread_bps",
        "_std_mult",
        "_depth_normalizer",
    )

    def __init__(
        self,
        max_spread_bps: float = MAX_SPREAD_BPS_THRESHOLD,
        depth_std_mult: float = DEPTH_VACUUM_STD_MULT,
    ) -> None:
        self._max_spread_bps: float = max_spread_bps
        self._std_mult: float = depth_std_mult
        self._depth_normalizer: OnlineNormalizer = OnlineNormalizer()

    def update_depth(self, d5: float) -> None:
        """Incorporate a new D_5 reading into the streaming normalizer."""
        self._depth_normalizer.update(d5)

    def should_veto_spread(self, bid: float, ask: float) -> bool:
        """Return True if current spread in bps exceeds threshold (3.5 bps)."""
        s_bps = calculate_spread_bps(bid, ask)
        return s_bps > self._max_spread_bps

    def should_veto_depth(self, d5: float) -> bool:
        """Return True if D_5 falls below mean - 1.5 * std."""
        if self._depth_normalizer.count < 2:
            return False

        std = self._depth_normalizer.std_dev
        if std < 1e-12:
            return False

        vacuum_threshold = self._depth_normalizer.mean - (self._std_mult * std)
        return d5 < vacuum_threshold

    def should_veto(self, bid: float, ask: float, d5: float | None = None) -> bool:
        """Return True if either spread or depth vacuum condition is triggered."""
        if self.should_veto_spread(bid, ask):
            return True

        if d5 is not None and self.should_veto_depth(d5):
            return True

        return False

    def __repr__(self) -> str:
        return (
            f"LiquidityVacuumVeto(max_spread_bps={self._max_spread_bps}, "
            f"std_mult={self._std_mult}, depth_count={self._depth_normalizer.count})"
        )
