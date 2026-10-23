"""
engine/spectral_risk.py — Pure-Python Spectral Risk & Mantegna Clustering Engine (Phase 6C).

Formulas & Architecture
-----------------------
1. Zero-Allocation Flat C-Array EWMA Covariance Tensor:
   - Upper-triangular 10x10 matrix stored in array.array('d', [0.0] * 55) (440 bytes).
   - Mapping: _flat_idx(i, j) = i * n - (i * (i - 1)) // 2 + (j - i) for i <= j.
   - EWMA decay: alpha = 2.0 / (60 + 1.0) (60-minute half-life span).
   - Covariance update: cov = (1 - alpha) * (cov + alpha * delta_i * delta_j).

2. Power-Iteration Spectral Absorption Ratio (AR):
   - Computes dominant eigenvalue lambda_max & eigenvector v via 8 Power Method iterations.
   - Rayleigh quotient: lambda_max = sum(v_i * (C * v)_i).
   - Absorption Ratio: AR = min(1.0, max(1.0/n, lambda_max / n)).

3. Mantegna Angular Distance & 4-Gate Risk Engine:
   - Gate 1 (Systemic Contagion): Veto altcoin breakouts if AR >= 0.72.
   - Gate 2 (Portfolio Heat Cap): Max 4 positions if AR < 0.58, else max 2.
   - Gate 3 (Mantegna Cluster Collision): dist = sqrt(0.5 * (1 - dir_i * dir_j * rho)). Veto if dist <= 0.35.
   - Gate 4 (Eigen-Beta Sizing): eigen_loading = n * (v_i^2), risk_mult = 1.0 - 0.55 * AR * eigen_loading.

Strict Constraints:
- Zero NumPy, zero Pandas, zero SciPy.
- Pure Python array.array('d') and math.
- __slots__ on all classes.
"""

import math
from array import array
from typing import Sequence

from config import SYMBOLS


# ─────────────────────────────────────────────────────────────────────
# Pure Helper Functions (Stateless Mathematical Functions)
# ─────────────────────────────────────────────────────────────────────

def flat_triangular_index(i: int, j: int, n: int = 10) -> int:
    """Map symmetric 2D matrix indices (i, j) to 1D upper-triangular array index.

    For n=10:
      (0, 0) -> 0, (0, 9) -> 9
      (1, 1) -> 10, (1, 9) -> 18
      ...
      (9, 9) -> 54
    """
    if i > j:
        i, j = j, i
    return i * n - (i * (i - 1)) // 2 + (j - i)


def compute_mantegna_distance(directional_rho: float) -> float:
    """Compute Mantegna metric distance: dist = sqrt(0.5 * (1 - directional_rho))."""
    clamped_rho = max(-1.0, min(1.0, directional_rho))
    return math.sqrt(max(0.0, 0.5 * (1.0 - clamped_rho)))


def compute_eigen_loading(eigenvector_component: float, n: int = 10) -> float:
    """Compute asset eigen-loading: n * (v_i^2)."""
    return n * (eigenvector_component ** 2)


def compute_risk_multiplier(ar: float, eigen_loading: float) -> float:
    """Compute position risk multiplier: max(0.25, min(1.0, 1.0 - 0.55 * AR * eigen_loading))."""
    mult = 1.0 - 0.55 * ar * eigen_loading
    return max(0.25, min(1.0, mult))


# ─────────────────────────────────────────────────────────────────────
# PurePythonSpectralRiskEngine Class
# ─────────────────────────────────────────────────────────────────────

