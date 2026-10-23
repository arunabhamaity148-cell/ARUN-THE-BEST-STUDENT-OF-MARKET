"""tests/test_synthesis.py — Comprehensive tests for engine/synthesis.py.

Verifies:
1. Slots and memory compliance for SignalSynthesizer.
2. Linear log-odds formula with clipping to [-4.0, +4.0].
3. Sigmoid probability calculation P(Win) = 1 / (1 + exp(-L_t)).
4. Threshold activation counting across all 6 edges.
5. Signal validity rule:
   - P(Win) >= 0.820
   - >= 3 distinct edges activated
   - Zero active vetoes
"""

import math
import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine.synthesis import (
    SignalSynthesizer,
    clip,
    compute_log_odds,
    compute_win_probability,
    count_activated_edges,
    BETA_0,
    MIN_WIN_PROBABILITY,
    MIN_ACTIVE_EDGES,
    EDGE_WEIGHTS,
    EDGE_THRESHOLDS,
)


class TestSignalSynthesizerSlots:
    """Verify slots enforcement."""

    def test_has_slots(self) -> None:
        assert hasattr(SignalSynthesizer, "__slots__")

    def test_no_dict(self) -> None:
        synth = SignalSynthesizer()
        assert not hasattr(synth, "__dict__")

    def test_repr(self) -> None:
        synth = SignalSynthesizer()
        r = repr(synth)
        assert "SignalSynthesizer" in r


class TestSynthesisPureFormulas:
    """Verify mathematical formulas for clipping, log-odds, and sigmoid."""

    def test_clip(self) -> None:
        assert clip(5.5) == 4.0
        assert clip(-6.0) == -4.0
        assert clip(2.5) == 2.5

    def test_log_odds_baseline(self) -> None:
        # All zeros -> log_odds = beta_0 (-1.5)
        zeros = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        assert compute_log_odds(zeros) == BETA_0

    def test_log_odds_max_activation(self) -> None:
        # All +4.0 -> sum(weights * 4.0) = 1.0 * 4.0 = 4.0
        # log_odds = -1.5 + 4.0 = 2.5
        fours = (4.0, 4.0, 4.0, 4.0, 4.0, 4.0)
        assert pytest.approx(compute_log_odds(fours), 1e-6) == 2.5

    def test_win_probability_sigmoid(self) -> None:
        # L = 0 -> P = 0.5
        assert compute_win_probability(0.0) == 0.5

        # L = 2.5 -> P = 1 / (1 + exp(-2.5)) ≈ 0.9241
        p = compute_win_probability(2.5)
        expected = 1.0 / (1.0 + math.exp(-2.5))
        assert pytest.approx(p, 1e-6) == expected
        assert p >= MIN_WIN_PROBABILITY

    def test_count_activated_edges(self) -> None:
        # OFI: 2.2, CVD: 2.0, Liq: 3.0, OI: 1.8, SMT: 1.8, Micro: 1.5
        # Pass 3 activated edges: OFI (2.5), CVD (2.2), Liq (3.5), rest 0
        z = (2.5, 2.2, 3.5, 0.5, 0.0, 0.0)
        assert count_activated_edges(z) == 3

        # Negative Z meets threshold via absolute value
        z_neg = (-2.5, -2.2, 1.0, 0.0, 0.0, 0.0)
        assert count_activated_edges(z_neg) == 2


class TestSignalSynthesizerEvaluation:
    """Verify signal validity decision rules."""

    def test_valid_signal_approved(self) -> None:
        synth = SignalSynthesizer()
        # Strong edge alignment across OFI, CVD, Liq, and OI
        res = synth.synthesize(
            z_ofi=3.8,     # activated (>= 2.2)
            z_cvd=3.5,     # activated (>= 2.0)
            z_liq=3.8,     # activated (>= 3.0)
            z_oi=3.0,      # activated (>= 1.8)
            z_smt=1.0,     # not activated (< 1.8)
            z_micro=1.0,   # not activated (< 1.5)
            has_active_veto=False,
        )

        assert res.active_edge_count == 4
        assert res.win_probability >= 0.820
        assert res.is_valid is True

    def test_veto_rejects_valid_probability_signal(self) -> None:
        synth = SignalSynthesizer()
        # Same strong setup with P(Win) >= 0.820, but has_active_veto = True
        res = synth.synthesize(
            z_ofi=3.8, z_cvd=3.5, z_liq=3.8,
            z_oi=3.0, z_smt=2.5, z_micro=2.0,
            has_active_veto=True,
        )
        assert res.win_probability >= 0.820
        assert res.active_edge_count >= 3
        # Must be rejected because of veto
        assert res.is_valid is False

    def test_insufficient_edges_rejects_signal(self) -> None:
        synth = SignalSynthesizer()
        # Only 2 edges activated (OFI and Liq)
        res = synth.synthesize(
            z_ofi=4.0,     # activated
            z_cvd=1.0,     # not activated
            z_liq=4.0,     # activated
            z_oi=0.5,      # not activated
            z_smt=0.5,     # not activated
            z_micro=0.5,   # not activated
            has_active_veto=False,
        )
        assert res.active_edge_count == 2
        # Fails minimum 3 active edges requirement
        assert res.is_valid is False

    def test_low_probability_rejects_signal(self) -> None:
        synth = SignalSynthesizer()
        # Edge thresholds barely met, but weak overall score -> P(Win) < 0.820
        res = synth.synthesize(
            z_ofi=2.2, z_cvd=-1.0, z_liq=3.0,
            z_oi=-1.0, z_smt=1.8, z_micro=-1.0,
            has_active_veto=False,
        )
        assert res.win_probability < 0.820
        assert res.is_valid is False


