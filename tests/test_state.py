"""tests/test_state.py — Tests for core/state.py (OnlineNormalizer + SymbolMicroState)."""

import math
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.state import OnlineNormalizer, SymbolMicroState
from config import TRADE_WINDOW, DEPTH_WINDOW, SPREAD_WINDOW


# ─────────────────────────────────────────────────────────────────────
# OnlineNormalizer (Welford's algorithm)
# ─────────────────────────────────────────────────────────────────────

class TestOnlineNormalizerSlots:
    """Verify __slots__ is enforced."""

    def test_has_slots(self) -> None:
        assert hasattr(OnlineNormalizer, "__slots__")

    def test_no_instance_dict(self) -> None:
        n = OnlineNormalizer()
        assert not hasattr(n, "__dict__")


class TestOnlineNormalizerEmpty:
    """Behaviour with zero or one sample."""

    def test_initial_count(self) -> None:
        n = OnlineNormalizer()
        assert n.count == 0

    def test_initial_mean(self) -> None:
        n = OnlineNormalizer()
        assert n.mean == 0.0

    def test_initial_variance(self) -> None:
        n = OnlineNormalizer()
        assert n.variance == 0.0

    def test_initial_std_dev(self) -> None:
        n = OnlineNormalizer()
        assert n.std_dev == 0.0

    def test_z_score_no_data(self) -> None:
        n = OnlineNormalizer()
        assert n.z_score(42.0) == 0.0

    def test_single_sample_variance(self) -> None:
        n = OnlineNormalizer()
        n.update(5.0)
        assert n.count == 1
        assert n.mean == 5.0
        assert n.variance == 0.0

    def test_single_sample_z_score(self) -> None:
        n = OnlineNormalizer()
        n.update(5.0)
        assert n.z_score(5.0) == 0.0


class TestOnlineNormalizerKnown:
    """Test against hand-calculated results.

    Dataset: [2, 4, 4, 4, 5, 5, 7, 9]
    Count   = 8
    Mean    = 5.0
    Pop σ²  = 4.0
    Pop σ   = 2.0
    z(7)    = (7 − 5) / 2 = 1.0
    z(3)    = (3 − 5) / 2 = −1.0
    """

    def _make(self) -> OnlineNormalizer:
        n = OnlineNormalizer()
        for v in [2, 4, 4, 4, 5, 5, 7, 9]:
            n.update(v)
        return n

    def test_count(self) -> None:
        assert self._make().count == 8

    def test_mean(self) -> None:
        assert math.isclose(self._make().mean, 5.0, abs_tol=1e-10)

    def test_variance(self) -> None:
        assert math.isclose(self._make().variance, 4.0, abs_tol=1e-10)

    def test_std_dev(self) -> None:
        assert math.isclose(self._make().std_dev, 2.0, abs_tol=1e-10)

    def test_z_score_positive(self) -> None:
        assert math.isclose(self._make().z_score(7.0), 1.0, abs_tol=1e-10)

    def test_z_score_negative(self) -> None:
        assert math.isclose(self._make().z_score(3.0), -1.0, abs_tol=1e-10)

    def test_z_score_at_mean(self) -> None:
        assert math.isclose(self._make().z_score(5.0), 0.0, abs_tol=1e-10)


class TestOnlineNormalizerReset:
    """Verify reset clears all state."""

    def test_reset_clears(self) -> None:
        n = OnlineNormalizer()
        for v in range(100):
            n.update(float(v))
        n.reset()
        assert n.count == 0
        assert n.mean == 0.0
        assert n.variance == 0.0


class TestOnlineNormalizerRepr:
    """Repr should be informative."""

    def test_repr_contains_count(self) -> None:
        n = OnlineNormalizer()
        n.update(1.0)
        r = repr(n)
        assert "count=1" in r
        assert "OnlineNormalizer" in r


class TestOnlineNormalizerStreaming:
    """Verify incremental updates produce same result as batch."""

    def test_incremental_vs_batch(self) -> None:
        """Compare Welford's result with stdlib statistics on 1000 values."""
        import statistics

        values = [float(i % 37) for i in range(1000)]
        n = OnlineNormalizer()
        for v in values:
            n.update(v)

        expected_mean = statistics.mean(values)
        expected_pvariance = statistics.pvariance(values)

        assert math.isclose(n.mean, expected_mean, rel_tol=1e-9)
        assert math.isclose(n.variance, expected_pvariance, rel_tol=1e-9)


# ─────────────────────────────────────────────────────────────────────
# SymbolMicroState
# ─────────────────────────────────────────────────────────────────────

class TestSymbolMicroStateSlots:
    """Verify __slots__ is enforced."""

    def test_has_slots(self) -> None:
        assert hasattr(SymbolMicroState, "__slots__")

    def test_no_instance_dict(self) -> None:
        s = SymbolMicroState("BTCUSDT")
        assert not hasattr(s, "__dict__")