class PurePythonSpectralRiskEngine:
    """Zero-allocation EWMA Covariance, Power-Iteration Spectral Absorption Ratio,
    and Mantegna Angular Distance Portfolio Gate.
    """

    __slots__ = (
        "symbols",
        "sym_to_idx",
        "n",
        "alpha",
        "cov_flat",
        "means",
        "eig_vec",
        "absorption_ratio",
        "_last_prices",
    )

    def __init__(
        self,
        symbols: tuple[str, ...] = SYMBOLS,
        span_minutes: float = 60.0,
    ) -> None:
        self.symbols: tuple[str, ...] = symbols
        self.sym_to_idx: dict[str, int] = {sym: i for i, sym in enumerate(symbols)}
        self.n: int = len(symbols)

        # EWMA decay factor for span_minutes (default 60 mins: alpha = 2 / (60 + 1))
        self.alpha: float = 2.0 / (span_minutes + 1.0)

        # 10x10 upper-triangular matrix: n*(n+1)//2 = 55 elements (440 bytes C-doubles)
        # Initialize diagonal with 1e-6 (non-zero variance) and off-diagonal with 0.0
        self.cov_flat: array = array("d", [
            1e-6 if i == j else 0.0
            for i in range(self.n)
            for j in range(i, self.n)
        ])

        # Mean vector initialized to zero
        self.means: array = array("d", [0.0] * self.n)

        # Normalized dominant eigenvector initialized to 1 / sqrt(n)
        unit_val: float = 1.0 / math.sqrt(self.n) if self.n > 0 else 0.0
        self.eig_vec: array = array("d", [unit_val] * self.n)

        # Baseline Absorption Ratio: 1 / n
        self.absorption_ratio: float = 1.0 / self.n if self.n > 0 else 0.10

        # Last price per symbol for 1m log-return computation
        self._last_prices: dict[str, float] = {}

    # ── Flat Array Index Helper ───────────────────────────────────────

    def _flat_idx(self, i: int, j: int) -> int:
        """Map (i, j) to the 1D upper-triangular array index."""
        return flat_triangular_index(i, j, self.n)

    # ── Matrix Element Accessors ──────────────────────────────────────

    def get_cov(self, i: int, j: int) -> float:
        """Get Cov(i, j) from the upper-triangular array."""
        return self.cov_flat[self._flat_idx(i, j)]

    def set_cov(self, i: int, j: int, value: float) -> None:
        """Set Cov(i, j) in the upper-triangular array."""
        self.cov_flat[self._flat_idx(i, j)] = value

    def get_corr(self, i: int, j: int) -> float:
        """Calculate Corr(i, j) from covariance and standard deviations."""
        if i == j:
            return 1.0
        cov_ij = self.get_cov(i, j)
        var_i = self.get_cov(i, i)
        var_j = self.get_cov(j, j)
        std_i = math.sqrt(max(1e-12, var_i))
        std_j = math.sqrt(max(1e-12, var_j))
        rho = cov_ij / (std_i * std_j)
        return max(-1.0, min(1.0, rho))

    # ── Covariance EWMA Update ────────────────────────────────────────

    def update_returns_1m(self, log_returns: Sequence[float] | dict[str, float]) -> None:
        """Incorporate a new 1-minute vector of log returns via EWMA."""
        if isinstance(log_returns, dict):
            r = [log_returns.get(sym, 0.0) for sym in self.symbols]
        else:
            r = list(log_returns)

        # 1. Calculate deltas and update means
        deltas = [0.0] * self.n
        for i in range(self.n):
            val = r[i] if i < len(r) else 0.0
            delta_i = val - self.means[i]
            deltas[i] = delta_i
            self.means[i] += self.alpha * delta_i

        # 2. Update upper-triangular covariance matrix:
        # cov = (1 - alpha) * (cov + alpha * delta_i * delta_j)
        decay = 1.0 - self.alpha
        for i in range(self.n):
            d_i = deltas[i]
            for j in range(i, self.n):
                idx = self._flat_idx(i, j)
                cov_prev = self.cov_flat[idx]
                cov_new = decay * (cov_prev + self.alpha * d_i * deltas[j])
                self.cov_flat[idx] = cov_new

        # 3. Perform Power Iteration to refresh dominant eigenvector & Absorption Ratio
        self._step_power_iteration()

    def update_price_and_step(self, symbol: str, close_price: float) -> None:
        """Update a single symbol's price, and if all symbols have updated, step EWMA."""
        prev_p = self._last_prices.get(symbol, close_price)
        self._last_prices[symbol] = close_price

        # If price changed, update single-symbol return in EWMA
        if prev_p > 0.0 and close_price > 0.0:
            ret = math.log(close_price / prev_p)
            idx = self.sym_to_idx.get(symbol)
            if idx is not None:
                delta_i = ret - self.means[idx]
                self.means[idx] += self.alpha * delta_i
                decay = 1.0 - self.alpha
                for j in range(self.n):
                    flat_idx = self._flat_idx(idx, j)
                    cov_prev = self.cov_flat[flat_idx]
                    # Update covariance row/col
                    delta_j = delta_i if j == idx else 0.0
                    cov_new = decay * (cov_prev + self.alpha * delta_i * delta_j)
                    self.cov_flat[flat_idx] = cov_new
                self._step_power_iteration()

    # ── Power-Iteration Spectral Absorption Ratio ─────────────────────

    def _step_power_iteration(self) -> float:
        """Perform exactly 8 Power Method iterations to find lambda_max and eigenvector v.

        Returns
        -------
        absorption_ratio : float
            Fraction of total system variance absorbed by the first principal component.
        """
        n = self.n
        if n < 2:
            self.absorption_ratio = 1.0
            return 1.0

        # Pre-compute standard deviations for correlation
        stds = [math.sqrt(max(1e-12, self.cov_flat[self._flat_idx(i, i)])) for i in range(n)]

        # Pre-compute 10x10 correlation matrix in local flat array for speed
        corr_matrix = [0.0] * (n * n)
        for i in range(n):
            std_i = stds[i]
            corr_matrix[i * n + i] = 1.0
            for j in range(i + 1, n):
                cov_ij = self.cov_flat[self._flat_idx(i, j)]
                c = max(-1.0, min(1.0, cov_ij / (std_i * stds[j])))
                corr_matrix[i * n + j] = c
                corr_matrix[j * n + i] = c

        # Initialize vector v from previous eig_vec
        v = list(self.eig_vec)

        # 8 Power Method iterations
        for _ in range(8):
            w = [0.0] * n
            for i in range(n):
                row_offset = i * n
                s = 0.0
                for j in range(n):
                    s += corr_matrix[row_offset + j] * v[j]
                w[i] = s

            norm_sq = sum(x * x for x in w)
            if norm_sq <= 1e-14:
                v = [1.0 / math.sqrt(n)] * n
                break
            inv_norm = 1.0 / math.sqrt(norm_sq)
            v = [x * inv_norm for x in w]

        # Compute Rayleigh quotient: lambda_max = sum(v_i * (C * v)_i)
        lambda_max = 0.0
        for i in range(n):
            row_offset = i * n
            cv_i = sum(corr_matrix[row_offset + j] * v[j] for j in range(n))
            lambda_max += v[i] * cv_i

        # Save normalized eigenvector
        for i in range(n):
            self.eig_vec[i] = v[i]

        # Absorption Ratio: lambda_max / n (clamped to [1/n, 1.0])
        self.absorption_ratio = min(1.0, max(1.0 / n, lambda_max / n))
        return self.absorption_ratio

    # ── Mantegna Angular Distance & Portfolio Gate ───────────────────

    def evaluate_portfolio_risk_gate(
        self,
        candidate_sym: str,
        candidate_dir: int,
        strategy_type: str,
        active_positions: dict[str, int],
    ) -> tuple[bool, float, str]:
        """Evaluate 4-Gate institutional portfolio risk filter.

        Parameters
        ----------
        candidate_sym : str
            Symbol under evaluation (e.g. "SOLUSDT").
        candidate_dir : int
            +1 for Long, -1 for Short.
        strategy_type : str
            "BREAKOUT", "MEAN_REVERSION", etc.
        active_positions : dict[str, int]
            Mapping of active symbols to their directions (+1 or -1).

        Returns
        -------
        approved : bool
            True if position passes all 4 gates, False if vetoed.
        risk_multiplier : float
            Position sizing scaling factor (0.25 to 1.0) derived from eigen-beta loading.
        reason : str
            Audit message explaining approval or veto trigger.
        """
        cand_idx = self.sym_to_idx.get(candidate_sym)
        if cand_idx is None:
            return True, 1.0, "APPROVED (untracked symbol)"

        ar = self.absorption_ratio

        # Gate 1: Systemic Contagion
        # When AR >= 0.72, altcoin breakouts fail due to systemic liquidity suction
        if ar >= 0.72 and strategy_type == "BREAKOUT" and candidate_sym not in ("BTCUSDT", "ETHUSDT"):
            return False, 0.0, f"VETO: Spectral AR >= 0.72 ({ar:.3f}) blocks altcoin breakout"

        # Gate 2: Portfolio Heat Cap
        # Max 4 concurrent positions during low systemic risk (< 0.58), max 2 during high risk
        max_positions = 4 if ar < 0.58 else 2
        # Filter active positions (ignore candidate if already counted)
        active_count = sum(1 for sym in active_positions if sym != candidate_sym)
        if active_count >= max_positions:
            return False, 0.0, f"VETO: Portfolio Heat Cap reached ({active_count}/{max_positions}, AR={ar:.3f})"

        # Gate 3: Mantegna Cluster Collision
        # For each active position, check directional angular distance
        for act_sym, act_dir in active_positions.items():
            if act_sym == candidate_sym:
                continue
            act_idx = self.sym_to_idx.get(act_sym)
            if act_idx is None:
                continue

            rho = self.get_corr(cand_idx, act_idx)
            directional_rho = candidate_dir * act_dir * rho
            dist = compute_mantegna_distance(directional_rho)

            # dist <= 0.35 corresponds to directional correlation > +0.755
            if dist <= 0.35:
                return (
                    False,
                    0.0,
                    f"VETO: Mantegna Cluster Collision with {act_sym} (dist={dist:.3f} <= 0.35, dir_rho={directional_rho:.2f})",
                )

        # Gate 4: Eigen-Beta Sizing
        # Assets that load heavily onto the market eigenvector receive reduced sizing
        eigen_loading = compute_eigen_loading(self.eig_vec[cand_idx], self.n)
        risk_mult = compute_risk_multiplier(ar, eigen_loading)

        return True, risk_mult, f"APPROVED (AR={ar:.3f}, eigen_loading={eigen_loading:.2f}, mult={risk_mult:.2f})"

    def __repr__(self) -> str:
        return (
            f"PurePythonSpectralRiskEngine(n={self.n}, "
            f"AR={self.absorption_ratio:.3f}, alpha={self.alpha:.4f})"
        )
