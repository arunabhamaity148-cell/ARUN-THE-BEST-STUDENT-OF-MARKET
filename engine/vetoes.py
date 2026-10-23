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


# ─────────────────────────────────────────────────────────────────────
# Pre-computed Constants
# ─────────────────────────────────────────────────────────────────────

OFI_MIN_PERSISTENCE: int = 5             # 5 × 100 ms = 500 ms minimum
CVD_LOOKBACK_MS: int = 500               # window for CVD direction check
FLASH_CRASH_DECEL_THRESHOLD: float = 0.6  # 60 % deceleration to unlock
LIQ_VELOCITY_WINDOW_MS: int = 10_000      # 10-second rolling window


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
