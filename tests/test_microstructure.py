"""tests/test_microstructure.py — Comprehensive tests for engine/microstructure.py.

Verifies:
1. Module constants and fast message router (first 256 bytes).
2. Level OFI bid/ask order flow contributions.
3. Multi-Level L5 OFI calculation with exponential depth decay weights.
4. Volume-Weighted Micro-Price and MicroSpreadBps displacement.
5. CVD Absorption Divergence (rolling OLS slope & z-normalized score).
6. Liquidation Cascade Exhaustion (rolling notional & Z_liq).
"""

import math
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.state import SymbolMicroState
from engine.microstructure import (
    DEPTH_WEIGHTS,
    ABSORPTION_WINDOW_MS,
    LIQ_ROLLING_MS,
    LIQ_BASELINE_MS,
    MAX_ROUTE_SCAN,
    extract_stream_type,
    compute_level_ofi_bid,
    compute_level_ofi_ask,
    update_l5_ofi,
    compute_micro_price,
    compute_micro_spread_bps,
    _rolling_statistics,
    compute_absorption_score,
    compute_rolling_liq_notional,
    update_z_liq,
)


# ─────────────────────────────────────────────────────────────────────
# 1. Constants & Fast Message Router
# ─────────────────────────────────────────────────────────────────────

class TestMicrostructureConstants:
    """Verify module-level constants and decay weights."""

    def test_depth_weights_is_tuple(self) -> None:
        assert isinstance(DEPTH_WEIGHTS, tuple)
        assert len(DEPTH_WEIGHTS) == 5

    def test_depth_weights_values(self) -> None:
        """Verify w_i = exp(-0.35 * i) for i in 0..4."""
        expected = tuple(math.exp(-0.35 * i) for i in range(5))
        for w, exp_w in zip(DEPTH_WEIGHTS, expected):
            assert math.isclose(w, exp_w, rel_tol=1e-9)

    def test_depth_weights_strictly_decreasing(self) -> None:
        assert DEPTH_WEIGHTS[0] == 1.0
        for i in range(len(DEPTH_WEIGHTS) - 1):
            assert DEPTH_WEIGHTS[i] > DEPTH_WEIGHTS[i + 1]
            assert DEPTH_WEIGHTS[i + 1] > 0.0

    def test_windows_positive(self) -> None:
        assert ABSORPTION_WINDOW_MS == 15 * 60 * 1000
        assert LIQ_ROLLING_MS == 60 * 1000
        assert LIQ_BASELINE_MS == 15 * 60 * 1000
        assert MAX_ROUTE_SCAN == 256


class TestExtractStreamType:
    """Verify stream router scans only the prefix and ignores control frames."""

    def test_extract_bytes_valid_stream(self) -> None:
        raw = b'{"stream":"btcusdt@depth20@100ms","data":{"bids":[]}}'
        assert extract_stream_type(raw) == "btcusdt@depth20@100ms"

    def test_extract_bytes_agg_trade(self) -> None:
        raw = b'{"stream":"ethusdt@aggTrade","data":{"p":"3000.0"}}'
        assert extract_stream_type(raw) == "ethusdt@aggTrade"

    def test_extract_bytes_control_response_result(self) -> None:
        raw = b'{"result":null,"id":1}'
        assert extract_stream_type(raw) is None

    def test_extract_bytes_control_response_id_only(self) -> None:
        raw = b'{"id":123,"status":200}'
        assert extract_stream_type(raw) is None

    def test_extract_bytes_no_stream_field(self) -> None:
        raw = b'{"e":"depthUpdate","E":123456789}'
        assert extract_stream_type(raw) is None

    def test_extract_str_valid_stream(self) -> None:
        raw = '{"stream":"solusdt@bookTicker","data":{}}'
        assert extract_stream_type(raw) == "solusdt@bookTicker"

    def test_extract_str_control_response(self) -> None:
        raw = '{"result":null,"id":42}'
        assert extract_stream_type(raw) is None

    def test_extract_respects_max_scan(self) -> None:
        # If stream appears after 256 bytes, router should return None
        padding = b'{"foo":"' + (b'x' * 300) + b'","stream":"btcusdt@aggTrade"}'
        assert extract_stream_type(padding, max_scan=256) is None


# ─────────────────────────────────────────────────────────────────────
# 2. Level OFI Helpers
# ─────────────────────────────────────────────────────────────────────

