"""tests/test_institutional_edges.py — Comprehensive tests for Phase 6B Institutional Microstructure Edges.

We test:
  1. Edge 1: Spot-to-Perp Lead-Lag (Lambda_lead)
  2. Edge 2: Self-Exciting Hawkes Process (Lambda_t & Exhaustion)
  3. Edge 3: Iceberg / Ghost Liquidity Replenishment (G_t)
  4. Edge 4: Cross-Margined Collateral Stress (Psi_stress)
  5. Edge 5: Lo-MacKinlay Variance Ratio & GKYZ Volatility Regime
  6. InstitutionalEdgeCalculator Slots and Stateful Routing
"""

import math
from collections import deque
import pytest

from engine.institutional_edges import (
    sign,
    compute_lead_lag_score,
    compute_hawkes_intensity,
    check_hawkes_exhaustion,
    compute_ghost_volume,
    compute_iceberg_ratio,
    compute_micro_price,
    compute_collateral_stress_score,
    sample_variance,
    compute_variance_ratio,
    compute_gkyz_variance,
    classify_volatility_regime,
    InstitutionalEdgeCalculator,
)
from core.state import SymbolMicroState


# ─────────────────────────────────────────────────────────────────────
# 1. Edge 1: Spot-to-Perp Lead-Lag (Lambda_lead)
# ─────────────────────────────────────────────────────────────────────

class TestSpotPerpLeadLag:
    """Test Edge 1: Spot-to-Perp Microsecond Lead-Lag."""

    def test_sign_helper(self) -> None:
        assert sign(5.5) == 1.0
        assert sign(-3.2) == -1.0
        assert sign(0.0) == 0.0

    def test_lead_lag_formula_pure(self) -> None:
        # Lambda_lead = Z(spot) - 0.65*Z(perp) - 0.80*sign(spot)*Z(delta_basis)
        z_spot = 3.0
        z_perp = 1.0
        z_delta_basis = -1.5
        spot_cvd = 100.0  # sign = +1.0
        delta_basis = -0.5  # sign = -1.0 (signs differ)

        score, triggered = compute_lead_lag_score(
            z_spot=z_spot,
            z_perp=z_perp,
            z_delta_basis=z_delta_basis,
            spot_cvd=spot_cvd,
            delta_basis=delta_basis,
        )

        expected = 3.0 - (0.65 * 1.0) - (0.80 * 1.0 * -1.5)
        # expected = 3.0 - 0.65 + 1.20 = 3.55
        assert math.isclose(score, expected)
        assert triggered is True
        assert abs(score) >= 2.65

    def test_lead_lag_blocks_when_signs_match(self) -> None:
        # Same signs between spot_cvd and delta_basis must NOT trigger
        z_spot = 4.0
        z_perp = 0.5
        z_delta_basis = -2.0
        spot_cvd = 50.0  # sign = +1.0
        delta_basis = 1.2   # sign = +1.0 (signs match!)

        score, triggered = compute_lead_lag_score(
            z_spot=z_spot,
            z_perp=z_perp,
            z_delta_basis=z_delta_basis,
            spot_cvd=spot_cvd,
            delta_basis=delta_basis,
        )

        assert abs(score) >= 2.65
        assert triggered is False  # signs matched, so blocked

    def test_lead_lag_blocks_when_below_threshold(self) -> None:
        # When score is below 2.65, must NOT trigger even if signs differ
        score, triggered = compute_lead_lag_score(
            z_spot=1.0,
            z_perp=1.0,
            z_delta_basis=0.5,
            spot_cvd=10.0,
            delta_basis=-1.0,
        )
        assert abs(score) < 2.65
        assert triggered is False

    def test_stateful_lead_lag_calculation(self) -> None:
        calc = InstitutionalEdgeCalculator("BTCUSDT")
        state = SymbolMicroState("BTCUSDT")

        now_ms = 100000
        # Add trades to spot and perp 10s deques
        state.add_spot_trade_10s(now_ms - 2000, 15.0)
        state.add_perp_trade_10s(now_ms - 1000, 5.0)

        # An old trade that should be pruned (> 10s old)
        state.add_spot_trade_10s(now_ms - 15000, 999.0)

        score, triggered = calc.update_lead_lag(
            state=state,
            spot_price=50000.0,
            perp_price=50010.0,
            now_ms=now_ms,
        )
        assert isinstance(score, float)
        assert isinstance(triggered, bool)
        # Old trade was pruned
        assert len(state.spot_cvd_10s) == 1
        assert state.spot_cvd_10s[0][1] == 15.0


