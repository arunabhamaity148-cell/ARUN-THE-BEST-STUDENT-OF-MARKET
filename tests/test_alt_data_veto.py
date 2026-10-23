"""tests/test_alt_data_veto.py — Comprehensive tests for Phase 6D Institutional & Alternative Data Vetoes.

We test:
  1. Deribit DVOL Options Gamma Unwind Veto (ROC calculation & 10m veto trigger)
  2. DefiLlama Stablecoin Supply Drain Veto (REST mock & chain mappings)
  3. US Spot ETF AP Rebalance Window Veto (UTC deterministic & Z_liq > 3.5 override)
  4. Matching-Engine Jitter (E - T) Congestion Veto & 20-frame hysteresis unlock
  5. Memory Constraints (__slots__, no __dict__)
"""

import asyncio
from collections import deque
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from engine.deribit_dvol import (
    compute_dvol_roc,
    DeribitDVOLVeto,
    DVOL_ROC_THRESHOLD,
    GAMMA_UNWIND_DURATION,
)
from engine.alt_data_veto import (
    is_us_dst,
    check_etf_ap_window,
    check_stablecoin_drain,
    check_jitter_congestion,
    AltDataVetoEngine,
    SYMBOL_TO_CHAIN,
)


# ─────────────────────────────────────────────────────────────────────
# 1. Deribit DVOL Options Gamma Unwind Veto
# ─────────────────────────────────────────────────────────────────────

class TestDeribitDVOLVeto:
    """Test options implied volatility spike detection."""

    def test_roc_calculation_pure(self) -> None:
        # 50.0 to 52.0 is +4.0%
        roc = compute_dvol_roc(current_dvol=52.0, baseline_dvol=50.0)
        assert roc == pytest.approx(4.0)

        # Negative change: 50.0 to 48.0 is -4.0%
        roc_neg = compute_dvol_roc(current_dvol=48.0, baseline_dvol=50.0)
        assert roc_neg == pytest.approx(-4.0)

        # Zero baseline guard
        assert compute_dvol_roc(50.0, 0.0) == 0.0

    def test_dvol_spike_triggers_gamma_unwind_veto(self) -> None:
        veto = DeribitDVOLVeto()
        now_mono = 1000.0

        # Baseline DVOL = 50.0
        veto.update_dvol(50.0, channel="btc", now_mono=now_mono)
        assert veto.is_gamma_unwind_active(now_mono=now_mono) is False

        # Small increase: 50.0 to 51.0 (+2.0% < 3.5%) -> No veto
        roc, triggered = veto.update_dvol(51.0, channel="btc", now_mono=now_mono + 1.0)
        assert roc == pytest.approx(2.0)
        assert triggered is False
        assert veto.is_gamma_unwind_active(now_mono=now_mono + 1.0) is False

        # Large spike: 50.0 to 52.0 (+4.0% >= 3.5%) -> Trigger Veto for 600s!
        roc_spike, triggered_spike = veto.update_dvol(52.0, channel="btc", now_mono=now_mono + 2.0)
        assert roc_spike == pytest.approx(4.0)
        assert triggered_spike is True
        assert veto.is_gamma_unwind_active(now_mono=now_mono + 2.0) is True
        assert veto.gamma_unwind_until == pytest.approx(now_mono + 2.0 + GAMMA_UNWIND_DURATION)

        # 599 seconds later -> Veto still active
        assert veto.is_gamma_unwind_active(now_mono=now_mono + 601.0) is True

        # 601 seconds later -> Veto expired
        assert veto.is_gamma_unwind_active(now_mono=now_mono + 603.0) is False

    def test_process_message_mock_ws(self) -> None:
        veto = DeribitDVOLVeto()
        now_mono = 500.0

        # Feed baseline message
        msg_base = {
            "jsonrpc": "2.0",
            "method": "subscription",
            "params": {
                "channel": "deribit_volatility_index.btc_usd",
                "data": {"volatility": 60.0},
            },
        }
        veto.process_message(msg_base, now_mono=now_mono)
        assert veto.latest_btc_dvol == 60.0

        # Feed spike message (60.0 -> 63.0 = +5.0% >= 3.5%)
        msg_spike = {
            "jsonrpc": "2.0",
            "method": "subscription",
            "params": {
                "channel": "deribit_volatility_index.btc_usd",
                "data": {"volatility": 63.0},
            },
        }
        veto.process_message(msg_spike, now_mono=now_mono + 1.0)
        assert veto.latest_btc_dvol == 63.0
        assert veto.is_gamma_unwind_active(now_mono=now_mono + 1.0) is True


