"""tests/test_spectral_risk.py — Comprehensive unit tests for Phase 6C Spectral Risk & Mantegna Clustering Engine.

We test:
  1. Upper-triangular flat index mapping (_flat_idx)
  2. Zero-allocation EWMA covariance update
  3. Power-iteration spectral absorption ratio (convergence to dominant eigenvalue)
  4. Mantegna angular distance calculation and clustering collision veto
  5. 4-Gate portfolio risk filter (Systemic contagion, heat cap, cluster collision, eigen-beta sizing)
  6. Memory constraints (__slots__, exactly 55 array elements / 440 bytes)
"""

import math
from array import array
import pytest

from engine.spectral_risk import (
    flat_triangular_index,
    compute_mantegna_distance,
    compute_eigen_loading,
    compute_risk_multiplier,
    PurePythonSpectralRiskEngine,
)


# ─────────────────────────────────────────────────────────────────────
# 1. Flat Index Mapping
# ─────────────────────────────────────────────────────────────────────

class TestFlatIndexMapping:
    """Test 2D-to-1D upper-triangular indexing."""

    def test_all_55_indices_unique_and_sequential(self) -> None:
        n = 10
        indices = []
        for i in range(n):
            for j in range(i, n):
                idx = flat_triangular_index(i, j, n)
                indices.append(idx)

        assert len(indices) == 55
        assert set(indices) == set(range(55))
        assert indices == list(range(55))

    def test_symmetry(self) -> None:
        n = 10
        for i in range(n):
            for j in range(n):
                assert flat_triangular_index(i, j, n) == flat_triangular_index(j, i, n)

    def test_diagonal_indices(self) -> None:
        # (0, 0)=0, (1, 1)=10, (2, 2)=19, ..., (9, 9)=54
        n = 10
        diags = [flat_triangular_index(i, i, n) for i in range(n)]
        assert diags[0] == 0
        assert diags[1] == 10
        assert diags[2] == 19
        assert diags[9] == 54


# ─────────────────────────────────────────────────────────────────────
# 2. Zero-Allocation EWMA Covariance
# ─────────────────────────────────────────────────────────────────────

class TestEWMACovariance:
    """Test EWMA covariance update logic and exact RAM footprint."""

    def test_ram_footprint_55_c_doubles(self) -> None:
        engine = PurePythonSpectralRiskEngine()
        assert len(engine.cov_flat) == 55
        assert engine.cov_flat.itemsize == 8  # 8 bytes per C-double
        assert len(engine.cov_flat) * engine.cov_flat.itemsize == 440  # Exactly 440 bytes!

    def test_initial_diagonal_non_zero(self) -> None:
        engine = PurePythonSpectralRiskEngine()
        for i in range(10):
            var_i = engine.get_cov(i, i)
            assert math.isclose(var_i, 1e-6)

    def test_single_step_ewma_manual_check(self) -> None:
        # Span = 60 -> alpha = 2 / 61
        engine = PurePythonSpectralRiskEngine(span_minutes=60.0)
        alpha = 2.0 / 61.0

        # Feed a constant return vector
        ret = [0.01] * 10
        engine.update_returns_1m(ret)

        # mean_1 = alpha * 0.01
        expected_mean = alpha * 0.01
        assert math.isclose(engine.means[0], expected_mean)

        # delta = 0.01 - 0.0 = 0.01
        # cov_1 = (1 - alpha) * (cov_0 + alpha * delta^2)
        expected_cov = (1.0 - alpha) * (1e-6 + alpha * (0.01 ** 2))
        assert math.isclose(engine.get_cov(0, 0), expected_cov)


# ─────────────────────────────────────────────────────────────────────
# 3. Power-Iteration Spectral Absorption Ratio (AR)
# ─────────────────────────────────────────────────────────────────────

class TestPowerIterationSpectralAR:
    """Test convergence of 8-step power iteration to dominant eigenvalue."""

    def test_uncorrelated_system_has_minimal_ar(self) -> None:
        # In a fully uncorrelated system, C = I_10, all eigenvalues are 1.0
        # AR = 1.0 / 10 = 0.10
        engine = PurePythonSpectralRiskEngine()
        ar = engine._step_power_iteration()
        assert math.isclose(ar, 0.10, abs_tol=1e-4)

    def test_perfectly_correlated_system_has_maximal_ar(self) -> None:
        # In a 100% synchronized market, C is all 1s, lambda_max = 10.0
        # AR = 10.0 / 10 = 1.00
        engine = PurePythonSpectralRiskEngine()
        # Set all covariances to 1.0
        for idx in range(55):
            engine.cov_flat[idx] = 1.0

        ar = engine._step_power_iteration()
        assert math.isclose(ar, 1.00, abs_tol=1e-4)

    def test_two_asset_analytical_eigenvalue(self) -> None:
        # For a 2x2 correlation matrix with rho = 0.8:
        # lambda_max = 1 + 0.8 = 1.8
        engine = PurePythonSpectralRiskEngine(symbols=("BTCUSDT", "ETHUSDT"))
        engine.set_cov(0, 0, 1.0)
        engine.set_cov(1, 1, 1.0)
        engine.set_cov(0, 1, 0.8)

        ar = engine._step_power_iteration()
        # lambda_max should be ~1.8, AR = 1.8 / 2 = 0.90
        assert math.isclose(ar, 0.90, rel_tol=1e-3)


