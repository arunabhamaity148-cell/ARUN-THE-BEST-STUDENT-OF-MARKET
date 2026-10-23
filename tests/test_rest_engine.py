"""tests/test_rest_engine.py — Comprehensive tests for engine/rest_engine.py.

Verifies:
1. Slots and memory compliance.
2. 180-second TTL caching behavior.
3. Rate limit guard on 'X-MBX-USED-WEIGHT-1M' (> 1800 units blocks calls).
4. Delta OI % calculation: ((OI_now - OI_3_periods_ago) / OI_3_periods_ago) * 100.
5. Basis bps calculation: ((mark_price - index_price) / index_price) * 10000.
6. nextFundingTime extraction and caching.
7. Clean session lifecycle management.
"""

import sys
import os
import time
from unittest.mock import AsyncMock, MagicMock

import orjson
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine.rest_engine import (
    LazyRestConfirmedEngine,
    MAX_USED_WEIGHT_1M,
    CACHE_TTL_SECONDS,
    WEIGHT_HEADER,
)


class MockGetContext:
    """Async context manager mock for aiohttp session.get."""
    def __init__(self, resp):
        self.resp = resp

    async def __aenter__(self):
        return self.resp

    async def __aexit__(self, *args):
        pass


class TestLazyRestConfirmedEngineSlots:
    """Verify slots enforcement."""

    def test_has_slots(self) -> None:
        assert hasattr(LazyRestConfirmedEngine, "__slots__")

    def test_no_dict(self) -> None:
        engine = LazyRestConfirmedEngine()
        assert not hasattr(engine, "__dict__")


class TestLazyRestConfirmedEngineCaching:
    """Verify 180-second cache prevents redundant network requests."""

    @pytest.mark.asyncio
    async def test_cached_result_returned_without_network(self) -> None:
        mock_session = MagicMock()
        mock_session.closed = False
        engine = LazyRestConfirmedEngine(session=mock_session)

        now = time.time()
        # Seed cache manually
        engine.oi_cache["BTCUSDT"] = (now, 3.5, 0.0001, 5.2)

        # Call at now + 30s (well within 180s)
        data = await engine.get_derivatives_data("BTCUSDT", now_ts=now + 30.0)
        assert data == (3.5, 0.0001, 5.2)
        mock_session.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_cache_expires_after_180_seconds(self) -> None:
        mock_session = MagicMock()
        mock_session.closed = False
        engine = LazyRestConfirmedEngine(session=mock_session)
        t0 = 1000.0
        engine.oi_cache["ETHUSDT"] = (t0, 1.0, 0.0001, 2.0)

        # Mock responses for when cache expires at t0 + 181s
        oi_body = orjson.dumps([
            {"sumOpenInterest": "100.0"},
            {"sumOpenInterest": "105.0"},
            {"sumOpenInterest": "110.0"},
            {"sumOpenInterest": "120.0"},
        ])
        prem_body = orjson.dumps({
            "markPrice": "3010.0",
            "indexPrice": "3000.0",
            "lastFundingRate": "0.0002",
            "nextFundingTime": 1700000000000,
        })

        mock_oi_resp = MagicMock()
        mock_oi_resp.status = 200
        mock_oi_resp.headers = {WEIGHT_HEADER: "15"}
        mock_oi_resp.read = AsyncMock(return_value=oi_body)

        mock_prem_resp = MagicMock()
        mock_prem_resp.status = 200
        mock_prem_resp.headers = {WEIGHT_HEADER: "16"}
        mock_prem_resp.read = AsyncMock(return_value=prem_body)

        mock_session.get.side_effect = [
            MockGetContext(mock_oi_resp),
            MockGetContext(mock_prem_resp),
        ]

        # Call with now_ts = t0 + 185s -> cache miss -> queries network
        result = await engine.get_derivatives_data("ETHUSDT", now_ts=t0 + 185.0)

        # OI changed from 100.0 to 120.0 -> +20.0%
        # Basis bps: ((3010 - 3000) / 3000) * 10000 = (10 / 3000) * 10000 = 33.3333 bps
        delta_oi, funding, basis = result
        assert pytest.approx(delta_oi, 0.01) == 20.0
        assert pytest.approx(funding, 0.00001) == 0.0002
        assert pytest.approx(basis, 0.01) == 33.3333

        # Cache must be updated
        cached_ts, c_oi, c_fr, c_basis = engine.oi_cache["ETHUSDT"]
        assert cached_ts == t0 + 185.0
        assert engine.get_next_funding_time("ETHUSDT") == 1700000000000