# ─────────────────────────────────────────────────────────────────────
# 2. Edge 2: Self-Exciting Hawkes Process (Lambda_t & Exhaustion)
# ─────────────────────────────────────────────────────────────────────

class TestHawkesProcess:
    """Test Edge 2: Self-Exciting Hawkes Process for liquidation cascades."""

    def test_hawkes_step_formula(self) -> None:
        # A_t = A_prev * exp(-beta * dt) + alpha * (V_k / avg_V)
        # lambda_t = mu_0 + A_t
        a_prev = 1.0
        dt = 0.5
        v_k = 20000.0
        avg_v = 10000.0
        alpha = 0.85
        beta = 1.20
        mu_0 = 0.05

        a_t, lambda_t = compute_hawkes_intensity(
            a_prev=a_prev,
            dt=dt,
            v_k=v_k,
            avg_v=avg_v,
            alpha=alpha,
            beta=beta,
            mu_0=mu_0,
        )

        expected_a = 1.0 * math.exp(-1.20 * 0.5) + 0.85 * (20000.0 / 10000.0)
        expected_lambda = 0.05 + expected_a
        assert math.isclose(a_t, expected_a)
        assert math.isclose(lambda_t, expected_lambda)

    def test_hawkes_exhaustion_trigger(self) -> None:
        mu_0 = 0.05
        # peak / mu_0 >= 25.0 means peak >= 1.25
        peak_lambda = 2.0  # 2.0 / 0.05 = 40.0 >= 25.0
        # lambda_t <= 0.70 * peak means lambda_t <= 1.40
        exhausted = check_hawkes_exhaustion(lambda_t=1.30, peak_lambda=peak_lambda, mu_0=mu_0)
        assert exhausted is True

    def test_hawkes_exhaustion_blocks_if_not_decayed(self) -> None:
        mu_0 = 0.05
        peak_lambda = 2.0
        # lambda_t > 0.70 * peak (e.g. 1.60 > 1.40) -> still active cascade
        exhausted = check_hawkes_exhaustion(lambda_t=1.60, peak_lambda=peak_lambda, mu_0=mu_0)
        assert exhausted is False

    def test_hawkes_exhaustion_blocks_if_peak_insufficient(self) -> None:
        mu_0 = 0.05
        peak_lambda = 1.0  # 1.0 / 0.05 = 20.0 < 25.0 (cascade wasn't large enough)
        exhausted = check_hawkes_exhaustion(lambda_t=0.50, peak_lambda=peak_lambda, mu_0=mu_0)
        assert exhausted is False

    def test_stateful_hawkes_tracking(self) -> None:
        calc = InstitutionalEdgeCalculator("ETHUSDT")
        state = SymbolMicroState("ETHUSDT")

        # Simulate sudden large cascade
        t0 = 1000000
        for i in range(5):
            calc.update_hawkes(state, price=3000.0, qty=50.0, ts_ms=t0 + i * 200)

        assert state.hawkes_a > 0.0
        assert len(state.hawkes_peak_ring) == 5
        peak = max(state.hawkes_peak_ring)
        assert peak > 0.05


# ─────────────────────────────────────────────────────────────────────
# 3. Edge 3: Iceberg / Ghost Liquidity Replenishment (G_t)
# ─────────────────────────────────────────────────────────────────────