# ─────────────────────────────────────────────────────────────────────
# 4. Mantegna Angular Distance
# ─────────────────────────────────────────────────────────────────────

class TestMantegnaDistance:
    """Test Mantegna metric distance: dist = sqrt(0.5 * (1 - directional_rho))."""

    def test_distance_at_boundaries(self) -> None:
        # Perfectly aligned: rho = 1.0 -> dist = 0.0
        assert math.isclose(compute_mantegna_distance(1.0), 0.0)

        # Orthogonal: rho = 0.0 -> dist = sqrt(0.5) ~ 0.7071
        assert math.isclose(compute_mantegna_distance(0.0), math.sqrt(0.5))

        # Completely anti-correlated: rho = -1.0 -> dist = 1.0
        assert math.isclose(compute_mantegna_distance(-1.0), 1.0)

    def test_clustering_veto_threshold(self) -> None:
        # Threshold dist <= 0.35 corresponds to directional rho >= 0.755
        dist_at_075 = compute_mantegna_distance(0.755)
        assert math.isclose(dist_at_075, 0.35, abs_tol=1e-2)

        # Above threshold (dist <= 0.35)
        dist_high_corr = compute_mantegna_distance(0.85)
        assert dist_high_corr < 0.35

        # Safe (dist > 0.35)
        dist_low_corr = compute_mantegna_distance(0.50)
        assert dist_low_corr > 0.35


# ─────────────────────────────────────────────────────────────────────
# 5. Four-Gate Portfolio Risk Filter
# ─────────────────────────────────────────────────────────────────────

