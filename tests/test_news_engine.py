"""tests/test_news_engine.py — Comprehensive tests for engine/news_engine.py.

Verifies:
1. Slots and memory compliance.
2. ISO8601 datetime parsing to UNIX timestamp.
3. ForexFactory data parsing: USD + High impact filtering.
4. 12-hour disk cache hit/expiration logic.
5. Macro blackout window: ±15 minutes (±900s) around USD high-impact news.
6. RSS feed XML parsing via xml.etree.ElementTree with root.clear().
7. Keyword shock detection: 'hack', 'exploited', 'insolvency', 'sec sues', 'depeg'.
8. 45-minute (2700s) blackout window upon shock detection.
"""

import os
import sys
import tempfile
import time
from unittest.mock import AsyncMock, MagicMock

import orjson
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine.news_engine import (
    MacroAndNewsVetoEngine,
    parse_iso_datetime,
    check_headline_shock,
    parse_rss_titles,
    MACRO_BLACKOUT_WINDOW_S,
    NEWS_SHOCK_BLACKOUT_S,
    FF_CACHE_TTL_SECONDS,
)


class MockGetContext:
    def __init__(self, resp):
        self.resp = resp
    async def __aenter__(self):
        return self.resp
    async def __aexit__(self, *args):
        pass


class TestNewsEngineSlots:
    """Verify slots enforcement."""

    def test_has_slots(self) -> None:
        assert hasattr(MacroAndNewsVetoEngine, "__slots__")

    def test_no_dict(self) -> None:
        engine = MacroAndNewsVetoEngine()
        assert not hasattr(engine, "__dict__")

    def test_repr(self) -> None:
        engine = MacroAndNewsVetoEngine()
        r = repr(engine)
        assert "MacroAndNewsVetoEngine" in r


class TestMacroCalendarParsingAndCache:
    """Verify ForexFactory parsing, filtering, and disk caching."""

    def test_parse_iso_datetime(self) -> None:
        ts = parse_iso_datetime("2026-10-23T14:30:00+00:00")
        assert ts > 0.0

    def test_filter_usd_high_impact_only(self) -> None:
        raw_events = [
            {"title": "CPI", "country": "USD", "impact": "High", "date": "2026-10-23T12:00:00+00:00"},
            {"title": "Retail Sales", "country": "USD", "impact": "Medium", "date": "2026-10-23T13:00:00+00:00"},
            {"title": "ECB Rate", "country": "EUR", "impact": "High", "date": "2026-10-23T14:00:00+00:00"},
            {"title": "Non-Farm Payrolls", "country": "USD", "impact": "High", "date": "2026-10-23T15:00:00+00:00"},
        ]
        engine = MacroAndNewsVetoEngine()
        engine._parse_calendar_data(raw_events)

        assert len(engine.usd_event_timestamps) == 2

    def test_disk_cache_load_and_save(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            engine = MacroAndNewsVetoEngine(cache_path=tmp_path)
            events = [
                {"title": "FOMC", "country": "USD", "impact": "High", "date": "2026-10-23T18:00:00+00:00"}
            ]
            engine.save_cache_to_disk(events)
            assert os.path.exists(tmp_path)

            # Load into fresh engine
            now = time.time()
            engine2 = MacroAndNewsVetoEngine(cache_path=tmp_path)
            loaded = engine2.load_cache_from_disk(now_ts=now)
            assert loaded is True
            assert len(engine2.usd_event_timestamps) == 1

            # Expired cache (> 12h)
            expired = engine2.load_cache_from_disk(now_ts=now + FF_CACHE_TTL_SECONDS + 10.0)
            assert expired is False
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)


class TestMacroBlackoutWindow:
    """Verify ±15 minute (900s) blackout window around events."""

    def test_macro_blackout_timing(self) -> None:
        engine = MacroAndNewsVetoEngine()
        event_time = 1_000_000.0
        engine.usd_event_timestamps = [event_time]

        # 1000s before event -> outside 900s window -> False
        assert engine.is_macro_blackout(now_ts=event_time - 1000.0) is False

        # 800s before event -> inside 900s window -> True
        assert engine.is_macro_blackout(now_ts=event_time - 800.0) is True

        # Exactly at event time -> True
        assert engine.is_macro_blackout(now_ts=event_time) is True

        # 800s after event -> inside 900s window -> True
        assert engine.is_macro_blackout(now_ts=event_time + 800.0) is True

        # 1000s after event -> outside 900s window -> False
        assert engine.is_macro_blackout(now_ts=event_time + 1000.0) is False


class TestRSSShockScanner:
    """Verify XML parsing, keyword shock filtering, and 45-minute blackout."""

    def test_headline_keywords(self) -> None:
        assert check_headline_shock("DeFi Protocol Suffers Major Hack, $50M Stolen") is True
        assert check_headline_shock("Lending Protocol Exploited by Flash Loan Attack") is True
        assert check_headline_shock("Exchange Faces Imminent Insolvency Rumors") is True
        assert check_headline_shock("SEC Sues Major Crypto Foundation Over Securities") is True
        assert check_headline_shock("Stablecoin Massive Depeg Hits $0.85 on Exchanges") is True

        # Benign headlines
        assert check_headline_shock("Bitcoin Breaks $70,000 as Inflows Surge") is False
        assert check_headline_shock("Ethereum Developers Plan Next Upgrade") is False

    def test_parse_rss_titles_from_xml(self) -> None:
        sample_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0">
            <channel>
                <title>Crypto News</title>
                <item><title>Bitcoin Rallies Past 70k</title></item>
                <item><title>Major Bridge Protocol Exploited for $10M</title></item>
            </channel>
        </rss>
        """
        titles = parse_rss_titles(sample_xml)
        assert len(titles) == 2
        assert "Bitcoin Rallies Past 70k" in titles
        assert "Major Bridge Protocol Exploited for $10M" in titles

    @pytest.mark.asyncio
    async def test_rss_scan_triggers_45_min_blackout(self) -> None:
        shock_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0">
            <channel>
                <item><title>Critical Exploit: Lending Protocol Drained</title></item>
            </channel>
        </rss>
        """
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read = AsyncMock(return_value=shock_xml)

        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.get.return_value = MockGetContext(mock_resp)

        engine = MacroAndNewsVetoEngine(session=mock_session)
        now = 5000.0

        triggered = await engine.scan_rss_feed("https://fake.url/rss", now_ts=now)
        assert triggered is True
        assert engine.shock_blackout_until == now + NEWS_SHOCK_BLACKOUT_S

        # Inside 45-minute (2700s) window -> blackout active
        assert engine.is_rss_shock_blackout(now_ts=now + 1000.0) is True
        assert engine.is_blackout(now_ts=now + 1000.0) is True

        # Outside 45-minute window -> blackout expired
        assert engine.is_rss_shock_blackout(now_ts=now + 2800.0) is False
        assert engine.is_blackout(now_ts=now + 2800.0) is False

    @pytest.mark.asyncio
    async def test_rss_scan_benign_no_blackout(self) -> None:
        benign_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0">
            <channel>
                <item><title>Bitcoin Holds Steady Above Support</title></item>
            </channel>
        </rss>
        """
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read = AsyncMock(return_value=benign_xml)

        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.get.return_value = MockGetContext(mock_resp)

        engine = MacroAndNewsVetoEngine(session=mock_session)
        now = 5000.0

        triggered = await engine.scan_rss_feed("https://fake.url/rss", now_ts=now)
        assert triggered is False
        assert engine.is_rss_shock_blackout(now_ts=now) is False