class TestSymbolMicroStateInit:
    """Verify clean initial state."""

    def test_symbol(self) -> None:
        s = SymbolMicroState("ETHUSDT")
        assert s.symbol == "ETHUSDT"

    def test_initial_prices_zero(self) -> None:
        s = SymbolMicroState("BTCUSDT")
        assert s.best_bid == 0.0
        assert s.best_ask == 0.0

    def test_initial_cvd_zero(self) -> None:
        s = SymbolMicroState("BTCUSDT")
        assert s.cvd == 0.0

    def test_deques_empty(self) -> None:
        s = SymbolMicroState("BTCUSDT")
        assert len(s.trade_prices) == 0
        assert len(s.bid_depths) == 0
        assert len(s.spread_history) == 0

    def test_deque_maxlens(self) -> None:
        s = SymbolMicroState("BTCUSDT")
        assert s.trade_prices.maxlen == TRADE_WINDOW
        assert s.trade_volumes.maxlen == TRADE_WINDOW
        assert s.bid_depths.maxlen == DEPTH_WINDOW
        assert s.ask_depths.maxlen == DEPTH_WINDOW
        assert s.spread_history.maxlen == SPREAD_WINDOW


class TestSymbolMicroStateBookTicker:
    """Test update_book_ticker method."""

    def test_sets_best_bid_ask(self) -> None:
        s = SymbolMicroState("BTCUSDT")
        s.update_book_ticker(bid=50000.0, bid_qty=1.5, ask=50001.0, ask_qty=2.0)
        assert s.best_bid == 50000.0
        assert s.best_bid_qty == 1.5
        assert s.best_ask == 50001.0
        assert s.best_ask_qty == 2.0

    def test_records_spread(self) -> None:
        s = SymbolMicroState("BTCUSDT")
        s.update_book_ticker(bid=50000.0, bid_qty=1.0, ask=50001.0, ask_qty=1.0)
        assert len(s.spread_history) == 1
        assert math.isclose(s.spread_history[0], 1.0)

    def test_spread_property(self) -> None:
        s = SymbolMicroState("BTCUSDT")
        s.update_book_ticker(bid=100.0, bid_qty=1.0, ask=102.0, ask_qty=1.0)
        assert math.isclose(s.spread, 2.0)

    def test_mid_price(self) -> None:
        s = SymbolMicroState("BTCUSDT")
        s.update_book_ticker(bid=100.0, bid_qty=1.0, ask=110.0, ask_qty=1.0)
        assert math.isclose(s.mid_price, 105.0)


class TestSymbolMicroStateTrade:
    """Test update_trade method."""

    def test_buyer_initiated_adds_cvd(self) -> None:
        s = SymbolMicroState("BTCUSDT")
        s.update_trade(price=50000.0, qty=0.5, is_buyer_maker=False, trade_time=1000)
        assert math.isclose(s.cvd, 0.5)

    def test_seller_initiated_subtracts_cvd(self) -> None:
        s = SymbolMicroState("BTCUSDT")
        s.update_trade(price=50000.0, qty=0.3, is_buyer_maker=True, trade_time=1000)
        assert math.isclose(s.cvd, -0.3)

    def test_multiple_trades_accumulate(self) -> None:
        s = SymbolMicroState("BTCUSDT")
        s.update_trade(price=100.0, qty=1.0, is_buyer_maker=False, trade_time=1)
        s.update_trade(price=101.0, qty=2.0, is_buyer_maker=True, trade_time=2)
        s.update_trade(price=102.0, qty=0.5, is_buyer_maker=False, trade_time=3)
        # CVD = +1.0 − 2.0 + 0.5 = −0.5
        assert math.isclose(s.cvd, -0.5)

    def test_records_price_and_volume(self) -> None:
        s = SymbolMicroState("BTCUSDT")
        s.update_trade(price=50000.0, qty=0.1, is_buyer_maker=False, trade_time=999)
        assert len(s.trade_prices) == 1
        assert s.trade_prices[0] == 50000.0
        assert len(s.trade_volumes) == 1
        assert s.trade_volumes[0] == 0.1

    def test_updates_last_price_and_time(self) -> None:
        s = SymbolMicroState("BTCUSDT")
        s.update_trade(price=42000.0, qty=1.0, is_buyer_maker=False, trade_time=12345)
        assert s.last_price == 42000.0
        assert s.last_trade_time == 12345

    def test_trade_deque_bounded(self) -> None:
        s = SymbolMicroState("BTCUSDT")
        for i in range(TRADE_WINDOW + 100):
            s.update_trade(price=float(i), qty=0.01, is_buyer_maker=False, trade_time=i)
        assert len(s.trade_prices) == TRADE_WINDOW


