"""tests/test_cross_asset.py — Comprehensive tests for engine/cross_asset.py.

Verifies:
1. Slots and memory compliance for SMTDetector.
2. Pure mathematical calculation of Bullish and Bearish SMT divergence.
3. SMT trigger threshold (>= 0.35) and structural sweep/hold conditions.
4. Bar-by-bar dynamic ATR and swing extreme calculation without NumPy/Pandas.
"""

import math
import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine.cross_asset import (
    SMTDetector,
    calculate_bullish_smt,
    calculate_bearish_smt,
    SMT_BULLISH_THRESHOLD,
    SMT_BEARISH_THRESHOLD,
)


class TestSMTDetectorSlots:
    """Verify slots enforcement."""

    def test_has_slots(self) -> None:
        assert hasattr(SMTDetector, "__slots__")

    def test_no_dict(self) -> None:
        detector = SMTDetector()
        assert not hasattr(detector, "__dict__")

    def test_repr(self) -> None:
        detector = SMTDetector()
        r = repr(detector)
        assert "SMTDetector" in r


class TestSMTPureFormulas:
    """Verify math for Bullish and Bearish SMT divergence."""

    def test_bullish_smt_formula(self) -> None:
        # Asset A (BTC): Price 49800 sweeps prior low 50000 (-200), ATR = 500 -> norm = -0.4
        # Asset B (ETH): Price 3050 holds above prior low 3000 (+50), ATR = 100 -> norm = +0.5
        # SMT = 0.5 - (-0.4) = 0.9
        smt = calculate_bullish_smt(
            price_a=49800.0, low_a=50000.0, atr_a=500.0,
            price_b=3050.0, low_b=3000.0, atr_b=100.0,
        )
        assert math.isclose(smt, 0.9, rel_tol=1e-9)

    def test_bearish_smt_formula(self) -> None:
        # Asset A: Price 50500 sweeps prior high 50000 (+500), ATR = 500 -> norm = +1.0
        # Asset B: Price 2950 fails to break prior high 3000 (-50), ATR = 100 -> norm = -0.5
        # SMT = 1.0 - (-0.5) = 1.5
        smt = calculate_bearish_smt(
            price_a=50500.0, high_a=50000.0, atr_a=500.0,
            price_b=2950.0, high_b=3000.0, atr_b=100.0,
        )
        assert math.isclose(smt, 1.5, rel_tol=1e-9)

    def test_zero_atr_guard(self) -> None:
        assert calculate_bullish_smt(100.0, 100.0, 0.0, 200.0, 200.0, 10.0) == 0.0
        assert calculate_bearish_smt(100.0, 100.0, 10.0, 200.0, 200.0, 0.0) == 0.0


class TestSMTDetectorLogic:
    """Verify stateful SMT detection against structural conditions."""

    def test_bullish_smt_triggers_above_threshold(self) -> None:
        detector = SMTDetector(threshold=0.35)
        detector.set_levels("BTCUSDT", swing_low=50000.0, swing_high=52000.0, atr=500.0)
        detector.set_levels("ETHUSDT", swing_low=3000.0, swing_high=3200.0, atr=100.0)

        # BTC sweeps low (49900 < 50000), ETH holds low (3030 > 3000)
        # norm_a = -100 / 500 = -0.2
        # norm_b = 30 / 100 = +0.3
        # score = 0.3 - (-0.2) = 0.5 >= 0.35
        is_triggered, score = detector.check_bullish_smt(
            "BTCUSDT", "ETHUSDT", price_a=49900.0, price_b=3030.0
        )
        assert is_triggered is True
        assert math.isclose(score, 0.5, rel_tol=1e-9)

    def test_bullish_smt_blocks_when_asset_a_did_not_sweep(self) -> None:
        detector = SMTDetector(threshold=0.35)
        detector.set_levels("BTCUSDT", swing_low=50000.0, swing_high=52000.0, atr=500.0)
        detector.set_levels("ETHUSDT", swing_low=3000.0, swing_high=3200.0, atr=100.0)

        # BTC did NOT sweep low (50100 > 50000)
        is_triggered, score = detector.check_bullish_smt(
            "BTCUSDT", "ETHUSDT", price_a=50100.0, price_b=3050.0
        )
        assert is_triggered is False

    def test_bullish_smt_blocks_when_both_assets_swept(self) -> None:
        detector = SMTDetector(threshold=0.35)
        detector.set_levels("BTCUSDT", swing_low=50000.0, swing_high=52000.0, atr=500.0)
        detector.set_levels("ETHUSDT", swing_low=3000.0, swing_high=3200.0, atr=100.0)

        # Both BTC and ETH swept lows (confirmed breakdown, not divergence)
        is_triggered, score = detector.check_bullish_smt(
            "BTCUSDT", "ETHUSDT", price_a=49900.0, price_b=2990.0
        )
        assert is_triggered is False

    def test_bullish_smt_blocks_when_divergence_below_threshold(self) -> None:
        detector = SMTDetector(threshold=0.35)
        detector.set_levels("BTCUSDT", swing_low=50000.0, swing_high=52000.0, atr=500.0)
        detector.set_levels("ETHUSDT", swing_low=3000.0, swing_high=3200.0, atr=100.0)

        # BTC sweeps by 20 (-0.04), ETH holds by 5 (+0.05) -> score = 0.09 < 0.35
        is_triggered, score = detector.check_bullish_smt(
            "BTCUSDT", "ETHUSDT", price_a=49980.0, price_b=3005.0
        )
        assert is_triggered is False
        assert score < 0.35

    def test_bearish_smt_triggers(self) -> None:
        detector = SMTDetector(threshold=0.35)
        detector.set_levels("BTCUSDT", swing_low=50000.0, swing_high=52000.0, atr=500.0)
        detector.set_levels("ETHUSDT", swing_low=3000.0, swing_high=3200.0, atr=100.0)

        # BTC sweeps high (52300 > 52000), ETH holds below high (3180 < 3200)
        # norm_a = (52300 - 52000) / 500 = +0.6
        # norm_b = (3180 - 3200) / 100 = -0.2
        # score = 0.6 - (-0.2) = 0.8 >= 0.35
        is_triggered, score = detector.check_bearish_smt(
            "BTCUSDT", "ETHUSDT", price_a=52300.0, price_b=3180.0
        )
        assert is_triggered is True
        assert math.isclose(score, 0.8, rel_tol=1e-9)

    def test_missing_levels_returns_false(self) -> None:
        detector = SMTDetector()
        is_triggered, score = detector.check_bullish_smt("BTCUSDT", "ETHUSDT", 50000.0, 3000.0)
        assert is_triggered is False
        assert score == 0.0


class TestSMTDetectorBarUpdates:
    """Verify dynamic ATR and swing levels from incoming bar sequence."""

    def test_bar_updates_atr_and_swings(self) -> None:
        detector = SMTDetector()
        bars = [
            (105.0, 95.0, 100.0),
            (108.0, 98.0, 102.0),
            (103.0, 92.0, 96.0),
            (110.0, 96.0, 105.0),
            (102.0, 90.0, 92.0),
        ]
        for h, l, c in bars:
            detector.update_bar("SOLUSDT", high=h, low=l, close=c)

        # Structural extremes over window
        assert detector.swing_highs["SOLUSDT"] == 110.0
        assert detector.swing_lows["SOLUSDT"] == 90.0
        assert detector.atrs["SOLUSDT"] > 0.0
