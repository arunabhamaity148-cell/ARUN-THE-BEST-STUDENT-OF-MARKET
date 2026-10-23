"""tests/test_derivatives.py — Comprehensive tests for engine/derivatives.py.

Verifies:
1. Slots and memory compliance for DerivativesRegimeClassifier.
2. Short Squeeze Setup detection:
   - Price flat or declining.
   - Rapid OI expansion (> 1.5%).
   - Backwardation basis (< -4.0 bps).
   - Negative funding rate (< -0.01% / -0.0001).
3. Long Trap Setup detection:
   - Price spiking into resistance (> 0.0%).
   - OI surge (> 2.0%).
   - Contango basis (> +8.0 bps).
   - Crowded positive funding (> +0.03% / +0.0003).
4. Neutral regime classification when conditions are unmet.
5. Funding rate normalization (accepting decimal or percentage formats).
"""

import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine.derivatives import (
    DerivativesRegimeClassifier,
    is_short_squeeze,
    is_long_trap,
    _normalize_funding_rate,
    REGIME_SHORT_SQUEEZE,
    REGIME_LONG_TRAP,
    REGIME_NEUTRAL,
)


class TestDerivativesClassifierSlots:
    """Verify slots enforcement."""

    def test_has_slots(self) -> None:
        assert hasattr(DerivativesRegimeClassifier, "__slots__")

    def test_no_dict(self) -> None:
        classifier = DerivativesRegimeClassifier()
        assert not hasattr(classifier, "__dict__")


class TestFundingRateNormalization:
    """Verify decimal fraction and percentage input handling."""

    def test_decimal_kept_intact(self) -> None:
        assert _normalize_funding_rate(0.0003) == 0.0003
        assert _normalize_funding_rate(-0.00015) == -0.00015

    def test_percentage_converted_to_decimal(self) -> None:
        # User passes 0.03 for 0.03%
        assert pytest.approx(_normalize_funding_rate(0.03), 1e-6) == 0.0003
        assert pytest.approx(_normalize_funding_rate(-0.02), 1e-6) == -0.0002


class TestShortSqueezeDetection:
    """Verify Short Squeeze setup classification."""

    def test_short_squeeze_valid_setup(self) -> None:
        classifier = DerivativesRegimeClassifier()
        # Price declining (-0.5%), OI +2.2%, Basis -5.5 bps, Funding -0.0002 (-0.02%)
        regime = classifier.classify(
            delta_oi_pct=2.2,
            basis_bps=-5.5,
            funding_rate=-0.0002,
            price_change_pct=-0.5,
        )
        assert regime == REGIME_SHORT_SQUEEZE

    def test_short_squeeze_flat_price(self) -> None:
        classifier = DerivativesRegimeClassifier()
        # Price exactly flat (0.0%)
        regime = classifier.classify(
            delta_oi_pct=1.8,
            basis_bps=-4.5,
            funding_rate=-0.00015,
            price_change_pct=0.0,
        )
        assert regime == REGIME_SHORT_SQUEEZE

    def test_short_squeeze_fails_if_price_rising(self) -> None:
        # Price rising is not short squeeze entry
        assert is_short_squeeze(
            delta_oi_pct=2.5,
            basis_bps=-6.0,
            funding_rate=-0.0003,
            price_change_pct=0.8,
        ) is False

    def test_short_squeeze_fails_if_oi_insufficient(self) -> None:
        # OI +1.0% <= 1.5%
        assert is_short_squeeze(
            delta_oi_pct=1.0,
            basis_bps=-6.0,
            funding_rate=-0.0003,
            price_change_pct=-0.2,
        ) is False

    def test_short_squeeze_fails_if_basis_positive(self) -> None:
        # Basis +1.0 bps >= -4.0 bps
        assert is_short_squeeze(
            delta_oi_pct=2.0,
            basis_bps=1.0,
            funding_rate=-0.0003,
            price_change_pct=-0.2,
        ) is False

    def test_short_squeeze_fails_if_funding_positive(self) -> None:
        assert is_short_squeeze(
            delta_oi_pct=2.0,
            basis_bps=-5.0,
            funding_rate=0.0001,
            price_change_pct=-0.2,
        ) is False


class TestLongTrapDetection:
    """Verify Long Trap setup classification."""

    def test_long_trap_valid_setup(self) -> None:
        classifier = DerivativesRegimeClassifier()
        # Price spiking (+1.2%), OI +3.0%, Basis +12.0 bps, Funding +0.0004 (+0.04%)
        regime = classifier.classify(
            delta_oi_pct=3.0,
            basis_bps=12.0,
            funding_rate=0.0004,
            price_change_pct=1.2,
        )
        assert regime == REGIME_LONG_TRAP

    def test_long_trap_fails_if_price_declining(self) -> None:
        assert is_long_trap(
            delta_oi_pct=3.0,
            basis_bps=10.0,
            funding_rate=0.0004,
            price_change_pct=-0.5,
        ) is False

    def test_long_trap_fails_if_oi_insufficient(self) -> None:
        # OI 1.8% <= 2.0%
        assert is_long_trap(
            delta_oi_pct=1.8,
            basis_bps=10.0,
            funding_rate=0.0004,
            price_change_pct=1.0,
        ) is False

    def test_long_trap_fails_if_basis_insufficient(self) -> None:
        # Basis +6.0 bps <= +8.0 bps
        assert is_long_trap(
            delta_oi_pct=2.5,
            basis_bps=6.0,
            funding_rate=0.0004,
            price_change_pct=1.0,
        ) is False

    def test_long_trap_fails_if_funding_low(self) -> None:
        # Funding +0.0001 <= +0.0003
        assert is_long_trap(
            delta_oi_pct=2.5,
            basis_bps=10.0,
            funding_rate=0.0001,
            price_change_pct=1.0,
        ) is False


class TestNeutralRegime:
    """Verify fallback to NEUTRAL when neither setup is active."""

    def test_neutral_market(self) -> None:
        classifier = DerivativesRegimeClassifier()
        regime = classifier.classify(
            delta_oi_pct=0.2,
            basis_bps=1.0,
            funding_rate=0.0001,
            price_change_pct=0.1,
        )
        assert regime == REGIME_NEUTRAL
