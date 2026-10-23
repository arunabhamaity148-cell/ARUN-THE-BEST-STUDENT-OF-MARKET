"""
engine/cross_asset.py — Cross-Asset Smart Money Divergence (SMT) Detector.

Detects non-confirmations and structural liquidity sweeps between correlated
crypto assets (BTCUSDT, ETHUSDT, SOLUSDT).

Strict Constraints:
- Zero NumPy / Pandas. Uses Python standard library structures.
- All classes use __slots__.
- Pre-computed constants in module-level immutable variables.

Formula for Bullish SMT:
  If Asset A (e.g. BTC) sweeps prior swing low (P_A,t < L_A(t0))
  and Asset B (e.g. ETH) holds firmly above prior swing low (P_B,t > L_B(t0)):
    SMT = ((P_B,t - L_B(t0)) / ATR_B) - ((P_A,t - L_A(t0)) / ATR_A)
  If SMT >= 0.35, flag Bullish SMT regime.
"""

from collections import deque
from typing import NamedTuple

# ─────────────────────────────────────────────────────────────────────
# Pre-computed Constants
# ─────────────────────────────────────────────────────────────────────

SMT_BULLISH_THRESHOLD: float = 0.35
SMT_BEARISH_THRESHOLD: float = 0.35
DEFAULT_ATR_WINDOW: int = 14
DEFAULT_SWING_WINDOW: int = 15      # 15 periods (e.g. 15 x 1m bars for 15m)


# ─────────────────────────────────────────────────────────────────────
# Pure Helper Functions (Fast & Directly Testable)
# ─────────────────────────────────────────────────────────────────────

def calculate_bullish_smt(
    price_a: float,
    low_a: float,
    atr_a: float,
    price_b: float,
    low_b: float,
    atr_b: float,
) -> float:
    """Calculate the Bullish SMT score between Asset A and Asset B.

    Returns the divergence score. When Asset A sweeps low (price_a < low_a)
    and Asset B holds low (price_b > low_b), the score is positive.

    Parameters
    ----------
    price_a : float
        Current price of reference asset (e.g. BTCUSDT).
    low_a : float
        Prior structural swing low of Asset A.
    atr_a : float
        Average True Range of Asset A (normalized scale).
    price_b : float
        Current price of correlated asset (e.g. ETHUSDT or SOLUSDT).
    low_b : float
        Prior structural swing low of Asset B.
    atr_b : float
        Average True Range of Asset B.
    """
    if atr_a <= 0.0 or atr_b <= 0.0:
        return 0.0

    norm_diff_b = (price_b - low_b) / atr_b
    norm_diff_a = (price_a - low_a) / atr_a
    return norm_diff_b - norm_diff_a


def calculate_bearish_smt(
    price_a: float,
    high_a: float,
    atr_a: float,
    price_b: float,
    high_b: float,
    atr_b: float,
) -> float:
    """Calculate the Bearish SMT score between Asset A and Asset B.

    When Asset A sweeps high (price_a > high_a) while Asset B fails to sweep
    (price_b < high_b), returns a positive divergence score.
    """
    if atr_a <= 0.0 or atr_b <= 0.0:
        return 0.0

    norm_diff_a = (price_a - high_a) / atr_a
    norm_diff_b = (price_b - high_b) / atr_b
    return norm_diff_a - norm_diff_b


# ─────────────────────────────────────────────────────────────────────
# SMT Detector Class
# ─────────────────────────────────────────────────────────────────────