# ─────────────────────────────────────────────────────────────────────
# 2. DefiLlama Stablecoin Supply Drain Veto
# ─────────────────────────────────────────────────────────────────────

class TestDefiLlamaStablecoins:
    """Test chain-specific stablecoin outflow filter."""

    def test_stablecoin_drain_rule_pure(self) -> None:
        # Drain <= -1.5% triggers veto
        assert check_stablecoin_drain(-1.5) is True
        assert check_stablecoin_drain(-2.8) is True

        # Benign / positive flow does not trigger
        assert check_stablecoin_drain(-1.49) is False
        assert check_stablecoin_drain(0.0) is False
        assert check_stablecoin_drain(5.2) is False

    def test_symbol_to_chain_mappings(self) -> None:
        engine = AltDataVetoEngine()
        assert engine.symbol_to_chain["SUIUSDT"] == "Sui"
        assert engine.symbol_to_chain["SOLUSDT"] == "Solana"
        assert engine.symbol_to_chain["AVAXUSDT"] == "Avalanche"
        assert engine.symbol_to_chain["NEARUSDT"] == "Near"
        assert engine.symbol_to_chain["TONUSDT"] == "TON"

    def test_stablecoin_draining_veto(self) -> None:
        engine = AltDataVetoEngine()

        # Set Solana stablecoins draining (-2.0%)
        engine.set_chain_change("Solana", -2.0)
        assert engine.is_stablecoin_draining("SOLUSDT") is True

        # Sui is healthy (+1.2%)
        engine.set_chain_change("Sui", 1.2)
        assert engine.is_stablecoin_draining("SUIUSDT") is False

        # Non-L1 tokens like BTC / ETH are not constrained by L1 stablecoin drainage
        assert engine.is_stablecoin_draining("BTCUSDT") is False
        assert engine.is_stablecoin_draining("ETHUSDT") is False

    @pytest.mark.asyncio
    async def test_fetch_defillama_rest_mock(self, tmp_path) -> None:
        engine = AltDataVetoEngine(cache_dir=str(tmp_path))
        mock_payload = [
            {"name": "Solana", "change_1d": -2.5},
            {"name": "Sui", "change_1d": 0.8},
            {"name": "Avalanche", "totalCirculatingUSD": {"peggedUSD": 980.0}, "totalCirculatingUSDPrevDay": {"peggedUSD": 1000.0}},
        ]
        mock_bytes = b'[{"name":"Solana","change_1d":-2.5},{"name":"Sui","change_1d":0.8},{"name":"Avalanche","totalCirculatingUSD":{"peggedUSD":980.0},"totalCirculatingUSDPrevDay":{"peggedUSD":1000.0}}]'

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.read = AsyncMock(return_value=mock_bytes)
        mock_resp.__aenter__.return_value = mock_resp
        mock_resp.__aexit__.return_value = False

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp
        mock_session.close = AsyncMock()

        with patch("aiohttp.ClientSession", return_value=mock_session):
            engine.last_poll_ts = 0.0  # Force refresh
            await engine.fetch_defillama_data()

        assert engine.chain_24h_changes["solana"] == -2.5
        assert engine.chain_24h_changes["sui"] == 0.8
        # Avalanche: (980 - 1000) / 1000 * 100 = -2.0%
        assert engine.chain_24h_changes["avalanche"] == pytest.approx(-2.0)

        # Verify veto triggers on parsed mock data
        assert engine.is_stablecoin_draining("SOLUSDT") is True
        assert engine.is_stablecoin_draining("AVAXUSDT") is True
        assert engine.is_stablecoin_draining("SUIUSDT") is False

    @pytest.mark.asyncio
    async def test_fetch_defillama_live_schema_mock(self, tmp_path) -> None:
        """Test DefiLlama's actual production schema: list of {'name': ..., 'totalCirculatingUSD': {'peggedUSD': ...}}."""
        engine = AltDataVetoEngine(cache_dir=str(tmp_path))

        # Frame 1: Baseline supply
        payload_1 = (
            b'[{"name": "Sui", "totalCirculatingUSD": {"peggedUSD": 500000000.0}},'
            b' {"name": "Solana", "totalCirculatingUSD": {"peggedUSD": 10000000000.0}},'
            b' {"name": "Near", "gecko_id": "near", "totalCirculatingUSD": {"peggedUSD": 200000000.0}}]'
        )
        mock_resp1 = AsyncMock()
        mock_resp1.status = 200
        mock_resp1.read = AsyncMock(return_value=payload_1)
        mock_resp1.__aenter__.return_value = mock_resp1
        mock_resp1.__aexit__.return_value = False

        mock_session1 = MagicMock()
        mock_session1.get.return_value = mock_resp1
        mock_session1.close = AsyncMock()

        with patch("aiohttp.ClientSession", return_value=mock_session1):
            engine.last_poll_ts = 0.0
            await engine.fetch_defillama_data()

        # All 3 chains parsed successfully on baseline
        assert "sui" in engine.chain_24h_changes
        assert "solana" in engine.chain_24h_changes
        assert "near" in engine.chain_24h_changes
        assert engine.chain_24h_changes["solana"] == 0.0
        assert engine.is_stablecoin_draining("SOLUSDT") is False

        # Frame 2: Solana supply drains by 3.0% (10.0B -> 9.7B), Sui increases (+4.0%)
        payload_2 = (
            b'[{"name": "Sui", "totalCirculatingUSD": {"peggedUSD": 520000000.0}},'
            b' {"name": "Solana", "totalCirculatingUSD": {"peggedUSD": 9700000000.0}}]'
        )
        mock_resp2 = AsyncMock()
        mock_resp2.status = 200
        mock_resp2.read = AsyncMock(return_value=payload_2)
        mock_resp2.__aenter__.return_value = mock_resp2
        mock_resp2.__aexit__.return_value = False

        mock_session2 = MagicMock()
        mock_session2.get.return_value = mock_resp2
        mock_session2.close = AsyncMock()

        with patch("aiohttp.ClientSession", return_value=mock_session2):
            engine.last_poll_ts = 0.0  # Force update
            await engine.fetch_defillama_data()

        assert engine.chain_24h_changes["solana"] == pytest.approx(-3.0)
        assert engine.chain_24h_changes["sui"] == pytest.approx(4.0)

        # Solana veto triggers (change <= -1.5%)
        assert engine.is_stablecoin_draining("SOLUSDT") is True
        # Sui is healthy
        assert engine.is_stablecoin_draining("SUIUSDT") is False