class TestIcebergGhostLiquidity:
    """Test Edge 3: Iceberg / Ghost Liquidity Replenishment."""

    def test_ghost_volume_calculation(self) -> None:
        # Aggressive sell of 10 BTC into bid with 5 BTC visible
        # If visible drops from 5 to 3 (drop = 2 BTC), then 10 - 2 = 8 BTC was ghost!
        ghost = compute_ghost_volume(aggressive_volume=10.0, prev_qty=5.0, new_qty=3.0)
        assert math.isclose(ghost, 8.0)

    def test_zero_ghost_when_book_drops_fully(self) -> None:
        # If book drops by 10 BTC on 10 BTC executed, ghost is 0.0
        ghost = compute_ghost_volume(aggressive_volume=10.0, prev_qty=12.0, new_qty=2.0)
        assert math.isclose(ghost, 0.0)

    def test_iceberg_ratio_formula(self) -> None:
        ratio = compute_iceberg_ratio(ghost_vol_sum=70.0, exec_vol_sum=100.0)
        assert math.isclose(ratio, 0.70, rel_tol=1e-5)

    def test_stateful_iceberg_trigger(self) -> None:
        calc = InstitutionalEdgeCalculator("SOLUSDT")
        state = SymbolMicroState("SOLUSDT")

        # Baseline executions with no ghost volume
        for _ in range(25):
            calc.update_iceberg(state, aggressive_volume=1.0, prev_qty=10.0, new_qty=9.0)

        # Consecutive iceberg replenishment events with heavy ghost volume
        for _ in range(5):
            g_t, triggered = calc.update_iceberg(
                state=state,
                aggressive_volume=100.0,
                prev_qty=10.0,
                new_qty=9.0,  # drop = 1.0, ghost = 99.0
            )
        assert g_t >= 0.68
        assert isinstance(triggered, bool)


# ─────────────────────────────────────────────────────────────────────
# 4. Edge 4: Cross-Margined Collateral Stress (Psi_stress)
# ─────────────────────────────────────────────────────────────────────

class TestCollateralStress:
    """Test Edge 4: Cross-Margined Collateral Stress (COIN-M vs USD-M)."""

    def test_micro_price_formula(self) -> None:
        # MP = (bid * ask_qty + ask * bid_qty) / (bid_qty + ask_qty)
        mp = compute_micro_price(bid=100.0, ask=102.0, bid_qty=3.0, ask_qty=1.0)
        # (100 * 1 + 102 * 3) / 4 = (100 + 306) / 4 = 406 / 4 = 101.5
        assert math.isclose(mp, 101.5)

    def test_micro_price_zero_guard(self) -> None:
        mp = compute_micro_price(bid=100.0, ask=102.0, bid_qty=0.0, ask_qty=0.0)
        assert math.isclose(mp, 101.0)

    def test_collateral_stress_pure(self) -> None:
        # Psi_stress = Z(MP_CM) - Z(MP_UM)
        psi = compute_collateral_stress_score(z_mp_cm=-1.2, z_mp_um=1.8)
        # -1.2 - 1.8 = -3.0
        assert math.isclose(psi, -3.0)

    def test_stateful_collateral_stress_triggers(self) -> None:
        calc = InstitutionalEdgeCalculator("BTCUSDT")

        # Seed normalizers with varying prices
        for i in range(25):
            calc.compute_collateral_stress(
                bid_cm=50000.0 + i, ask_cm=50001.0 + i, bid_qty_cm=1.0, ask_qty_cm=1.0,
                bid_um=50000.0 + i, ask_um=50001.0 + i, bid_qty_um=1.0, ask_qty_um=1.0,
            )

        # Force Short setup: Z(MP_UM) >= 2.0 and Psi_stress <= -2.7
        psi, short_trig, long_trig = calc.compute_collateral_stress(
            bid_cm=49000.0, ask_cm=49001.0, bid_qty_cm=1.0, ask_qty_cm=1.0,
            bid_um=52000.0, ask_um=52001.0, bid_qty_um=1.0, ask_qty_um=1.0,
        )
        assert isinstance(psi, float)
        assert isinstance(short_trig, bool)
        assert isinstance(long_trig, bool)


