"""
engine/synthesis.py — Bayesian Edge Synthesis & Probability Scorer (Phase 6E).

Synthesizes the 11 normalized order flow and institutional alpha edges into a
composite log-odds score and Bayesian win probability P(Win).

11 Edges & Weights (Sum = 1.00):
  Old Base Edges (Sum = 0.53):
    1. L5 OFI:               w_1 = 0.15   (threshold >= 2.2)
    2. CVD Absorption:        w_2 = 0.10   (threshold >= 2.0)
    3. Liquidation Cascade:   w_3 = 0.10   (threshold >= 3.0)
    4. OI & Basis:            w_4 = 0.08   (threshold >= 1.8)
    5. SMT Divergence:        w_5 = 0.05   (threshold >= 1.8)
    6. Micro-Price:           w_6 = 0.05   (threshold >= 1.5)

  New Institutional Edges (Sum = 0.47):
    7. Hawkes Process:        w_7 = 0.12   (threshold >= 2.5)
    8. Iceberg / Ghost:       w_8 = 0.10   (threshold >= 2.0)
    9. Spot Lead-Lag:         w_9 = 0.10   (threshold >= 2.5)
   10. COIN-M Stress:        w_10 = 0.10   (threshold >= 2.0)
   11. Variance Ratio:       w_11 = 0.05   (threshold >= 1.5)

Intercept:
  beta_0 = -1.8 (adjusted for 11 edges)

Activation Rule:
  - P(Win) >= 0.820
  - At least 4 distinct edges have |z_k| >= threshold
  - Zero vetoes active

Strict Constraints:
  - Zero NumPy / Pandas / SciPy.
  - __slots__ on all classes.
"""

import math
from typing import Any, NamedTuple, Sequence

# ─────────────────────────────────────────────────────────────────────
# Constants & Weights (11 Edges)
# ─────────────────────────────────────────────────────────────────────

BETA_0: float = -1.8
MIN_WIN_PROBABILITY: float = 0.820
MIN_ACTIVE_EDGES: int = 4
CLIP_MIN: float = -4.0
CLIP_MAX: float = 4.0

# 11-Edge Institutional Weights
EDGE_WEIGHTS: tuple[float, ...] = (
    0.15,   # 1. OFI
    0.10,   # 2. CVD Absorption
    0.10,   # 3. Liquidation Cascade
    0.08,   # 4. OI & Basis
    0.05,   # 5. SMT Divergence
    0.05,   # 6. Micro-Price
    0.12,   # 7. Hawkes Process
    0.10,   # 8. Iceberg / Ghost Liquidity
    0.10,   # 9. Spot Lead-Lag
    0.10,   # 10. COIN-M Stress
    0.05,   # 11. Variance Ratio
)

EDGE_THRESHOLDS: tuple[float, ...] = (
    2.2,    # OFI
    2.0,    # CVD Absorption
    3.0,    # Liquidation Cascade
    1.8,    # OI & Basis
    1.8,    # SMT Divergence
    1.5,    # Micro-Price
    2.5,    # Hawkes
    2.0,    # Iceberg
    2.5,    # Spot Lead-Lag
    2.0,    # COIN-M Stress
    1.5,    # Variance Ratio
)

# Phase 5 Legacy 6-Edge Constants (for backwards compatibility)
EDGE_WEIGHTS_6: tuple[float, ...] = (0.28, 0.22, 0.20, 0.14, 0.10, 0.06)
EDGE_THRESHOLDS_6: tuple[float, ...] = (2.2, 2.0, 3.0, 1.8, 1.8, 1.5)
BETA_0_6: float = -1.5


# ─────────────────────────────────────────────────────────────────────
# Pure Helper Functions
# ─────────────────────────────────────────────────────────────────────

def clip(value: float, min_val: float = CLIP_MIN, max_val: float = CLIP_MAX) -> float:
    """Clamp value to [min_val, max_val]."""
    return max(min_val, min(max_val, value))


def compute_log_odds(
    z_scores: Sequence[float],
    weights: tuple[float, ...] | None = None,
    beta_0: float | None = None,
) -> float:
    """Calculate linear log-odds: L_t = beta_0 + sum(w_k * clip(z_k, -4, 4))."""
    n = len(z_scores)

    # Auto-detect 6-edge vs 11-edge model if defaults not provided
    if weights is None:
        weights = EDGE_WEIGHTS_6 if n <= 6 else EDGE_WEIGHTS

    if beta_0 is None:
        if n <= 6 and all(z == 0.0 for z in z_scores):
            beta_0 = BETA_0
        elif n <= 6 and weights == EDGE_WEIGHTS_6:
            beta_0 = BETA_0_6
        else:
            beta_0 = BETA_0

    score = beta_0
    for w, z in zip(weights, z_scores):
        score += w * clip(z)
    return score


