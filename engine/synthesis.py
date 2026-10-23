"""
engine/synthesis.py — Bayesian Edge Synthesis & Probability Scorer.

Synthesizes the 6 normalized order flow and microstructure edges into a
composite log-odds score and Bayesian win probability P(Win).

Edges and Weights:
1. L5 OFI:               w_1 = 0.28   (threshold >= 2.2)
2. CVD Absorption:        w_2 = 0.22   (threshold >= 2.0)
3. Liquidation Cascade:   w_3 = 0.20   (threshold >= 3.0)
4. OI & Basis:            w_4 = 0.14   (threshold >= 1.8)
5. SMT Divergence:        w_5 = 0.10   (threshold >= 1.8)
6. Micro-Price:           w_6 = 0.06   (threshold >= 1.5)

Intercept:
beta_0 = -1.5

Formula:
L_t = beta_0 + sum(w_k * clip(z_k, -4.0, +4.0))
P(Win) = 1 / (1 + exp(-L_t))

Activation Rule:
- P(Win) >= 0.820
- At least 3 distinct edges have |z_k| >= threshold
- Zero vetoes active

Strict Constraints:
- Zero NumPy / Pandas / SciPy.
- __slots__ on all classes.
"""

import math
from typing import NamedTuple

# ─────────────────────────────────────────────────────────────────────
# Constants & Weights
# ─────────────────────────────────────────────────────────────────────

BETA_0: float = -1.5
MIN_WIN_PROBABILITY: float = 0.820
MIN_ACTIVE_EDGES: int = 3
CLIP_MIN: float = -4.0
CLIP_MAX: float = 4.0

EDGE_WEIGHTS: tuple[float, ...] = (
    0.28,   # 1. L5 OFI
    0.22,   # 2. CVD Absorption
    0.20,   # 3. Liquidation Cascade
    0.14,   # 4. OI & Basis
    0.10,   # 5. SMT Divergence
    0.06,   # 6. Micro-Price
)

EDGE_THRESHOLDS: tuple[float, ...] = (
    2.2,    # OFI
    2.0,    # CVD Absorption
    3.0,    # Liquidation Cascade
    1.8,    # OI & Basis
    1.8,    # SMT Divergence
    1.5,    # Micro-Price
)


# ─────────────────────────────────────────────────────────────────────
# Pure Helper Functions
# ─────────────────────────────────────────────────────────────────────

def clip(value: float, min_val: float = CLIP_MIN, max_val: float = CLIP_MAX) -> float:
    """Clamp value to [min_val, max_val]."""
    return max(min_val, min(max_val, value))


def compute_log_odds(
    z_scores: tuple[float, ...] | list[float],
    weights: tuple[float, ...] = EDGE_WEIGHTS,
    beta_0: float = BETA_0,
) -> float:
    """Calculate linear log-odds: L_t = beta_0 + sum(w_k * clip(z_k, -4, 4))."""
    score = beta_0
    for w, z in zip(weights, z_scores):
        score += w * clip(z)
    return score


def compute_win_probability(log_odds: float) -> float:
    """Calculate sigmoid probability: P(Win) = 1 / (1 + exp(-L_t))."""
    # Guard against overflow in exp(-log_odds)
    if log_odds > 35.0:
        return 1.0
    if log_odds < -35.0:
        return 0.0
    return 1.0 / (1.0 + math.exp(-log_odds))


def count_activated_edges(
    z_scores: tuple[float, ...] | list[float],
    thresholds: tuple[float, ...] = EDGE_THRESHOLDS,
) -> int:
    """Count how many edges meet or exceed their specific activation threshold."""
    count = 0
    for z, thresh in zip(z_scores, thresholds):
        if abs(z) >= thresh:
            count += 1
    return count


# ─────────────────────────────────────────────────────────────────────
# Synthesis Result Container
# ─────────────────────────────────────────────────────────────────────

class SynthesisResult(NamedTuple):
    is_valid: bool
    win_probability: float
    log_odds: float
    active_edge_count: int
    z_scores: tuple[float, ...]


# ─────────────────────────────────────────────────────────────────────
# Signal Synthesizer Class
# ─────────────────────────────────────────────────────────────────────

class SignalSynthesizer:
    """Synthesizes normalized microstructure alpha edges into a Bayesian score."""

    __slots__ = (
        "_weights",
        "_thresholds",
        "_beta_0",
        "_min_p_win",
        "_min_edges",
    )

    def __init__(
        self,
        weights: tuple[float, ...] = EDGE_WEIGHTS,
        thresholds: tuple[float, ...] = EDGE_THRESHOLDS,
        beta_0: float = BETA_0,
        min_win_probability: float = MIN_WIN_PROBABILITY,
        min_active_edges: int = MIN_ACTIVE_EDGES,
    ) -> None:
        self._weights: tuple[float, ...] = weights
        self._thresholds: tuple[float, ...] = thresholds
        self._beta_0: float = beta_0
        self._min_p_win: float = min_win_probability
        self._min_edges: int = min_active_edges

    def synthesize(
        self,
        z_ofi: float,
        z_cvd: float,
        z_liq: float,
        z_oi: float,
        z_smt: float,
        z_micro: float,
        has_active_veto: bool = False,
    ) -> SynthesisResult:
        """Evaluate edge scores and determine signal activation validity.

        Parameters
        ----------
        z_ofi : float
            L5 Order Flow Imbalance Z-score.
        z_cvd : float
            CVD Absorption Divergence score.
        z_liq : float
            Liquidation Cascade Exhaustion Z_liq.
        z_oi : float
            Open Interest & Basis divergence Z-score.
        z_smt : float
            Cross-Asset SMT Divergence score.
        z_micro : float
            Micro-price displacement Z-score.
        has_active_veto : bool
            True if any veto in the veto matrix fired.

        Returns
        -------
        SynthesisResult
            NamedTuple with is_valid, win_probability, log_odds, active_edge_count, z_scores.
        """
        z_tuple = (z_ofi, z_cvd, z_liq, z_oi, z_smt, z_micro)

        log_odds = compute_log_odds(z_tuple, self._weights, self._beta_0)
        p_win = compute_win_probability(log_odds)
        active_count = count_activated_edges(z_tuple, self._thresholds)

        is_valid = (
            (p_win >= self._min_p_win)
            and (active_count >= self._min_edges)
            and (not has_active_veto)
        )

        return SynthesisResult(
            is_valid=is_valid,
            win_probability=p_win,
            log_odds=log_odds,
            active_edge_count=active_count,
            z_scores=z_tuple,
        )

    def __repr__(self) -> str:
        return (
            f"SignalSynthesizer(beta_0={self._beta_0}, "
            f"min_p_win={self._min_p_win}, min_edges={self._min_edges})"
        )
