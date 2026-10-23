"""
engine/risk.py — Volatility-Adjusted Entry, SL, TP, and Kelly Risk Sizing.

Calculates:
1. Volatility-Adjusted Stop Loss (SL):
   - LONG:  min(P_sweep, P_entry - 1.5 * ATR_14) - 0.35 * ATR_14
   - SHORT: max(P_sweep, P_entry + 1.5 * ATR_14) + 0.35 * ATR_14
2. Multi-Tiered Take Profits (TP):
   - TP1 = Entry ± 1.5R (40% scale-out)
   - TP2 = Entry ± 2.8R (40% scale-out)
   - TP3 = Entry ± 4.5R (20% runner)
   where R = |P_entry - SL|
3. Fractional Kelly Criterion Position Sizing:
   - f* = min(0.0125, 0.25 * (p - (1 - p) / b))
   where p is Win Probability and b = 2.45 (risk/reward ratio parameter).

Strict Constraints:
- Zero NumPy / Pandas.
- __slots__ on all classes.
"""

from typing import NamedTuple, Literal

# ─────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────

KELLY_PAYOFF_B: float = 2.45             # Payoff ratio b
KELLY_FRACTION_MULTIPLIER: float = 0.25  # Quarter-Kelly scaling
KELLY_CAP: float = 0.0125                # 1.25% max portfolio risk cap

TP1_R_MULT: float = 1.5                  # 1.5R (40%)
TP2_R_MULT: float = 2.8                  # 2.8R (40%)
TP3_R_MULT: float = 4.5                  # 4.5R (20%)

SL_ATR_MULT_BASE: float = 1.5
SL_ATR_BUFFER: float = 0.35

DirectionType = Literal["LONG", "SHORT"]


# ─────────────────────────────────────────────────────────────────────
# Data Container
# ─────────────────────────────────────────────────────────────────────

class RiskLevels(NamedTuple):
    direction: DirectionType
    entry_price: float
    stop_loss: float
    risk_r: float
    tp1: float
    tp2: float
    tp3: float
    kelly_fraction: float


# ─────────────────────────────────────────────────────────────────────
# Pure Helper Functions
# ─────────────────────────────────────────────────────────────────────

def compute_stop_loss(
    direction: DirectionType,
    entry_price: float,
    sweep_price: float,
    atr_14: float,
) -> float:
    """Calculate volatility-buffered stop loss based on entry, sweep, and ATR."""
    if direction == "LONG":
        base = min(sweep_price, entry_price - SL_ATR_MULT_BASE * atr_14)
        return base - SL_ATR_BUFFER * atr_14
    else:  # SHORT
        base = max(sweep_price, entry_price + SL_ATR_MULT_BASE * atr_14)
        return base + SL_ATR_BUFFER * atr_14


def compute_take_profits(
    direction: DirectionType,
    entry_price: float,
    risk_r: float,
) -> tuple[float, float, float]:
    """Calculate TP1 (1.5R), TP2 (2.8R), and TP3 (4.5R)."""
    if direction == "LONG":
        tp1 = entry_price + TP1_R_MULT * risk_r
        tp2 = entry_price + TP2_R_MULT * risk_r
        tp3 = entry_price + TP3_R_MULT * risk_r
    else:  # SHORT
        tp1 = entry_price - TP1_R_MULT * risk_r
        tp2 = entry_price - TP2_R_MULT * risk_r
        tp3 = entry_price - TP3_R_MULT * risk_r
    return tp1, tp2, tp3


def compute_kelly_sizing(
    win_probability: float,
    payoff_b: float = KELLY_PAYOFF_B,
    multiplier: float = KELLY_FRACTION_MULTIPLIER,
    cap: float = KELLY_CAP,
    risk_multiplier: float = 1.0,
) -> float:
    """Calculate quarter-Kelly position fraction: f* = min(cap, mult * (p - (1-p)/b)) * risk_multiplier."""
    p = win_probability
    if p <= 0.0 or payoff_b <= 0.0:
        return 0.0

    raw_kelly = p - ((1.0 - p) / payoff_b)
    if raw_kelly <= 0.0:
        return 0.0

    r_mult = max(0.0, risk_multiplier)
    scaled_kelly = multiplier * raw_kelly * r_mult
    return min(cap * r_mult, max(0.0, scaled_kelly))


# ─────────────────────────────────────────────────────────────────────
# Risk Calculator Class
# ─────────────────────────────────────────────────────────────────────

class RiskCalculator:
    """Calculates entry, stop loss, take-profit targets, and Kelly sizing."""

    __slots__ = (
        "_payoff_b",
        "_kelly_multiplier",
        "_kelly_cap",
    )

    def __init__(
        self,
        payoff_b: float = KELLY_PAYOFF_B,
        kelly_multiplier: float = KELLY_FRACTION_MULTIPLIER,
        kelly_cap: float = KELLY_CAP,
    ) -> None:
        self._payoff_b: float = payoff_b
        self._kelly_multiplier: float = kelly_multiplier
        self._kelly_cap: float = kelly_cap

    def calculate_risk(
        self,
        direction: DirectionType,
        entry_price: float,
        sweep_price: float,
        atr_14: float,
        win_probability: float,
        risk_multiplier: float = 1.0,
    ) -> RiskLevels:
        """Compute full risk profile for trade signal.

        Parameters
        ----------
        direction : "LONG" or "SHORT"
        entry_price : float
            Current market entry mid/ask/bid price.
        sweep_price : float
            Extremum of the structural liquidity sweep.
        atr_14 : float
            14-period Average True Range.
        win_probability : float
            Bayesian P(Win) from synthesizer.
        risk_multiplier : float, optional
            Portfolio scaling multiplier from Spectral Risk Gate (default 1.0).

        Returns
        -------
        RiskLevels
            Tuple with direction, entry, stop_loss, risk_r, tp1, tp2, tp3, kelly_fraction.
        """
        # Ensure positive ATR
        atr = max(1e-8, atr_14)

        sl = compute_stop_loss(direction, entry_price, sweep_price, atr)
        risk_r = abs(entry_price - sl)
        if risk_r <= 0.0:
            risk_r = 1.85 * atr

        tp1, tp2, tp3 = compute_take_profits(direction, entry_price, risk_r)
        kelly = compute_kelly_sizing(
            win_probability,
            self._payoff_b,
            self._kelly_multiplier,
            self._kelly_cap,
            risk_multiplier=risk_multiplier,
        )

        return RiskLevels(
            direction=direction,
            entry_price=entry_price,
            stop_loss=sl,
            risk_r=risk_r,
            tp1=tp1,
            tp2=tp2,
            tp3=tp3,
            kelly_fraction=kelly,
        )

    def __repr__(self) -> str:
        return (
            f"RiskCalculator(b={self._payoff_b}, "
            f"kelly_cap={self._kelly_cap})"
        )
