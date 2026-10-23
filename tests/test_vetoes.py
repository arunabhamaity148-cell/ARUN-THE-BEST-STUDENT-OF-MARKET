"""tests/test_vetoes.py — Comprehensive tests for engine/vetoes.py.

Verifies:
1. SpoofingQuoteFlickerVeto:
   - Persistence requirement (5 consecutive 100ms depth updates / 500ms).
   - Realized taker flow (@aggTrade CVD direction) corroboration.
   - Lookback window truncation.
2. FlashCrashKnifeCatchVeto:
   - Bars counter-trend / mean-reversion during accelerating liquidation cascade.
   - Permits trend-following signals unconditionally.
   - Unlocks counter-trend signals only after > 60% deceleration from local cascade peak.
   - Rolling 10-second liquidation velocity window.
3. Memory & slots compliance for all veto classes.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.state import SymbolMicroState
from engine.vetoes import (
    OFI_MIN_PERSISTENCE,
    CVD_LOOKBACK_MS,
    FLASH_CRASH_DECEL_THRESHOLD,
    LIQ_VELOCITY_WINDOW_MS,
    DELEVERAGING_OI_THRESHOLD,
    NEUTRAL_FUNDING_MAX,
    DELEVERAGING_ZLIQ_OVERRIDE,
    FUNDING_BOUNDARY_MS,
    EXTREME_FUNDING_THRESHOLD,
    SpoofingQuoteFlickerVeto,
    FlashCrashKnifeCatchVeto,
    DeleveragingVeto,
    FundingSettlementBoundaryVeto,
)


# ─────────────────────────────────────────────────────────────────────
# 1. SpoofingQuoteFlickerVeto
# ─────────────────────────────────────────────────────────────────────

class TestSpoofingQuoteFlickerVetoSlots:
    """Verify slots and memory efficiency."""

    def test_has_slots(self) -> None:
        assert hasattr(SpoofingQuoteFlickerVeto, "__slots__")

    def test_no_dict(self) -> None:
        veto = SpoofingQuoteFlickerVeto()
        assert not hasattr(veto, "__dict__")

    def test_repr(self) -> None:
        veto = SpoofingQuoteFlickerVeto()
        r = repr(veto)
        assert "SpoofingQuoteFlickerVeto" in r
        assert "min_persistence=5" in r


class TestSpoofingQuoteFlickerVetoLogic:
    """Verify veto decision logic across persistence and CVD alignment."""

    def test_neutral_ofi_is_vetoed(self) -> None:
        state = SymbolMicroState("BTCUSDT")
        veto = SpoofingQuoteFlickerVeto()
        assert veto.should_veto(state, ofi=0.0, now_ms=1000) is True

    def test_insufficient_persistence_is_vetoed(self) -> None:
        """Streak < 5 must be vetoed even if CVD is aligned."""
        state = SymbolMicroState("BTCUSDT")
        veto = SpoofingQuoteFlickerVeto()
        now = 10_000

        # CVD is positive (+10.0)
        state.trade_deltas.append((now - 100, 10.0))
        # Persistence streak is only 4 (< 5)
        state.ofi_sign_streak = 4

        assert veto.should_veto(state, ofi=5.0, now_ms=now) is True

    def test_persistence_met_but_no_taker_flow_is_vetoed(self) -> None:
        """Streak >= 5, but no recent trades -> CVD dir is 0 -> vetoed."""
        state = SymbolMicroState("BTCUSDT")
        veto = SpoofingQuoteFlickerVeto()
        state.ofi_sign_streak = 5

        assert veto.should_veto(state, ofi=5.0, now_ms=10_000) is True

    def test_persistence_met_but_cvd_opposes_ofi_is_vetoed(self) -> None:
        """Positive OFI (buying pressure in book) but taker CVD is negative (aggressive selling).

        Classic quote spoofing scenario.
        """
        state = SymbolMicroState("BTCUSDT")
        veto = SpoofingQuoteFlickerVeto()
        now = 10_000

        state.ofi_sign_streak = 6
        # Recent taker flow is negative
        state.trade_deltas.append((now - 200, -5.0))

        assert veto.should_veto(state, ofi=10.0, now_ms=now) is True

    def test_persistence_met_and_cvd_corroborates_buy_allowed(self) -> None:
        """Streak >= 5 and net CVD in last 500ms is positive -> signal ALLOWED (should_veto=False)."""
        state = SymbolMicroState("BTCUSDT")
        veto = SpoofingQuoteFlickerVeto()
        now = 10_000

        state.ofi_sign_streak = 5
        # Positive taker trade in lookback window
        state.trade_deltas.append((now - 150, 8.5))

        assert veto.should_veto(state, ofi=12.0, now_ms=now) is False

    def test_persistence_met_and_cvd_corroborates_sell_allowed(self) -> None:
        """Negative OFI (streak >= 5) and net CVD negative -> signal ALLOWED (should_veto=False)."""
        state = SymbolMicroState("BTCUSDT")
        veto = SpoofingQuoteFlickerVeto()
        now = 10_000

        state.ofi_sign_streak = 5
        state.trade_deltas.append((now - 300, -12.0))

        assert veto.should_veto(state, ofi=-8.0, now_ms=now) is False

    def test_cvd_lookback_window_cutoff(self) -> None:
        """Trades older than 500ms should not corroborate current OFI."""
        state = SymbolMicroState("BTCUSDT")
        veto = SpoofingQuoteFlickerVeto(cvd_lookback_ms=500)
        now = 10_000

        state.ofi_sign_streak = 5
        # Trade occurred 600ms ago (> 500ms cutoff)
        state.trade_deltas.append((now - 600, 15.0))

        assert veto.should_veto(state, ofi=5.0, now_ms=now) is True


# ─────────────────────────────────────────────────────────────────────
# 2. FlashCrashKnifeCatchVeto
# ─────────────────────────────────────────────────────────────────────

class TestFlashCrashKnifeCatchVetoSlots:
    """Verify slots and memory efficiency."""

    def test_has_slots(self) -> None:
        assert hasattr(FlashCrashKnifeCatchVeto, "__slots__")

    def test_no_dict(self) -> None:
        veto = FlashCrashKnifeCatchVeto()
        assert not hasattr(veto, "__dict__")

    def test_repr(self) -> None:
        veto = FlashCrashKnifeCatchVeto()
        r = repr(veto)
        assert "FlashCrashKnifeCatchVeto" in r
        assert "decel_threshold=0.6" in r


class TestFlashCrashKnifeCatchVetoLogic:
    """Verify veto decision logic during cascade acceleration and deceleration."""

    def test_trend_following_signal_is_never_vetoed(self) -> None:
        """Non counter-trend signals pass through freely even during violent cascade."""
        state = SymbolMicroState("BTCUSDT")
        veto = FlashCrashKnifeCatchVeto()
        now = 20_000

        # Massive liquidation active
        state.add_liquidation(now - 1000, 5_000_000.0)

        # is_counter_trend = False
        assert veto.should_veto(state, is_counter_trend=False, now_ms=now) is False

    def test_no_liquidation_allows_counter_trend(self) -> None:
        """When there is no liquidation activity (velocity = 0), counter-trend is allowed."""
        state = SymbolMicroState("BTCUSDT")
        veto = FlashCrashKnifeCatchVeto()
        assert veto.should_veto(state, is_counter_trend=True, now_ms=1000) is False

    def test_accelerating_cascade_blocks_counter_trend(self) -> None:
        """While liquidation velocity is rising, counter-trend knife-catching is blocked."""
        state = SymbolMicroState("BTCUSDT")
        veto = FlashCrashKnifeCatchVeto()
        now = 50_000

        # Initial liquidation burst
        state.add_liquidation(now - 3000, 500_000.0)
        assert veto.should_veto(state, is_counter_trend=True, now_ms=now) is True
        assert state.liq_velocity_peak == 500_000.0

        # Cascade accelerates: another 500k burst in the same 10s window
        state.add_liquidation(now - 1000, 500_000.0)
        assert veto.should_veto(state, is_counter_trend=True, now_ms=now) is True
        assert state.liq_velocity_peak == 1_000_000.0

    def test_insufficient_deceleration_remains_blocked(self) -> None:
        """Deceleration < 60% of peak must remain blocked."""
        state = SymbolMicroState("BTCUSDT")
        veto = FlashCrashKnifeCatchVeto()

        # Set peak to 1,000,000
        state.liq_velocity_peak = 1_000_000.0
        now = 100_000

        # Current 10s velocity is 600,000 (deceleration = 40% < 60%)
        state.add_liquidation(now - 2000, 600_000.0)
        assert veto.should_veto(state, is_counter_trend=True, now_ms=now) is True

    def test_deceleration_above_60_percent_unlocks_signal(self) -> None:
        """Deceleration >= 60% unlocks counter-trend signal and resets peak."""
        state = SymbolMicroState("BTCUSDT")
        veto = FlashCrashKnifeCatchVeto(decel_threshold=0.6)

        # Previous cascade peak was 1,000,000
        state.liq_velocity_peak = 1_000_000.0
        now = 100_000

        # Velocity has dropped to 300,000 (70% deceleration from peak > 60%)
        state.add_liquidation(now - 1000, 300_000.0)

        assert veto.should_veto(state, is_counter_trend=True, now_ms=now) is False
        # Peak must be reset to 0.0 upon unlock
        assert state.liq_velocity_peak == 0.0

    def test_velocity_window_cutoff(self) -> None:
        """Liquidation events older than 10s drop out of current velocity."""
        state = SymbolMicroState("BTCUSDT")
        veto = FlashCrashKnifeCatchVeto(velocity_window_ms=10_000)
        now = 50_000

        # Event 12 seconds ago (outside 10s window)
        state.add_liquidation(now - 12_000, 1_000_000.0)
        # Event 2 seconds ago (inside 10s window)
        state.add_liquidation(now - 2_000, 200_000.0)

        velocity = veto.compute_liq_velocity(state, now_ms=now)
        assert velocity == 200_000.0


# ─────────────────────────────────────────────────────────────────────
# 3. DeleveragingVeto
# ─────────────────────────────────────────────────────────────────────

class TestDeleveragingVetoSlots:
    """Verify slots enforcement."""

    def test_has_slots(self) -> None:
        assert hasattr(DeleveragingVeto, "__slots__")

    def test_no_dict(self) -> None:
        veto = DeleveragingVeto()
        assert not hasattr(veto, "__dict__")

    def test_repr(self) -> None:
        veto = DeleveragingVeto()
        r = repr(veto)
        assert "DeleveragingVeto" in r


class TestDeleveragingVetoLogic:
    """Verify deleveraging veto rules and Z_liq override."""

    def test_non_counter_trend_allowed(self) -> None:
        veto = DeleveragingVeto()
        # Trend-following long is allowed
        assert veto.should_veto(
            is_long=True,
            is_counter_trend=False,
            price_change_pct=-1.5,
            delta_oi_pct=-3.0,
            funding_rate=0.00005,
        ) is False

    def test_short_signal_allowed(self) -> None:
        veto = DeleveragingVeto()
        # Short signal is not vetoed by deleveraging long veto
        assert veto.should_veto(
            is_long=False,
            is_counter_trend=True,
            price_change_pct=-1.5,
            delta_oi_pct=-3.0,
            funding_rate=0.00005,
        ) is False

    def test_deleveraging_blocks_mean_reversion_long(self) -> None:
        veto = DeleveragingVeto()
        # Price declining (-1.0%), OI contracting (-3.2% < -2.5%), funding neutral (0.00005 <= 0.0001)
        # Z_liq = 1.0 (no extreme cascade) -> VETOED
        assert veto.should_veto(
            is_long=True,
            is_counter_trend=True,
            price_change_pct=-1.0,
            delta_oi_pct=-3.2,
            funding_rate=0.00005,
            z_liq=1.0,
        ) is True

    def test_zliq_override_unlocks_long(self) -> None:
        veto = DeleveragingVeto()
        # Same conditions, but Z_liq = 3.8 > 3.5 (capitulation exhaustion) -> UNLOCKED
        assert veto.should_veto(
            is_long=True,
            is_counter_trend=True,
            price_change_pct=-1.0,
            delta_oi_pct=-3.2,
            funding_rate=0.00005,
            z_liq=3.8,
        ) is False

    def test_fails_if_price_rising(self) -> None:
        veto = DeleveragingVeto()
        # Price rising (+0.5%) -> not a deleveraging dump
        assert veto.should_veto(
            is_long=True,
            is_counter_trend=True,
            price_change_pct=0.5,
            delta_oi_pct=-3.0,
            funding_rate=0.00005,
        ) is False

    def test_fails_if_oi_not_contracting(self) -> None:
        veto = DeleveragingVeto()
        # OI is expanding (+1.0% >= -2.5%)
        assert veto.should_veto(
            is_long=True,
            is_counter_trend=True,
            price_change_pct=-1.0,
            delta_oi_pct=1.0,
            funding_rate=0.00005,
        ) is False

    def test_fails_if_funding_not_neutral(self) -> None:
        veto = DeleveragingVeto()
        # Funding rate is negative and extreme (-0.0003 > 0.0001 neutral threshold)
        assert veto.should_veto(
            is_long=True,
            is_counter_trend=True,
            price_change_pct=-1.0,
            delta_oi_pct=-3.0,
            funding_rate=-0.0003,
        ) is False


# ─────────────────────────────────────────────────────────────────────
# 4. FundingSettlementBoundaryVeto
# ─────────────────────────────────────────────────────────────────────

class TestFundingSettlementBoundaryVetoSlots:
    """Verify slots enforcement."""

    def test_has_slots(self) -> None:
        assert hasattr(FundingSettlementBoundaryVeto, "__slots__")

    def test_no_dict(self) -> None:
        veto = FundingSettlementBoundaryVeto()
        assert not hasattr(veto, "__dict__")

    def test_repr(self) -> None:
        veto = FundingSettlementBoundaryVeto()
        r = repr(veto)
        assert "FundingSettlementBoundaryVeto" in r


class TestFundingSettlementBoundaryVetoLogic:
    """Verify extreme funding boundary suppression."""

    def test_outside_boundary_window_allowed(self) -> None:
        veto = FundingSettlementBoundaryVeto()
        now = 1_000_000
        settlement = now + 10 * 60 * 1000  # 10 minutes away (> 5m boundary)

        # Long with extreme funding (+0.08%) outside boundary is allowed
        assert veto.should_veto(
            is_long=True,
            funding_rate=0.0008,
            next_funding_time_ms=settlement,
            now_ms=now,
        ) is False

    def test_inside_boundary_long_paying_extreme_funding_vetoed(self) -> None:
        veto = FundingSettlementBoundaryVeto()
        now = 1_000_000
        settlement = now + 2 * 60 * 1000  # 2 minutes away (<= 5m boundary)

        # Long paying > +0.05% (+0.0006) -> VETOED
        assert veto.should_veto(
            is_long=True,
            funding_rate=0.0006,
            next_funding_time_ms=settlement,
            now_ms=now,
        ) is True

    def test_inside_boundary_short_paying_extreme_negative_funding_vetoed(self) -> None:
        veto = FundingSettlementBoundaryVeto()
        now = 1_000_000
        settlement = now + 1 * 60 * 1000  # 1 minute away

        # Short paying < -0.05% (-0.0007) -> VETOED
        assert veto.should_veto(
            is_long=False,
            funding_rate=-0.0007,
            next_funding_time_ms=settlement,
            now_ms=now,
        ) is True

    def test_inside_boundary_normal_funding_allowed(self) -> None:
        veto = FundingSettlementBoundaryVeto()
        now = 1_000_000
        settlement = now + 3 * 60 * 1000

        # Normal funding (+0.01% / +0.0001 <= +0.0005) -> ALLOWED
        assert veto.should_veto(
            is_long=True,
            funding_rate=0.0001,
            next_funding_time_ms=settlement,
            now_ms=now,
        ) is False

    def test_inside_boundary_receiver_allowed(self) -> None:
        veto = FundingSettlementBoundaryVeto()
        now = 1_000_000
        settlement = now + 2 * 60 * 1000

        # Short position receives positive funding (+0.08%) -> ALLOWED (only payers are penalized)
        assert veto.should_veto(
            is_long=False,
            funding_rate=0.0008,
            next_funding_time_ms=settlement,
            now_ms=now,
        ) is False

    def test_zero_settlement_timestamp_allowed(self) -> None:
        veto = FundingSettlementBoundaryVeto()
        assert veto.should_veto(
            is_long=True,
            funding_rate=0.001,
            next_funding_time_ms=0,
            now_ms=1000,
        ) is False