class TestElevenEdgeSynthesis:
    """Verify Phase 6E 11-Edge Bayesian synthesis, weights, thresholds, and veto rules."""

    def test_11_edge_weights_and_thresholds(self) -> None:
        assert len(EDGE_WEIGHTS) == 11
        assert len(EDGE_THRESHOLDS) == 11
        assert pytest.approx(sum(EDGE_WEIGHTS), 1e-6) == 1.0
        assert pytest.approx(sum(EDGE_WEIGHTS[:6]), 1e-6) == 0.53
        assert pytest.approx(sum(EDGE_WEIGHTS[6:]), 1e-6) == 0.47
        assert BETA_0 == -1.8
        assert MIN_WIN_PROBABILITY == 0.820
        assert MIN_ACTIVE_EDGES == 4

    def test_11_edge_log_odds_baseline_and_saturation(self) -> None:
        zeros_11 = (0.0,) * 11
        assert pytest.approx(compute_log_odds(zeros_11), 1e-6) == -1.8

        fours_11 = (4.0,) * 11
        # -1.8 + 1.0 * 4.0 = 2.2
        assert pytest.approx(compute_log_odds(fours_11), 1e-6) == 2.2
        p_win = compute_win_probability(2.2)
        assert p_win >= 0.820

    def test_11_edge_activation_counting(self) -> None:
        # Thresholds: OFI(2.2), CVD(2.0), Liq(3.0), OI(1.8), SMT(1.8), Micro(1.5),
        #             Hawkes(2.5), Iceberg(2.0), LeadLag(2.5), CM_Stress(2.0), VR(1.5)
        z_11 = (2.5, 2.1, 0.0, 0.0, 0.0, 0.0, 3.0, 2.2, 0.0, 0.0, 0.0)
        # Active: OFI, CVD, Hawkes, Iceberg -> 4 active
        assert count_activated_edges(z_11) == 4

    def test_11_edge_valid_signal_activation(self) -> None:
        synth = SignalSynthesizer()
        # High conviction institutional alignment across edges
        res = synth.synthesize(
            z_ofi=3.8,
            z_cvd=3.8,
            z_liq=3.8,
            z_oi=3.5,
            z_smt=3.0,
            z_micro=3.0,
            has_active_veto=False,
            z_hawkes=3.8,
            z_iceberg=3.8,
            z_lead_lag=3.8,
            z_coinm_stress=3.5,
            z_vr=3.0,
        )
        assert res.is_valid is True
        assert res.active_edge_count >= 4
        assert res.win_probability >= 0.820
        assert len(res.z_scores) == 11

    def test_11_edge_insufficient_edges_rejected(self) -> None:
        synth = SignalSynthesizer()
        # High scores on only 3 edges (OFI, CVD, Liq), remaining zero
        res = synth.synthesize(
            z_ofi=4.0,
            z_cvd=4.0,
            z_liq=4.0,
            z_oi=0.0,
            z_smt=0.0,
            z_micro=0.0,
            has_active_veto=False,
            z_hawkes=0.0,
            z_iceberg=0.0,
            z_lead_lag=0.0,
            z_coinm_stress=0.0,
            z_vr=0.0,
        )
        # Needs at least 4 active edges in 11-edge model
        assert res.active_edge_count == 3
        assert res.is_valid is False

    def test_11_edge_veto_rejection(self) -> None:
        synth = SignalSynthesizer()
        # Strong 11-edge alignment but has_active_veto=True
        res = synth.synthesize(
            3.8, 3.8, 3.8, 3.5, 3.0, 3.0, 3.8, 3.8, 3.8, 3.5, 3.0,
            has_active_veto=True,
        )
        assert res.win_probability >= 0.820
        assert res.active_edge_count >= 4
        assert res.is_valid is False