class TestLevelOFIHelpers:
    """Verify bid and ask level OFI contribution logic."""

    def test_bid_price_increase(self) -> None:
        # Higher bid placed -> buying pressure equal to full new qty
        e = compute_level_ofi_bid(prev_price=100.0, prev_qty=1.0, curr_price=101.0, curr_qty=2.5)
        assert e == 2.5

    def test_bid_price_same_qty_increase(self) -> None:
        # More bids added at same price
        e = compute_level_ofi_bid(prev_price=100.0, prev_qty=1.0, curr_price=100.0, curr_qty=3.0)
        assert e == 2.0

    def test_bid_price_same_qty_decrease(self) -> None:
        # Bids cancelled or filled at same price
        e = compute_level_ofi_bid(prev_price=100.0, prev_qty=3.0, curr_price=100.0, curr_qty=1.0)
        assert e == -2.0

    def test_bid_price_decrease(self) -> None:
        # Best bid dropped -> previous bid removed
        e = compute_level_ofi_bid(prev_price=101.0, prev_qty=2.0, curr_price=100.0, curr_qty=5.0)
        assert e == -2.0

    def test_ask_price_decrease(self) -> None:
        # Ask price lowered -> new lower ask -> aggressive selling pressure (+curr_qty)
        e = compute_level_ofi_ask(prev_price=105.0, prev_qty=1.0, curr_price=104.0, curr_qty=3.0)
        assert e == 3.0

    def test_ask_price_same_qty_increase(self) -> None:
        # More asks added at same price -> more selling pressure
        e = compute_level_ofi_ask(prev_price=105.0, prev_qty=1.0, curr_price=105.0, curr_qty=4.0)
        assert e == 3.0

    def test_ask_price_same_qty_decrease(self) -> None:
        # Asks cancelled or lifted
        e = compute_level_ofi_ask(prev_price=105.0, prev_qty=4.0, curr_price=105.0, curr_qty=1.0)
        assert e == -3.0

    def test_ask_price_increase(self) -> None:
        # Ask price retreated higher -> selling pressure reduced (-prev_qty)
        e = compute_level_ofi_ask(prev_price=104.0, prev_qty=2.0, curr_price=105.0, curr_qty=5.0)
        assert e == -2.0


# ─────────────────────────────────────────────────────────────────────
# 3. Multi-Level L5 OFI
# ─────────────────────────────────────────────────────────────────────

class TestUpdateL5OFI:
    """Verify L5 OFI calculation, state persistence, streak tracking, and history."""

    def _make_book(self, bid_start=100.0, ask_start=101.0, qty=1.0):
        bids = [[str(bid_start - i * 0.5), str(qty)] for i in range(5)]
        asks = [[str(ask_start + i * 0.5), str(qty)] for i in range(5)]
        return bids, asks

    def test_first_update_returns_zero(self) -> None:
        state = SymbolMicroState("BTCUSDT")
        bids, asks = self._make_book(100.0, 101.0, 2.0)
        ofi = update_l5_ofi(state, bids, asks)
        assert ofi == 0.0
        assert state.prev_bid_prices[0] == 100.0
        assert state.prev_bid_qtys[0] == 2.0
        assert state.prev_ask_prices[0] == 101.0
        assert state.prev_ask_qtys[0] == 2.0
        assert state.ofi_sign_streak == 0
        assert state.last_ofi_sign == 0

    def test_second_update_bid_qty_increase_level0(self) -> None:
        state = SymbolMicroState("BTCUSDT")
        bids1, asks1 = self._make_book(100.0, 101.0, 1.0)
        update_l5_ofi(state, bids1, asks1)

        # Level 0 bid increases qty from 1.0 to 3.0 (delta = +2.0)
        bids2, asks2 = self._make_book(100.0, 101.0, 1.0)
        bids2[0][1] = "3.0"

        ofi = update_l5_ofi(state, bids2, asks2)
        # e_b[0] = 2.0, e_a[0] = 0.0, w_0 = 1.0
        assert math.isclose(ofi, 2.0, rel_tol=1e-9)
        assert state.ofi_sign_streak == 1
        assert state.last_ofi_sign == 1

    def test_weighted_decay_across_levels(self) -> None:
        state = SymbolMicroState("BTCUSDT")
        bids1, asks1 = self._make_book(100.0, 101.0, 1.0)
        update_l5_ofi(state, bids1, asks1)

        # Level 2 bid increases qty by 1.0, Level 4 ask increases qty by 2.0
        bids2, asks2 = self._make_book(100.0, 101.0, 1.0)
        bids2[2][1] = "2.0"  # +1.0 at level 2
        asks2[4][1] = "3.0"  # +2.0 at level 4

        ofi = update_l5_ofi(state, bids2, asks2)
        expected = DEPTH_WEIGHTS[2] * 1.0 - DEPTH_WEIGHTS[4] * 2.0
        assert math.isclose(ofi, expected, rel_tol=1e-9)

    def test_streak_tracking_same_sign(self) -> None:
        state = SymbolMicroState("BTCUSDT")
        bids, asks = self._make_book(100.0, 101.0, 1.0)
        update_l5_ofi(state, bids, asks)

        # 3 consecutive positive updates
        for i in range(3):
            b2, a2 = self._make_book(100.0, 101.0, 1.0)
            b2[0][1] = str(2.0 + i)
            update_l5_ofi(state, b2, a2)
            assert state.ofi_sign_streak == i + 1
            assert state.last_ofi_sign == 1

    def test_streak_resets_on_sign_flip(self) -> None:
        state = SymbolMicroState("BTCUSDT")
        bids, asks = self._make_book(100.0, 101.0, 1.0)
        update_l5_ofi(state, bids, asks)

        # Positive update -> streak 1
        b2, a2 = self._make_book(100.0, 101.0, 1.0)
        b2[0][1] = "3.0"
        update_l5_ofi(state, b2, a2)
        assert state.ofi_sign_streak == 1
        assert state.last_ofi_sign == 1

        # Negative update -> streak resets to 1 with sign -1
        b3, a3 = self._make_book(100.0, 101.0, 1.0)
        a3[0][1] = "5.0"
        update_l5_ofi(state, b3, a3)
        assert state.ofi_sign_streak == 1
        assert state.last_ofi_sign == -1


