"""
engine/heat_lock.py — Cross-Asset Correlation Heat Lock.

Maintains a rolling 60-minute 1-minute log-return correlation matrix across
tracked futures symbols to protect against systemic portfolio overexposure.

Correlation Heat Lock Veto:
- Rejects candidate signals if:
  1. max(|rho|) > 0.75 against any currently active trade signal.
  2. Number of active signals >= 3 (concurrency cap).

Strict Constraints:
- Zero NumPy / Pandas / SciPy.
- Standard Python math and collections.deque.
- __slots__ on all classes.
"""

import math
from collections import deque
from typing import Collection

# ─────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────

MAX_CORRELATION_THRESHOLD: float = 0.75
MAX_ACTIVE_SIGNALS: int = 3
ROLLING_RETURN_WINDOW: int = 60          # 60 1-minute intervals (1 hour)
MIN_PERIODS_FOR_CORR: int = 5            # minimum points for valid correlation


# ─────────────────────────────────────────────────────────────────────
# Pure Helper Functions
# ─────────────────────────────────────────────────────────────────────

def compute_pearson_correlation(series_a: list[float], series_b: list[float]) -> float:
    """Compute Pearson correlation coefficient between two equal-length series.

    Returns 0.0 if series have insufficient points (< 5) or zero variance.
    """
    n = min(len(series_a), len(series_b))
    if n < MIN_PERIODS_FOR_CORR:
        return 0.0

    a = series_a[-n:]
    b = series_b[-n:]

    mean_a = sum(a) / n
    mean_b = sum(b) / n

    cov = 0.0
    var_a = 0.0
    var_b = 0.0

    for i in range(n):
        da = a[i] - mean_a
        db = b[i] - mean_b
        cov += da * db
        var_a += da * da
        var_b += db * db

    denom = math.sqrt(var_a * var_b)
    if denom < 1e-12:
        return 0.0

    corr = cov / denom
    # Clamp to [-1.0, 1.0] to guard float inaccuracies
    return max(-1.0, min(1.0, corr))


# ─────────────────────────────────────────────────────────────────────
# Correlation Heat Lock Class
# ─────────────────────────────────────────────────────────────────────

class CorrelationHeatLock:
    """Tracks cross-asset rolling 1-minute log returns and enforces heat lock veto."""

    __slots__ = (
        "returns_history",
        "last_prices",
        "_max_corr",
        "_max_active",
        "_window",
    )

    def __init__(
        self,
        max_correlation: float = MAX_CORRELATION_THRESHOLD,
        max_active_signals: int = MAX_ACTIVE_SIGNALS,
        window: int = ROLLING_RETURN_WINDOW,
    ) -> None:
        self.returns_history: dict[str, deque] = {}
        self.last_prices: dict[str, float] = {}
        self._max_corr: float = max_correlation
        self._max_active: int = max_active_signals
        self._window: int = window

    def add_return(self, symbol: str, log_return: float) -> None:
        """Add a calculated 1-minute log return to the symbol's buffer."""
        if symbol not in self.returns_history:
            self.returns_history[symbol] = deque(maxlen=self._window)
        self.returns_history[symbol].append(log_return)

    def add_price(self, symbol: str, price: float) -> None:
        """Add a 1-minute close price, calculating log-return against previous price."""
        if price <= 0.0:
            return

        prev_price = self.last_prices.get(symbol)
        if prev_price is not None and prev_price > 0.0:
            log_ret = math.log(price / prev_price)
            self.add_return(symbol, log_ret)

        self.last_prices[symbol] = price

    def get_correlation(self, symbol_a: str, symbol_b: str) -> float:
        """Calculate current correlation between symbol_a and symbol_b."""
        if symbol_a == symbol_b:
            return 1.0

        hist_a = self.returns_history.get(symbol_a)
        hist_b = self.returns_history.get(symbol_b)

        if not hist_a or not hist_b:
            return 0.0

        return compute_pearson_correlation(list(hist_a), list(hist_b))

    def should_veto(
        self,
        candidate_symbol: str,
        active_symbols: Collection[str] | None = None,
    ) -> bool:
        """Return True if candidate signal should be vetoed by correlation heat lock.

        Parameters
        ----------
        candidate_symbol : str
            Symbol for the prospective new trade.
        active_symbols : Collection[str]
            Set or list of symbols with active, open positions/signals.
        """
        if active_symbols is None:
            return False

        # Rule 1: Concurrency Cap
        if len(active_symbols) >= self._max_active:
            return True

        # Rule 2: Correlation Heat Check (|rho| > 0.75 against any active trade)
        for active_sym in active_symbols:
            corr = self.get_correlation(candidate_symbol, active_sym)
            if abs(corr) > self._max_corr:
                return True

        return False

    def __repr__(self) -> str:
        return (
            f"CorrelationHeatLock(tracked_symbols={len(self.returns_history)}, "
            f"max_corr={self._max_corr}, max_active={self._max_active})"
        )