class SMTDetector:
    """Tracks structural 15-minute swing levels and ATR across symbols.

    Attributes
    ----------
    swing_lows : dict[str, float]
        Structural swing lows per symbol.
    swing_highs : dict[str, float]
        Structural swing highs per symbol.
    atrs : dict[str, float]
        Current ATR values per symbol.
    recent_bars : dict[str, deque]
        Recent closed bar history (high, low, close) to auto-update ATR & swings.
    """

    __slots__ = (
        "swing_lows",
        "swing_highs",
        "atrs",
        "recent_bars",
        "_threshold",
    )

    def __init__(self, threshold: float = SMT_BULLISH_THRESHOLD) -> None:
        self.swing_lows: dict[str, float] = {}
        self.swing_highs: dict[str, float] = {}
        self.atrs: dict[str, float] = {}
        self.recent_bars: dict[str, deque] = {}
        self._threshold: float = threshold

    def set_levels(
        self,
        symbol: str,
        swing_low: float,
        swing_high: float,
        atr: float,
    ) -> None:
        """Manually or externally set structural swing levels and ATR."""
        self.swing_lows[symbol] = swing_low
        self.swing_highs[symbol] = swing_high
        self.atrs[symbol] = max(1e-8, atr)

    def update_bar(
        self,
        symbol: str,
        high: float,
        low: float,
        close: float,
    ) -> None:
        """Update recent bar deque and dynamically maintain ATR and swing extremes.

        Parameters
        ----------
        symbol : str
            Symbol name.
        high, low, close : float
            Price bounds of the closed 1m/15m bar.
        """
        if symbol not in self.recent_bars:
            self.recent_bars[symbol] = deque(maxlen=DEFAULT_SWING_WINDOW)

        bars = self.recent_bars[symbol]
        prev_close = bars[-1][2] if bars else close
        bars.append((high, low, close))

        # True Range: max(H-L, |H-Cp|, |L-Cp|)
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))

        # Rolling average ATR
        prev_atr = self.atrs.get(symbol, tr)
        # Smoothing (alpha = 1 / 14)
        new_atr = (prev_atr * (DEFAULT_ATR_WINDOW - 1) + tr) / DEFAULT_ATR_WINDOW
        self.atrs[symbol] = max(1e-8, new_atr)

        # Dynamic structural swing low & high over window
        self.swing_lows[symbol] = min(b[1] for b in bars)
        self.swing_highs[symbol] = max(b[0] for b in bars)

    def check_bullish_smt(
        self,
        symbol_a: str,
        symbol_b: str,
        price_a: float,
        price_b: float,
    ) -> tuple[bool, float]:
        """Check if Bullish SMT is triggered between symbol_a and symbol_b.

        Condition:
          Asset A sweeps prior low (price_a < low_a)
          Asset B holds above prior low (price_b > low_b)
          SMT score >= threshold (0.35)

        Returns
        -------
        tuple[bool, float]
            (is_bullish_smt, smt_score)
        """
        low_a = self.swing_lows.get(symbol_a)
        low_b = self.swing_lows.get(symbol_b)
        atr_a = self.atrs.get(symbol_a)
        atr_b = self.atrs.get(symbol_b)

        if low_a is None or low_b is None or atr_a is None or atr_b is None:
            return False, 0.0

        # Structural condition: A sweeps low while B holds above low
        swept_a = price_a < low_a
        held_b = price_b > low_b

        score = calculate_bullish_smt(price_a, low_a, atr_a, price_b, low_b, atr_b)

        is_triggered = swept_a and held_b and (score >= self._threshold)
        return is_triggered, score

    def check_bearish_smt(
        self,
        symbol_a: str,
        symbol_b: str,
        price_a: float,
        price_b: float,
    ) -> tuple[bool, float]:
        """Check if Bearish SMT is triggered between symbol_a and symbol_b.

        Condition:
          Asset A sweeps prior high (price_a > high_a)
          Asset B holds below prior high (price_b < high_b)
          SMT score >= threshold (0.35)

        Returns
        -------
        tuple[bool, float]
            (is_bearish_smt, smt_score)
        """
        high_a = self.swing_highs.get(symbol_a)
        high_b = self.swing_highs.get(symbol_b)
        atr_a = self.atrs.get(symbol_a)
        atr_b = self.atrs.get(symbol_b)

        if high_a is None or high_b is None or atr_a is None or atr_b is None:
            return False, 0.0

        swept_a = price_a > high_a
        held_b = price_b < high_b

        score = calculate_bearish_smt(price_a, high_a, atr_a, price_b, high_b, atr_b)

        is_triggered = swept_a and held_b and (score >= self._threshold)
        return is_triggered, score

    def __repr__(self) -> str:
        return (
            f"SMTDetector(tracked_symbols={list(self.swing_lows.keys())}, "
            f"threshold={self._threshold})"
        )