# ─────────────────────────────────────────────────────────────────────
# 3. US Spot ETF AP Rebalance Window Veto
# ─────────────────────────────────────────────────────────────────────

class TestETFAPWindowVeto:
    """Test 15:45 - 16:15 ET Spot ETF Authorized Participant Window."""

    def test_dst_determination(self) -> None:
        # Summer (July 15) -> US DST active
        summer_dt = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
        assert is_us_dst(summer_dt) is True

        # Winter (January 15) -> US Standard Time active
        winter_dt = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
        assert is_us_dst(winter_dt) is False

    def test_summer_etf_window_1945_to_2015_utc(self) -> None:
        # Summer date (July 15)
        # 19:40 UTC -> outside
        assert check_etf_ap_window(datetime(2026, 7, 15, 19, 40, tzinfo=timezone.utc)) is False

        # 19:45 UTC -> inside window (vetoed)
        assert check_etf_ap_window(datetime(2026, 7, 15, 19, 45, tzinfo=timezone.utc)) is True

        # 20:00 UTC -> inside window (vetoed)
        assert check_etf_ap_window(datetime(2026, 7, 15, 20, 0, tzinfo=timezone.utc)) is True

        # 20:15 UTC -> inside window (vetoed)
        assert check_etf_ap_window(datetime(2026, 7, 15, 20, 15, tzinfo=timezone.utc)) is True

        # 20:16 UTC -> outside
        assert check_etf_ap_window(datetime(2026, 7, 15, 20, 16, tzinfo=timezone.utc)) is False

    def test_winter_etf_window_2045_to_2115_utc(self) -> None:
        # Winter date (January 15)
        # 20:40 UTC -> outside
        assert check_etf_ap_window(datetime(2026, 1, 15, 20, 40, tzinfo=timezone.utc)) is False

        # 20:45 UTC -> inside window (vetoed)
        assert check_etf_ap_window(datetime(2026, 1, 15, 20, 45, tzinfo=timezone.utc)) is True

        # 21:00 UTC -> inside window (vetoed)
        assert check_etf_ap_window(datetime(2026, 1, 15, 21, 0, tzinfo=timezone.utc)) is True

        # 21:15 UTC -> inside window (vetoed)
        assert check_etf_ap_window(datetime(2026, 1, 15, 21, 15, tzinfo=timezone.utc)) is True

        # 21:20 UTC -> outside
        assert check_etf_ap_window(datetime(2026, 1, 15, 21, 20, tzinfo=timezone.utc)) is False

    def test_zliq_exhaustion_override_unlocks_etf_window(self) -> None:
        # Inside ETF window during summer
        dt = datetime(2026, 7, 15, 20, 0, tzinfo=timezone.utc)

        # Normal condition -> Blocked
        assert check_etf_ap_window(dt, z_liq=2.0) is True
        assert check_etf_ap_window(dt, z_liq=3.5) is True

        # Extreme liquidation exhaustion (Z_liq > 3.5) -> OVERRIDE UNLOCKED!
        assert check_etf_ap_window(dt, z_liq=3.6) is False
        assert check_etf_ap_window(dt, z_liq=5.0) is False