class TestSymbolMicroStateDepth:
    """Test update_depth method."""

    def test_records_bid_ask_depth(self) -> None:
        s = SymbolMicroState("BTCUSDT")
        s.update_depth(total_bid_depth=500.0, total_ask_depth=450.0)
        assert len(s.bid_depths) == 1
        assert s.bid_depths[0] == 500.0
        assert s.ask_depths[0] == 450.0

    def test_depth_deque_bounded(self) -> None:
        s = SymbolMicroState("BTCUSDT")
        for i in range(DEPTH_WINDOW + 50):
            s.update_depth(float(i), float(i))
        assert len(s.bid_depths) == DEPTH_WINDOW


class TestSymbolMicroStateZScores:
    """Test z-score derived properties."""

    def test_cvd_z_with_data(self) -> None:
        s = SymbolMicroState("BTCUSDT")
        # Feed enough trades to establish statistics
        for i in range(50):
            s.update_trade(
                price=50000.0, qty=1.0,
                is_buyer_maker=(i % 2 == 0),
                trade_time=i,
            )
        # Should return a float (not crash)
        z = s.cvd_z
        assert isinstance(z, float)

    def test_spread_z_with_data(self) -> None:
        s = SymbolMicroState("BTCUSDT")
        for i in range(20):
            s.update_book_ticker(
                bid=50000.0, bid_qty=1.0,
                ask=50000.0 + float(i % 5 + 1), ask_qty=1.0,
            )
        z = s.spread_z
        assert isinstance(z, float)


class TestSymbolMicroStateRepr:
    """Repr should be human-readable."""

    def test_repr(self) -> None:
        s = SymbolMicroState("SOLUSDT")
        r = repr(s)
        assert "SOLUSDT" in r
        assert "SymbolMicroState" in r


# ─────────────────────────────────────────────────────────────────────
# SymbolMicroState — Phase 6 Spot, COIN-M & Jitter Rings
# ─────────────────────────────────────────────────────────────────────

class TestSymbolMicroStatePhase6:
    """Verify Phase 6 ring buffers and jitter tracking."""

    def test_phase6_deques_initialized(self) -> None:
        s = SymbolMicroState("BTCUSDT")
        assert len(s.spot_cvd_lm) == 0
        assert len(s.spot_price_ring) == 0
        assert len(s.coin_m_ofi_ring) == 0
        assert len(s.matching_engine_jitter_ring) == 0

    def test_jitter_ring_maxlen_is_twenty(self) -> None:
        s = SymbolMicroState("BTCUSDT")
        assert s.matching_engine_jitter_ring.maxlen == 20

    def test_record_jitter(self) -> None:
        s = SymbolMicroState("BTCUSDT")
        jitter = s.record_jitter(event_time=1025.0, trade_time=1000.0)
        assert math.isclose(jitter, 25.0)
        assert len(s.matching_engine_jitter_ring) == 1
        assert math.isclose(s.matching_engine_jitter_ring[0], 25.0)

    def test_jitter_ring_bounded_to_twenty(self) -> None:
        s = SymbolMicroState("BTCUSDT")
        for i in range(30):
            s.record_jitter(event_time=100.0 + i, trade_time=100.0)
        assert len(s.matching_engine_jitter_ring) == 20
        assert math.isclose(s.matching_engine_jitter_ring[-1], 29.0)

    def test_update_jitter(self) -> None:
        s = SymbolMicroState("BTCUSDT")
        s.update_jitter(14.2)
        assert len(s.matching_engine_jitter_ring) == 1
        assert math.isclose(s.matching_engine_jitter_ring[0], 14.2)

    def test_update_spot_trade(self) -> None:
        s = SymbolMicroState("BTCUSDT")
        s.update_spot_trade(price=64000.0, qty=0.5, is_buyer_maker=False, trade_time=12345)
        assert len(s.spot_price_ring) == 1
        assert s.spot_price_ring[-1] == 64000.0
        assert len(s.spot_cvd_lm) == 1
        assert s.spot_cvd_lm[-1] == (12345, 0.5)

    def test_spot_cvd_1m_alias(self) -> None:
        s = SymbolMicroState("BTCUSDT")
        assert s.spot_cvd_1m is s.spot_cvd_lm
        s.update_spot_trade(price=64000.0, qty=1.0, is_buyer_maker=True, trade_time=555)
        assert s.spot_cvd_1m[-1] == (555, -1.0)

    def test_update_coin_m_ofi(self) -> None:
        s = SymbolMicroState("BTCUSDT")
        s.update_coin_m_ofi(155.8)
        assert len(s.coin_m_ofi_ring) == 1
        assert math.isclose(s.coin_m_ofi_ring[-1], 155.8)

    def test_update_trade_with_event_time_records_jitter(self) -> None:
        s = SymbolMicroState("BTCUSDT")
        s.update_trade(price=64000.0, qty=1.0, is_buyer_maker=False, trade_time=1000, event_time=1018)
        assert len(s.matching_engine_jitter_ring) == 1
        assert math.isclose(s.matching_engine_jitter_ring[-1], 18.0)