# ─────────────────────────────────────────────────────────────────────
# 5. Edge 5: Lo-MacKinlay Variance Ratio & GKYZ Volatility
# ─────────────────────────────────────────────────────────────────────

class TestVarianceRatioAndGKYZ:
    """Test Edge 5: Lo-MacKinlay Variance Ratio and GKYZ Volatility."""

    def test_sample_variance_pure(self) -> None:
        data = [2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0]
        # Mean = 40 / 8 = 5.0
        # Differences = [-3, -1, -1, -1, 0, 0, 2, 4]
        # Squared sum = 9 + 1 + 1 + 1 + 0 + 0 + 4 + 16 = 32
        # Variance = 32 / 7 = 4.571428...
        var = sample_variance(data)
        assert math.isclose(var, 32.0 / 7.0)

    def test_variance_ratio_pure_insufficient_data(self) -> None:
        assert compute_variance_ratio([100.0, 101.0]) == 1.0

    def test_variance_ratio_pure_random_walk(self) -> None:
        # A steady arithmetic/geometric series
        closes = [100.0 + i * 0.5 for i in range(30)]
        vr = compute_variance_ratio(closes)
        assert isinstance(vr, float)
        assert vr > 0.0

    def test_gkyz_variance_pure(self) -> None:
        # GKYZ_var = (ln(O/C_prev))^2 + 0.5*(ln(H/L))^2 - (2ln2 - 1)*(ln(C/O))^2
        v = compute_gkyz_variance(
            open_p=100.0,
            high_p=102.0,
            low_p=99.0,
            close_p=101.0,
            prev_close=100.0,
        )
        assert v > 0.0

    def test_classify_volatility_regime(self) -> None:
        # Mean reversion: VR(5) <= 0.85 and abs(Carry_Skew) >= 1.5
        assert classify_volatility_regime(vr_5=0.80, carry_skew=1.6) == "MEAN_REVERSION"
        assert classify_volatility_regime(vr_5=0.80, carry_skew=-2.0) == "MEAN_REVERSION"

        # Breakout: VR(5) >= 1.18
        assert classify_volatility_regime(vr_5=1.25, carry_skew=0.5) == "BREAKOUT"

        # Neutral otherwise
        assert classify_volatility_regime(vr_5=1.00, carry_skew=0.5) == "NEUTRAL"
        assert classify_volatility_regime(vr_5=0.80, carry_skew=0.5) == "NEUTRAL"

    def test_stateful_kline_regime_update(self) -> None:
        calc = InstitutionalEdgeCalculator("SOLUSDT")

        # Feed 15 klines
        for i in range(15):
            vr, vol, skew, regime = calc.update_kline(
                open_p=150.0 + i,
                high_p=152.0 + i,
                low_p=149.0 + i,
                close_p=151.0 + i,
                funding_rate=0.0001,
            )
        assert isinstance(vr, float)
        assert isinstance(vol, float)
        assert isinstance(skew, float)
        assert regime in ("MEAN_REVERSION", "BREAKOUT", "NEUTRAL")


# ─────────────────────────────────────────────────────────────────────
# 6. InstitutionalEdgeCalculator Slots & Memory Test
# ─────────────────────────────────────────────────────────────────────

class TestInstitutionalEdgeCalculatorSlots:

    def test_has_slots(self) -> None:
        assert hasattr(InstitutionalEdgeCalculator, "__slots__")

    def test_no_dict(self) -> None:
        calc = InstitutionalEdgeCalculator("BTCUSDT")
        assert not hasattr(calc, "__dict__")

    def test_repr(self) -> None:
        calc = InstitutionalEdgeCalculator("BTCUSDT")
        assert "BTCUSDT" in repr(calc)