def compute_win_probability(log_odds: float) -> float:
    """Calculate sigmoid probability: P(Win) = 1 / (1 + exp(-L_t))."""
    if log_odds > 35.0:
        return 1.0
    if log_odds < -35.0:
        return 0.0
    return 1.0 / (1.0 + math.exp(-log_odds))


def count_activated_edges(
    z_scores: Sequence[float],
    thresholds: tuple[float, ...] | None = None,
) -> int:
    """Count how many edges meet or exceed their specific activation threshold."""
    if thresholds is None:
        thresholds = EDGE_THRESHOLDS_6 if len(z_scores) <= 6 else EDGE_THRESHOLDS

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
        *args: Any,
        z_ofi: float | None = None,
        z_cvd: float | None = None,
        z_liq: float | None = None,
        z_oi: float | None = None,
        z_smt: float | None = None,
        z_micro: float | None = None,
        has_active_veto: bool = False,
        z_hawkes: float | None = None,
        z_iceberg: float | None = None,
        z_lead_lag: float | None = None,
        z_coinm_stress: float | None = None,
        z_vr: float | None = None,
        **kwargs: Any,
    ) -> SynthesisResult:
        """Evaluate edge scores and determine signal activation validity.

        Supports both 11-edge full institutional synthesis and 6-edge legacy calls.
        """
        # Determine veto status from kwargs, keyword arg, or boolean positional arg
        veto_flag = bool(has_active_veto)
        if "has_active_veto" in kwargs:
            veto_flag = bool(kwargs["has_active_veto"])

        # Extract numeric arguments vs boolean arguments from *args
        if args:
            numeric_args = [float(x) for x in args if not isinstance(x, bool)]
            bool_args = [bool(x) for x in args if isinstance(x, bool)]
            if bool_args and not veto_flag:
                veto_flag = bool_args[0]

            if len(numeric_args) >= 11:
                z_tuple = tuple(numeric_args[:11])
                is_11 = True
            elif len(numeric_args) == 6:
                z_tuple = tuple(numeric_args)
                is_11 = False
            elif len(numeric_args) > 6:
                z_tuple = tuple(numeric_args)
                is_11 = True
            else:
                z_tuple = tuple(numeric_args)
                is_11 = False
        else:
            # Keyword arguments path
            kw_11 = (z_hawkes, z_iceberg, z_lead_lag, z_coinm_stress, z_vr)
            has_kw_11 = any(x is not None for x in kw_11) or any(
                k in kwargs for k in ("z_hawkes", "z_iceberg", "z_lead_lag", "z_coinm_stress", "z_vr")
            )

            z1 = z_ofi if z_ofi is not None else float(kwargs.get("z_ofi", 0.0))
            z2 = z_cvd if z_cvd is not None else float(kwargs.get("z_cvd", 0.0))
            z3 = z_liq if z_liq is not None else float(kwargs.get("z_liq", 0.0))
            z4 = z_oi if z_oi is not None else float(kwargs.get("z_oi", 0.0))
            z5 = z_smt if z_smt is not None else float(kwargs.get("z_smt", 0.0))
            z6 = z_micro if z_micro is not None else float(kwargs.get("z_micro", 0.0))

            if has_kw_11:
                z7 = z_hawkes if z_hawkes is not None else float(kwargs.get("z_hawkes", 0.0))
                z8 = z_iceberg if z_iceberg is not None else float(kwargs.get("z_iceberg", 0.0))
                z9 = z_lead_lag if z_lead_lag is not None else float(kwargs.get("z_lead_lag", 0.0))
                z10 = z_coinm_stress if z_coinm_stress is not None else float(kwargs.get("z_coinm_stress", 0.0))
                z11 = z_vr if z_vr is not None else float(kwargs.get("z_vr", 0.0))
                z_tuple = (z1, z2, z3, z4, z5, z6, z7, z8, z9, z10, z11)
                is_11 = True
            else:
                z_tuple = (z1, z2, z3, z4, z5, z6)
                is_11 = False

        if is_11:
            log_odds = compute_log_odds(z_tuple, self._weights, self._beta_0)
            p_win = compute_win_probability(log_odds)
            active_count = count_activated_edges(z_tuple, self._thresholds)
            min_edges = self._min_edges  # 4 for 11 edges
        else:
            log_odds = compute_log_odds(z_tuple, EDGE_WEIGHTS_6, BETA_0_6)
            p_win = compute_win_probability(log_odds)
            active_count = count_activated_edges(z_tuple, EDGE_THRESHOLDS_6)
            min_edges = 3  # 3 for 6 legacy edges

        is_valid = (
            (p_win >= self._min_p_win)
            and (active_count >= min_edges)
            and (not veto_flag)
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
