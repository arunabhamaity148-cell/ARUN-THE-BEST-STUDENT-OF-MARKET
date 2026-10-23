"""tests/test_heat_lock.py — Comprehensive tests for engine/heat_lock.py.

Verifies:
1. Slots and memory compliance for CorrelationHeatLock.
2. Pearson correlation math across positive, negative, and zero-correlation series.
3. Insufficient sample (< 5) and zero variance guards.
4. Concurrency cap veto (>= 3 active signals).
5. Correlation heat lock veto (max(|rho|) > 0.75 against active signals).
6. 1-minute close price log-return calculation.
"""

import math
import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine.heat_lock import (
    CorrelationHeatLock,
    compute_pearson_correlation,
    MAX_CORRELATION_THRESHOLD,
    MAX_ACTIVE_SIGNALS,
)


class TestCorrelationHeatLockSlots:
    """Verify slots enforcement."""

    def test_has_slots(self) -> None:
        assert hasattr(CorrelationHeatLock, "__slots__")

    def test_no_dict(self) -> None:
        lock = CorrelationHeatLock()
        assert not hasattr(lock, "__dict__")

    def test_repr(self) -> None:
        lock = CorrelationHeatLock()
        r = repr(lock)
        assert "CorrelationHeatLock" in r


class TestPearsonCorrelationMath:
    """Verify Pearson correlation calculation."""

    def test_perfect_positive_correlation(self) -> None:
        a = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
        b = [2.0, 4.0, 6.0, 8.0, 10.0, 12.0]
        rho = compute_pearson_correlation(a, b)
        assert math.isclose(rho, 1.0, rel_tol=1e-9)

    def test_perfect_negative_correlation(self) -> None:
        a = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
        b = [-2.0, -4.0, -6.0, -8.0, -10.0, -12.0]
        rho = compute_pearson_correlation(a, b)
        assert math.isclose(rho, -1.0, rel_tol=1e-9)

    def test_uncorrelated(self) -> None:
        a = [1.0, 0.0, -1.0, 0.0, 1.0, 0.0, -1.0, 0.0]
        b = [0.0, 1.0, 0.0, -1.0, 0.0, 1.0, 0.0, -1.0]
        rho = compute_pearson_correlation(a, b)
        assert math.isclose(rho, 0.0, abs_tol=1e-9)

    def test_insufficient_samples_guard(self) -> None:
        # Fewer than 5 samples -> 0.0
        assert compute_pearson_correlation([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == 0.0

    def test_zero_variance_guard(self) -> None:
        # Flat series
        assert compute_pearson_correlation([5.0] * 10, [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]) == 0.0


class TestCorrelationHeatLockVeto:
    """Verify concurrency cap and correlation heat lock rules."""

    def test_concurrency_cap_veto(self) -> None:
        lock = CorrelationHeatLock(max_active_signals=3)
        # Already 3 active positions
        active = {"BTCUSDT", "ETHUSDT", "SOLUSDT"}
        assert lock.should_veto("XRPUSDT", active_symbols=active) is True

    def test_concurrency_cap_allowed(self) -> None:
        lock = CorrelationHeatLock(max_active_signals=3)
        # Only 2 active positions
        active = {"BTCUSDT", "ETHUSDT"}
        # No returns recorded yet -> corr is 0.0 <= 0.75
        assert lock.should_veto("SOLUSDT", active_symbols=active) is False

    def test_high_correlation_blocks_candidate_signal(self) -> None:
        lock = CorrelationHeatLock(max_correlation=0.75)

        # Feed highly correlated returns between BTC and ETH (rho ~ 0.95)
        btc_returns = [0.01 * (i % 3 - 1) + 0.005 for i in range(20)]
        eth_returns = [0.01 * (i % 3 - 1) + 0.004 for i in range(20)]

        for r_b, r_e in zip(btc_returns, eth_returns):
            lock.add_return("BTCUSDT", r_b)
            lock.add_return("ETHUSDT", r_e)

        corr = lock.get_correlation("ETHUSDT", "BTCUSDT")
        assert corr > 0.75

        # Active position in BTCUSDT -> candidate ETHUSDT must be VETOED
        assert lock.should_veto("ETHUSDT", active_symbols={"BTCUSDT"}) is True

    def test_low_correlation_allows_candidate_signal(self) -> None:
        lock = CorrelationHeatLock(max_correlation=0.75)

        # Uncorrelated returns
        btc_returns = [0.01, -0.01, 0.02, -0.02, 0.01, -0.01, 0.02, -0.02]
        sol_returns = [-0.01, 0.02, 0.01, -0.01, 0.02, 0.01, -0.02, 0.01]

        for r_b, r_s in zip(btc_returns, sol_returns):
            lock.add_return("BTCUSDT", r_b)
            lock.add_return("SOLUSDT", r_s)

        corr = lock.get_correlation("SOLUSDT", "BTCUSDT")
        assert abs(corr) <= 0.75

        assert lock.should_veto("SOLUSDT", active_symbols={"BTCUSDT"}) is False

    def test_add_price_computes_log_returns(self) -> None:
        lock = CorrelationHeatLock()
        prices = [100.0, 102.0, 101.0, 103.0, 105.0]
        for p in prices:
            lock.add_price("SOLUSDT", p)

        # 5 prices -> 4 log returns
        assert len(lock.returns_history["SOLUSDT"]) == 4
        # First return: ln(102 / 100)
        assert math.isclose(lock.returns_history["SOLUSDT"][0], math.log(1.02), rel_tol=1e-9)