# ─────────────────────────────────────────────────────────────────────
# 4. Matching-Engine Jitter Veto (E - T)
# ─────────────────────────────────────────────────────────────────────

class TestMatchingEngineJitterVeto:
    """Test exchange matching-engine latency spike and hysteresis unlock."""

    def test_jitter_hysteresis_state_machine_pure(self) -> None:
        # Normal low jitters (<= 10ms) -> Not vetoed
        jitters = [5.0, 6.0, 7.0, 8.0, 5.0]
        vetoed, count = check_jitter_congestion(jitters, is_currently_vetoed=False, consecutive_low_count=0)
        assert vetoed is False

        # Sudden latency spike: last 5 average = (40 + 42 + 38 + 45 + 50) / 5 = 43ms > 35ms -> VETOED!
        spike_jitters = [40.0, 42.0, 38.0, 45.0, 50.0]
        vetoed, count = check_jitter_congestion(spike_jitters, is_currently_vetoed=False, consecutive_low_count=0)
        assert vetoed is True
        assert count == 0

        # While vetoed, 19 consecutive low frames (<= 10ms) -> Still vetoed
        state_vetoed = True
        c_count = 0
        for _ in range(19):
            state_vetoed, c_count = check_jitter_congestion([8.0], state_vetoed, c_count)
            assert state_vetoed is True

        # 20th consecutive low frame -> UNLOCKED!
        state_vetoed, c_count = check_jitter_congestion([8.0], state_vetoed, c_count)
        assert state_vetoed is False
        assert c_count == 20

    def test_jitter_reset_if_glitch_occurs_during_cooldown(self) -> None:
        state_vetoed = True
        c_count = 0

        # 10 good frames
        for _ in range(10):
            state_vetoed, c_count = check_jitter_congestion([5.0], state_vetoed, c_count)

        assert c_count == 10
        assert state_vetoed is True

        # 1 bad frame (> 10ms) -> Reset count to 0!
        state_vetoed, c_count = check_jitter_congestion([15.0], state_vetoed, c_count)
        assert c_count == 0
        assert state_vetoed is True

    def test_stateful_engine_jitter_tracking(self) -> None:
        engine = AltDataVetoEngine()
        ring = deque(maxlen=20)

        # Feed 5 high-jitter updates
        for j in (36.0, 38.0, 40.0, 42.0, 39.0):
            ring.append(j)

        assert engine.is_engine_congested(ring) is True

        # Feed 20 clean updates
        for _ in range(20):
            ring.append(8.0)
            engine.is_engine_congested(ring)

        assert engine.is_engine_congested(ring) is False


# ─────────────────────────────────────────────────────────────────────
# 5. Slots & Memory Verification
# ─────────────────────────────────────────────────────────────────────

class TestAltDataSlots:

    def test_deribit_slots(self) -> None:
        assert hasattr(DeribitDVOLVeto, "__slots__")
        veto = DeribitDVOLVeto()
        assert not hasattr(veto, "__dict__")
        assert "DeribitDVOLVeto" in repr(veto)

    def test_alt_data_slots(self) -> None:
        assert hasattr(AltDataVetoEngine, "__slots__")
        engine = AltDataVetoEngine()
        assert not hasattr(engine, "__dict__")
        assert "AltDataVetoEngine" in repr(engine)
