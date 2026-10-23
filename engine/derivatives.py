"""
engine/derivatives.py — Derivatives Positioning & Regime Classifier.

Uses on-demand Open Interest dynamics, funding rate extremes, and spot-perp
basis to classify the institutional positioning regime.

Setups
------
1. Short Squeeze Setup:
   - Price flat or declining (price_change_pct <= 0.0).
   - Rapid OI expansion (delta_oi_pct > 1.5%).
   - Backwardation basis (basis_bps < -4.0).
   - Negative funding rate (funding_rate < -0.01% / -0.0001).

2. Long Trap Setup:
   - Price spikes into resistance (price_change_pct > 0.0).
   - OI surges (delta_oi_pct > 2.0%).
   - Contango basis (basis_bps > +8.0).
   - Crowded positive funding rate (funding_rate > +0.03% / +0.0003).

Strict Constraints:
- Zero NumPy / Pandas.
- All classes use __slots__.
"""

from typing import Literal

# ─────────────────────────────────────────────────────────────────────
# Regime Constants
# ─────────────────────────────────────────────────────────────────────

REGIME_SHORT_SQUEEZE = "SHORT_SQUEEZE"
REGIME_LONG_TRAP = "LONG_TRAP"
REGIME_NEUTRAL = "NEUTRAL"

RegimeType = Literal["SHORT_SQUEEZE", "LONG_TRAP", "NEUTRAL"]

# Thresholds
SHORT_SQUEEZE_OI_THRESHOLD: float = 1.5       # +1.5%
SHORT_SQUEEZE_BASIS_THRESHOLD: float = -4.0    # -4.0 bps
SHORT_SQUEEZE_FUNDING_THRESHOLD: float = -0.0001  # -0.01% (-0.0001 decimal)

LONG_TRAP_OI_THRESHOLD: float = 2.0           # +2.0%
LONG_TRAP_BASIS_THRESHOLD: float = 8.0        # +8.0 bps
LONG_TRAP_FUNDING_THRESHOLD: float = 0.0003   # +0.03% (+0.0003 decimal)


# ─────────────────────────────────────────────────────────────────────
# Pure Helper Function
# ─────────────────────────────────────────────────────────────────────

def _normalize_funding_rate(funding_rate: float) -> float:
    """Normalize funding rate to decimal fraction (e.g. -0.01% -> -0.0001).

    If the caller passed percentage (e.g. -0.02 for -0.02%), converts to -0.0002.
    If already decimal fraction (|x| < 0.005), keeps as is.
    """
    if abs(funding_rate) >= 0.005:
        return funding_rate / 100.0
    return funding_rate


def is_short_squeeze(
    delta_oi_pct: float,
    basis_bps: float,
    funding_rate: float,
    price_change_pct: float = 0.0,
) -> bool:
    """Return True if conditions match a Short Squeeze Setup.

    - price_change_pct <= 0.0 (price flat or declining)
    - delta_oi_pct > 1.5%
    - basis_bps < -4.0 bps
    - funding_rate < -0.01% (-0.0001 decimal)
    """
    fr = _normalize_funding_rate(funding_rate)
    return (
        price_change_pct <= 0.0
        and delta_oi_pct > SHORT_SQUEEZE_OI_THRESHOLD
        and basis_bps < SHORT_SQUEEZE_BASIS_THRESHOLD
        and fr < SHORT_SQUEEZE_FUNDING_THRESHOLD
    )


def is_long_trap(
    delta_oi_pct: float,
    basis_bps: float,
    funding_rate: float,
    price_change_pct: float = 0.01,
) -> bool:
    """Return True if conditions match a Long Trap Setup.

    - price_change_pct > 0.0 (price spiking)
    - delta_oi_pct > 2.0%
    - basis_bps > +8.0 bps
    - funding_rate > +0.03% (+0.0003 decimal)
    """
    fr = _normalize_funding_rate(funding_rate)
    return (
        price_change_pct > 0.0
        and delta_oi_pct > LONG_TRAP_OI_THRESHOLD
        and basis_bps > LONG_TRAP_BASIS_THRESHOLD
        and fr > LONG_TRAP_FUNDING_THRESHOLD
    )


# ─────────────────────────────────────────────────────────────────────
# Classifier Class
# ─────────────────────────────────────────────────────────────────────

class DerivativesRegimeClassifier:
    """Classifies futures market regime based on derivatives state."""

    __slots__ = ()

    def classify(
        self,
        delta_oi_pct: float,
        basis_bps: float,
        funding_rate: float,
        price_change_pct: float = 0.0,
    ) -> RegimeType:
        """Classify current market condition into SHORT_SQUEEZE, LONG_TRAP, or NEUTRAL.

        Parameters
        ----------
        delta_oi_pct : float
            3-period open interest growth percentage (e.g. 2.1 for +2.1%).
        basis_bps : float
            Perp vs index basis in basis points.
        funding_rate : float
            Current 8-hour funding rate (decimal or percentage).
        price_change_pct : float
            Price change percentage over the observation window (default 0.0).

        Returns
        -------
        RegimeType
            "SHORT_SQUEEZE", "LONG_TRAP", or "NEUTRAL"
        """
        if is_short_squeeze(delta_oi_pct, basis_bps, funding_rate, price_change_pct):
            return REGIME_SHORT_SQUEEZE

        if is_long_trap(delta_oi_pct, basis_bps, funding_rate, price_change_pct):
            return REGIME_LONG_TRAP

        return REGIME_NEUTRAL