class TestLazyRestConfirmedEngineRateLimitGuard:
    """Verify X-MBX-USED-WEIGHT-1M rate limit defenses."""

    @pytest.mark.asyncio
    async def test_rate_limit_exceeded_returns_neutral_fallback(self) -> None:
        mock_session = MagicMock()
        mock_session.closed = False
        engine = LazyRestConfirmedEngine(session=mock_session)
        now = 5000.0

        # Simulate rate limit exceeded
        engine.current_weight = 1850
        engine.weight_reset_time = now + 40.0

        result = await engine.get_derivatives_data("SOLUSDT", now_ts=now)
        assert result == (0.0, 0.0, 0.0)
        mock_session.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_rate_limit_resets_after_window(self) -> None:
        mock_session = MagicMock()
        mock_session.closed = False
        engine = LazyRestConfirmedEngine(session=mock_session)
        now = 5000.0

        # Current weight was high, but window expired (now >= reset_time)
        engine.current_weight = 1900
        engine.weight_reset_time = now - 1.0

        mock_oi_resp = MagicMock()
        mock_oi_resp.status = 200
        mock_oi_resp.headers = {WEIGHT_HEADER: "10"}
        mock_oi_resp.read = AsyncMock(return_value=orjson.dumps([
            {"sumOpenInterest": "50.0"},
            {"sumOpenInterest": "50.0"},
            {"sumOpenInterest": "50.0"},
            {"sumOpenInterest": "50.0"},
        ]))

        mock_prem_resp = MagicMock()
        mock_prem_resp.status = 200
        mock_prem_resp.headers = {WEIGHT_HEADER: "12"}
        mock_prem_resp.read = AsyncMock(return_value=orjson.dumps({
            "markPrice": "100.0",
            "indexPrice": "100.0",
            "lastFundingRate": "0.0",
            "nextFundingTime": 0,
        }))

        mock_session.get.side_effect = [
            MockGetContext(mock_oi_resp),
            MockGetContext(mock_prem_resp),
        ]

        # Should execute requests now that minute has reset
        res = await engine.get_derivatives_data("SOLUSDT", now_ts=now)
        assert res == (0.0, 0.0, 0.0)
        assert engine.current_weight == 12
        assert engine.weight_reset_time > now


class TestLazyRestConfirmedEngineCalculations:
    """Verify exact formulas for OI delta % and Basis bps."""

    @pytest.mark.asyncio
    async def test_zero_division_guard(self) -> None:
        mock_session = MagicMock()
        mock_session.closed = False
        engine = LazyRestConfirmedEngine(session=mock_session)

        # Zero initial OI and zero index price
        oi_body = orjson.dumps([
            {"sumOpenInterest": "0.0"},
            {"sumOpenInterest": "0.0"},
            {"sumOpenInterest": "0.0"},
            {"sumOpenInterest": "50.0"},
        ])
        prem_body = orjson.dumps({
            "markPrice": "100.0",
            "indexPrice": "0.0",
            "lastFundingRate": "0.0001",
        })

        mock_oi_resp = MagicMock()
        mock_oi_resp.status = 200
        mock_oi_resp.headers = {}
        mock_oi_resp.read = AsyncMock(return_value=oi_body)

        mock_prem_resp = MagicMock()
        mock_prem_resp.status = 200
        mock_prem_resp.headers = {}
        mock_prem_resp.read = AsyncMock(return_value=prem_body)

        mock_session.get.side_effect = [
            MockGetContext(mock_oi_resp),
            MockGetContext(mock_prem_resp),
        ]

        delta_oi, funding, basis = await engine.get_derivatives_data("BTCUSDT", now_ts=1000.0)
        assert delta_oi == 0.0
        assert basis == 0.0
        assert funding == 0.0001

    @pytest.mark.asyncio
    async def test_session_close(self) -> None:
        engine = LazyRestConfirmedEngine()
        sess = await engine._get_session()
        assert not sess.closed
        await engine.close()
        assert sess.closed