# ─────────────────────────────────────────────────────────────────────
# 4. Volume-Weighted Micro-Price & Micro-Spread Displacement
# ─────────────────────────────────────────────────────────────────────

class TestMicroPrice:
    """Verify volume-weighted micro-price formula and bps displacement."""

    def test_symmetric_quantities_equal_mid(self) -> None:
        p = compute_micro_price(bid=100.0, bid_qty=10.0, ask=102.0, ask_qty=10.0)
        assert math.isclose(p, 101.0)

    def test_heavy_bid_qty_pulls_towards_ask(self) -> None:
        # P_micro = (100*10 + 102*30) / 40 = (1000 + 3060) / 40 = 4060 / 40 = 101.5
        p = compute_micro_price(bid=100.0, bid_qty=30.0, ask=102.0, ask_qty=10.0)
        assert math.isclose(p, 101.5)

    def test_heavy_ask_qty_pulls_towards_bid(self) -> None:
        # P_micro = (100*30 + 102*10) / 40 = (3000 + 1020) / 40 = 4020 / 40 = 100.5
        p = compute_micro_price(bid=100.0, bid_qty=10.0, ask=102.0, ask_qty=30.0)
        assert math.isclose(p, 100.5)

    def test_zero_total_qty_returns_mid(self) -> None:
        p = compute_micro_price(bid=100.0, bid_qty=0.0, ask=102.0, ask_qty=0.0)
        assert math.isclose(p, 101.0)

    def test_micro_spread_bps_symmetric_zero(self) -> None:
        bps = compute_micro_spread_bps(bid=100.0, bid_qty=5.0, ask=102.0, ask_qty=5.0)
        assert math.isclose(bps, 0.0, abs_tol=1e-9)

    def test_micro_spread_bps_positive_displacement(self) -> None:
        # mid = 101.0, micro = 101.5 -> diff = +0.5 -> bps = (0.5 / 101.0) * 1e4
        bps = compute_micro_spread_bps(bid=100.0, bid_qty=30.0, ask=102.0, ask_qty=10.0)
        expected = ((101.5 - 101.0) / 101.0) * 1e4
        assert math.isclose(bps, expected, rel_tol=1e-9)
        assert bps > 0.0

    def test_micro_spread_bps_negative_displacement(self) -> None:
        bps = compute_micro_spread_bps(bid=100.0, bid_qty=10.0, ask=102.0, ask_qty=30.0)
        assert bps < 0.0

    def test_micro_spread_bps_zero_mid_guard(self) -> None:
        bps = compute_micro_spread_bps(bid=0.0, bid_qty=1.0, ask=0.0, ask_qty=1.0)
        assert bps == 0.0


# ─────────────────────────────────────────────────────────────────────
# 5. CVD Absorption Divergence
# ─────────────────────────────────────────────────────────────────────