class TestPortfolioRiskGates:
    """Test Gate 1 (Contagion), Gate 2 (Heat Cap), Gate 3 (Cluster Collision), Gate 4 (Sizing)."""

    def test_gate1_systemic_contagion_blocks_altcoin_breakout(self) -> None:
        engine = PurePythonSpectralRiskEngine()
        engine.absorption_ratio = 0.75  # Systemic risk extreme >= 0.72

        # Altcoin breakout must be VETOED
        approved, mult, reason = engine.evaluate_portfolio_risk_gate(
            candidate_sym="SOLUSDT",
            candidate_dir=1,
            strategy_type="BREAKOUT",
            active_positions={},
        )
        assert approved is False
        assert mult == 0.0
        assert "VETO: Spectral AR >= 0.72" in reason

    def test_gate1_allows_btc_eth_breakout_during_contagion(self) -> None:
        engine = PurePythonSpectralRiskEngine()
        engine.absorption_ratio = 0.75

        # BTC and ETH are immune to Gate 1
        for sym in ("BTCUSDT", "ETHUSDT"):
            approved, mult, reason = engine.evaluate_portfolio_risk_gate(
                candidate_sym=sym,
                candidate_dir=1,
                strategy_type="BREAKOUT",
                active_positions={},
            )
            assert approved is True
            assert mult > 0.0

    def test_gate1_allows_mean_reversion_during_contagion(self) -> None:
        engine = PurePythonSpectralRiskEngine()
        engine.absorption_ratio = 0.75

        # Mean reversion is NOT blocked by Gate 1
        approved, mult, reason = engine.evaluate_portfolio_risk_gate(
            candidate_sym="SOLUSDT",
            candidate_dir=-1,
            strategy_type="MEAN_REVERSION",
            active_positions={},
        )
        assert approved is True
        assert mult > 0.0

    def test_gate2_portfolio_heat_cap_low_risk(self) -> None:
        engine = PurePythonSpectralRiskEngine()
        engine.absorption_ratio = 0.40  # Low risk (< 0.58) -> max 4 positions

        # 3 positions active -> 4th allowed
        active = {"BTCUSDT": 1, "ETHUSDT": 1, "SOLUSDT": -1}
        approved, _, _ = engine.evaluate_portfolio_risk_gate(
            candidate_sym="DOGEUSDT",
            candidate_dir=1,
            strategy_type="BREAKOUT",
            active_positions=active,
        )
        assert approved is True

        # 4 positions active -> 5th VETOED
        active["DOGEUSDT"] = 1
        approved, mult, reason = engine.evaluate_portfolio_risk_gate(
            candidate_sym="NEARUSDT",
            candidate_dir=1,
            strategy_type="BREAKOUT",
            active_positions=active,
        )
        assert approved is False
        assert "Portfolio Heat Cap" in reason

    def test_gate2_portfolio_heat_cap_high_risk(self) -> None:
        engine = PurePythonSpectralRiskEngine()
        engine.absorption_ratio = 0.65  # High risk (>= 0.58) -> max 2 positions

        active = {"BTCUSDT": 1, "ETHUSDT": 1}
        approved, mult, reason = engine.evaluate_portfolio_risk_gate(
            candidate_sym="SOLUSDT",
            candidate_dir=1,
            strategy_type="MEAN_REVERSION",
            active_positions=active,
        )
        assert approved is False
        assert "Portfolio Heat Cap" in reason

    def test_gate3_cluster_collision_blocks_same_direction(self) -> None:
        engine = PurePythonSpectralRiskEngine()
        engine.absorption_ratio = 0.30

        # Set high correlation between SOLUSDT and AVAXUSDT (rho = 0.90)
        sol_idx = engine.sym_to_idx["SOLUSDT"]
        avax_idx = engine.sym_to_idx["AVAXUSDT"]
        engine.set_cov(sol_idx, sol_idx, 1.0)
        engine.set_cov(avax_idx, avax_idx, 1.0)
        engine.set_cov(sol_idx, avax_idx, 0.90)

        # Holding Long SOL, candidate Long AVAX -> collision!
        active = {"SOLUSDT": 1}
        approved, mult, reason = engine.evaluate_portfolio_risk_gate(
            candidate_sym="AVAXUSDT",
            candidate_dir=1,  # Same direction (+1 * +1 * 0.90 = +0.90, dist = 0.22 < 0.35)
            strategy_type="BREAKOUT",
            active_positions=active,
        )
        assert approved is False
        assert "Mantegna Cluster Collision" in reason

    def test_gate3_hedged_positions_allowed(self) -> None:
        engine = PurePythonSpectralRiskEngine()
        engine.absorption_ratio = 0.30

        sol_idx = engine.sym_to_idx["SOLUSDT"]
        avax_idx = engine.sym_to_idx["AVAXUSDT"]
        engine.set_cov(sol_idx, sol_idx, 1.0)
        engine.set_cov(avax_idx, avax_idx, 1.0)
        engine.set_cov(sol_idx, avax_idx, 0.90)

        # Holding Long SOL (+1), candidate Short AVAX (-1) -> Hedged!
        # dir_rho = (+1) * (-1) * 0.90 = -0.90, dist = sqrt(0.5 * 1.90) = 0.975 > 0.35
        active = {"SOLUSDT": 1}
        approved, mult, reason = engine.evaluate_portfolio_risk_gate(
            candidate_sym="AVAXUSDT",
            candidate_dir=-1,
            strategy_type="BREAKOUT",
            active_positions=active,
        )
        assert approved is True
        assert mult > 0.0

    def test_gate4_eigen_beta_sizing_scaling(self) -> None:
        engine = PurePythonSpectralRiskEngine()
        engine.absorption_ratio = 0.50

        # When AR is moderate and loading is 1.0:
        # risk_mult = 1.0 - 0.55 * 0.50 * 1.0 = 0.725
        loading = 1.0
        mult = compute_risk_multiplier(0.50, loading)
        assert math.isclose(mult, 0.725)

        # Bound check [0.25, 1.0]
        assert compute_risk_multiplier(1.0, 3.0) == 0.25
        assert compute_risk_multiplier(0.1, 0.0) == 1.0


# ─────────────────────────────────────────────────────────────────────
# 6. Slots and Memory Verification
# ─────────────────────────────────────────────────────────────────────

class TestSpectralRiskEngineSlots:

    def test_has_slots(self) -> None:
        assert hasattr(PurePythonSpectralRiskEngine, "__slots__")

    def test_no_dict(self) -> None:
        engine = PurePythonSpectralRiskEngine()
        assert not hasattr(engine, "__dict__")

    def test_repr(self) -> None:
        engine = PurePythonSpectralRiskEngine()
        r = repr(engine)
        assert "PurePythonSpectralRiskEngine" in r
        assert "AR=" in r
