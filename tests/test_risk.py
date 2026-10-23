"""tests/test_risk.py — Comprehensive tests for engine/risk.py.

Verifies:
1. Slots and memory compliance for RiskCalculator and RiskLevels.
2. Stop loss calculation for LONG and SHORT with ATR volatility buffers.
3. Multi-tier take-profit levels: TP1 (1.5R), TP2 (2.8R), TP3 (4.5R).
4. Quarter-Kelly criterion sizing with 1.25% portfolio risk cap and zero floor.
"""

import math
import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine.risk import (
    RiskCalculator,
    RiskLevels,
    compute_stop_loss,
    compute_take_profits,
    compute_kelly_sizing,
    KELLY_CAP,
    KELLY_PAYOFF_B,
    TP1_R_MULT,
    TP2_R_MULT,
    TP3_R_MULT,
)


class TestRiskCalculatorSlots:
    """Verify slots enforcement."""

    def test_has_slots(self) -> None:
        assert hasattr(RiskCalculator, "__slots__")

    def test_no_dict(self) -> None:
        calc = RiskCalculator()
        assert not hasattr(calc, "__dict__")

    def test_repr(self) -> None:
        calc = RiskCalculator()
        r = repr(calc)
        assert "RiskCalculator" in r


class TestStopLossCalculation:
    """Verify volatility-buffered stop loss calculation."""

    def test_long_stop_loss_sweep_lower(self) -> None:
        # Entry = 100.0, ATR = 2.0. Base1 = 100 - 1.5*2 = 97.0
        # Sweep = 96.0 < 97.0 -> base = 96.0
        # SL = 96.0 - 0.35*2.0 = 96.0 - 0.70 = 95.30
        sl = compute_stop_loss("LONG", entry_price=100.0, sweep_price=96.0, atr_14=2.0)
        assert pytest.approx(sl, 1e-6) == 95.30

    def test_long_stop_loss_atr_lower(self) -> None:
        # Sweep = 98.0 > 97.0 -> base = 97.0
        # SL = 97.0 - 0.35*2.0 = 96.30
        sl = compute_stop_loss("LONG", entry_price=100.0, sweep_price=98.0, atr_14=2.0)
        assert pytest.approx(sl, 1e-6) == 96.30

    def test_short_stop_loss_sweep_higher(self) -> None:
        # Entry = 100.0, ATR = 2.0. Base1 = 100 + 1.5*2 = 103.0
        # Sweep = 104.0 > 103.0 -> base = 104.0
        # SL = 104.0 + 0.35*2.0 = 104.70
        sl = compute_stop_loss("SHORT", entry_price=100.0, sweep_price=104.0, atr_14=2.0)
        assert pytest.approx(sl, 1e-6) == 104.70

    def test_short_stop_loss_atr_higher(self) -> None:
        # Sweep = 102.0 < 103.0 -> base = 103.0
        # SL = 103.0 + 0.35*2.0 = 103.70
        sl = compute_stop_loss("SHORT", entry_price=100.0, sweep_price=102.0, atr_14=2.0)
        assert pytest.approx(sl, 1e-6) == 103.70


class TestTakeProfitCalculation:
    """Verify TP1, TP2, TP3 targets."""

    def test_long_take_profits(self) -> None:
        entry = 100.0
        risk_r = 5.0
        tp1, tp2, tp3 = compute_take_profits("LONG", entry, risk_r)

        assert pytest.approx(tp1, 1e-6) == 100.0 + 1.5 * 5.0  # 107.5
        assert pytest.approx(tp2, 1e-6) == 100.0 + 2.8 * 5.0  # 114.0
        assert pytest.approx(tp3, 1e-6) == 100.0 + 4.5 * 5.0  # 122.5

    def test_short_take_profits(self) -> None:
        entry = 100.0
        risk_r = 5.0
        tp1, tp2, tp3 = compute_take_profits("SHORT", entry, risk_r)

        assert pytest.approx(tp1, 1e-6) == 100.0 - 1.5 * 5.0  # 92.5
        assert pytest.approx(tp2, 1e-6) == 100.0 - 2.8 * 5.0  # 86.0
        assert pytest.approx(tp3, 1e-6) == 100.0 - 4.5 * 5.0  # 77.5


class TestKellySizingCalculation:
    """Verify quarter-Kelly criterion and 1.25% maximum cap."""

    def test_high_win_prob_clamped_at_cap(self) -> None:
        # p = 0.85 -> Kelly formula gives ~19% -> clamped to KELLY_CAP (1.25%)
        f = compute_kelly_sizing(win_probability=0.85)
        assert f == KELLY_CAP

    def test_low_win_prob_clamped_at_zero(self) -> None:
        # p = 0.20 -> Negative Kelly edge -> clamped to 0.0
        f = compute_kelly_sizing(win_probability=0.20)
        assert f == 0.0

    def test_intermediate_win_prob(self) -> None:
        # p = 0.30: raw_kelly = 0.30 - (0.70 / 2.45) = 0.30 - 0.285714 = 0.0142857
        # f* = 0.25 * 0.0142857 = 0.0035714 (0.357% < 1.25%)
        f = compute_kelly_sizing(win_probability=0.30)
        expected = 0.25 * (0.30 - (0.70 / 2.45))
        assert pytest.approx(f, 1e-6) == expected
        assert 0.0 < f < KELLY_CAP


class TestRiskCalculatorFullProfile:
    """Verify full risk profile calculation."""

    def test_calculate_risk_long(self) -> None:
        calc = RiskCalculator()
        profile = calc.calculate_risk(
            direction="LONG",
            entry_price=50000.0,
            sweep_price=49000.0,
            atr_14=500.0,
            win_probability=0.85,
        )

        assert profile.direction == "LONG"
        assert profile.entry_price == 50000.0
        assert profile.stop_loss < 50000.0
        assert profile.risk_r > 0.0
        assert profile.tp1 > profile.entry_price
        assert profile.tp2 > profile.tp1
        assert profile.tp3 > profile.tp2
        assert profile.kelly_fraction == 0.0125