class TestAbsorptionDivergence:
    """Verify rolling OLS slope calculation and absorption divergence score."""

    def test_rolling_statistics_insufficient_samples(self) -> None:
        snaps = [(1000, 100.0, 10.0)]
        slope, std = _rolling_statistics(snaps, now_ms=1000, window_ms=60000, value_index=1)
        assert slope == 0.0
        assert std == 0.0

    def test_rolling_statistics_known_linear_slope(self) -> None:
        # 4 samples spaced 1 sec apart, price increasing by 5.0 per sec
        snaps = [
            (1000, 100.0, 0.0),
            (2000, 105.0, 0.0),
            (3000, 110.0, 0.0),
            (4000, 115.0, 0.0),
        ]
        slope, std = _rolling_statistics(snaps, now_ms=4000, window_ms=10000, value_index=1)
        assert math.isclose(slope, 5.0, rel_tol=1e-6)
        assert std > 0.0

    def test_rolling_statistics_window_filter(self) -> None:
        # Old samples should be filtered out
        snaps = [
            (1000, 50.0, 0.0),   # age = 9000 ms, cutoff is 5000 ms
            (6000, 100.0, 0.0),
            (8000, 110.0, 0.0),
            (10000, 120.0, 0.0),
        ]
        slope, _ = _rolling_statistics(snaps, now_ms=10000, window_ms=5000, value_index=1)
        # points at t=6000, 8000, 10000 (0s, 2s, 4s) -> price 100, 110, 120 -> slope = 5.0/s
        assert math.isclose(slope, 5.0, rel_tol=1e-6)

    def test_compute_absorption_score_insufficient_samples(self) -> None:
        state = SymbolMicroState("BTCUSDT")
        # 5 samples (< 10 minimum)
        for i in range(5):
            state.absorption_snapshots.append((1000 * i, 100.0 - i, float(i)))
        assert compute_absorption_score(state, now_ms=5000) == 0.0

    def test_compute_absorption_score_bullish_absorption(self) -> None:
        """Price drops but CVD rises/absorbs limit bids -> high positive score."""
        state = SymbolMicroState("BTCUSDT")
        now = 100_000
        # 20 samples: price falling 1.0/s, CVD rising 2.0/s
        for i in range(20):
            ts = now - (20 - i) * 1000
            price = 50000.0 - i * 2.0
            cvd = 100.0 + i * 5.0
            state.absorption_snapshots.append((ts, price, cvd))

        score = compute_absorption_score(state, now_ms=now)
        # cvd_slope > 0, price_slope < 0 -> cvd_slope_norm - price_slope_norm > 0
        assert score > 0.0

    def test_compute_absorption_score_flat_data_returns_zero(self) -> None:
        state = SymbolMicroState("BTCUSDT")
        now = 100_000
        # Constant price & cvd -> std_dev == 0 -> returns 0.0
        for i in range(15):
            state.absorption_snapshots.append((now - (15 - i) * 1000, 50000.0, 100.0))
        assert compute_absorption_score(state, now_ms=now) == 0.0


# ─────────────────────────────────────────────────────────────────────
# 6. Liquidation Cascade Exhaustion (Z_liq)
# ─────────────────────────────────────────────────────────────────────

class TestLiquidationZLiq:
    """Verify rolling liquidation notional sum and Z_liq calculation."""

    def test_rolling_liq_notional_window(self) -> None:
        state = SymbolMicroState("BTCUSDT")
        now = 100_000
        # Event 1: 70 seconds ago (outside 60s window)
        state.add_liquidation(now - 70_000, 50_000.0)
        # Event 2: 30 seconds ago (inside)
        state.add_liquidation(now - 30_000, 20_000.0)
        # Event 3: 5 seconds ago (inside)
        state.add_liquidation(now - 5_000, 30_000.0)

        notional = compute_rolling_liq_notional(state, now_ms=now, window_ms=60_000)
        assert math.isclose(notional, 50_000.0)

    def test_update_z_liq_initial_zero(self) -> None:
        state = SymbolMicroState("BTCUSDT")
        now = 100_000
        # First sample -> normalizer count = 1 -> z_score is 0.0
        z = update_z_liq(state, now_ms=now)
        assert z == 0.0

    def test_update_z_liq_spike_detection(self) -> None:
        """Establish low baseline then introduce huge liquidation burst -> Z >= 3.0."""
        state = SymbolMicroState("BTCUSDT")
        now = 500_000

        # Baseline: 50 ticks of small normal liquidation volume (~1,000)
        for i in range(50):
            ts = now - (50 - i) * 1000
            # Feed state normalizer
            state.liq_normalizer.update(1000.0 + (i % 5) * 10.0)

        # Huge cascade event: 500,000 notional
        state.add_liquidation(now - 1000, 500_000.0)
        z = update_z_liq(state, now_ms=now)

        assert z >= 3.0
