"""
engine/news_engine.py — Macroeconomic Calendar & News Shock Veto Engine.

Implements:
1. ForexFactory Macro Calendar Blackout:
   - Fetches high-impact USD economic events.
   - Caches to disk (12-hour TTL) to prevent rate limits.
   - Enforces a ±15-minute (±900s) blackout window around event times.
2. Real-Time Crypto RSS Headline Shock Detector:
   - Scans CoinDesk and Cointelegraph RSS feeds.
   - Parses top 10 items using standard library xml.etree.ElementTree (zero heavy NLP).
   - Searches for blacklisted shock keywords ('hack', 'exploited', 'insolvency', 'sec sues', 'depeg').
   - Triggers a mandatory 45-minute (2700s) blackout upon shock detection.
   - Clears XML tree elements immediately after parsing to conserve memory.

Strict Constraints:
- Zero heavy NLP libraries (no spacy, transformers, beautifulsoup4).
- __slots__ on all classes.
"""

import os
import tempfile
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any

import aiohttp
import orjson

# ─────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────

FOREX_FACTORY_URL: str = (
    "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
)
COINDESK_RSS_URL: str = "https://www.coindesk.com/arc/outboundfeeds/rss/"
COINTELEGRAPH_RSS_URL: str = "https://cointelegraph.com/rss"

DEFAULT_FF_CACHE_PATH: str = os.environ.get(
    "FF_CACHE_PATH",
    os.path.join(tempfile.gettempdir(), "ff_calendar_cache.json"),
)

FF_CACHE_TTL_SECONDS: float = 43200.0     # 12 hours
MACRO_BLACKOUT_WINDOW_S: float = 900.0    # ±15 minutes (900s)
NEWS_SHOCK_BLACKOUT_S: float = 2700.0     # 45 minutes (2700s)

SHOCK_KEYWORDS: tuple[str, ...] = (
    "hack",
    "exploited",
    "exploit",
    "insolvency",
    "sec sues",
    "depeg",
)


# ─────────────────────────────────────────────────────────────────────
# Pure Helper Functions
# ─────────────────────────────────────────────────────────────────────

def parse_iso_datetime(date_str: str) -> float:
    """Parse ISO8601 date string to UNIX timestamp in seconds."""
    try:
        dt = datetime.fromisoformat(date_str)
        # If naive, treat as UTC
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (ValueError, TypeError):
        return 0.0


def check_headline_shock(title: str, keywords: tuple[str, ...] = SHOCK_KEYWORDS) -> bool:
    """Return True if title contains any high-severity shock keyword."""
    lower_title = title.lower()
    return any(kw in lower_title for kw in keywords)


def parse_rss_titles(xml_bytes: bytes, max_items: int = 10) -> list[str]:
    """Parse top N titles from RSS XML and explicitly clear root element."""
    titles: list[str] = []
    try:
        root = ET.fromstring(xml_bytes)
        items = root.findall(".//item")
        for item in items[:max_items]:
            title_node = item.find("title")
            if title_node is not None and title_node.text:
                titles.append(title_node.text.strip())
        # Clean up memory immediately
        root.clear()
    except Exception:
        pass
    return titles


# ─────────────────────────────────────────────────────────────────────
# Macro & News Veto Engine Class
# ─────────────────────────────────────────────────────────────────────

class MacroAndNewsVetoEngine:
    """Monitors ForexFactory macro events and crypto RSS news shocks.

    Attributes
    ----------
    cache_path : str
        Disk path where ForexFactory calendar is cached.
    usd_event_timestamps : list[float]
        UNIX timestamps of parsed High-impact USD events.
    shock_blackout_until : float
        UNIX timestamp until which RSS shock blackout is active.
    """

    __slots__ = (
        "cache_path",
        "usd_event_timestamps",
        "shock_blackout_until",
        "_session",
        "_own_session",
    )

    def __init__(
        self,
        cache_path: str = DEFAULT_FF_CACHE_PATH,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self.cache_path: str = cache_path
        self.usd_event_timestamps: list[float] = []
        self.shock_blackout_until: float = 0.0
        self._session: aiohttp.ClientSession | None = session
        self._own_session: bool = session is None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or (isinstance(self._session.closed, bool) and self._session.closed):
            self._session = aiohttp.ClientSession()
            self._own_session = True
        return self._session

    async def close(self) -> None:
        """Close internally managed aiohttp session."""
        if self._own_session and self._session is not None and not self._session.closed:
            await self._session.close()

    # ── 1. ForexFactory Macro Engine ────────────────────────────────

    def load_cache_from_disk(self, now_ts: float) -> bool:
        """Load cached calendar from disk if present and younger than 12h."""
        if not os.path.exists(self.cache_path):
            return False

        try:
            mtime = os.path.getmtime(self.cache_path)
            if now_ts - mtime > FF_CACHE_TTL_SECONDS:
                return False

            with open(self.cache_path, "rb") as f:
                data = orjson.loads(f.read())
            self._parse_calendar_data(data)
            return True
        except Exception:
            return False

    def save_cache_to_disk(self, data: Any) -> None:
        """Save calendar payload to disk atomically/safely."""
        try:
            parent = os.path.dirname(self.cache_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(self.cache_path, "wb") as f:
                f.write(orjson.dumps(data))
        except Exception:
            pass

    def _parse_calendar_data(self, data: Any) -> None:
        """Filter for country == 'USD' and impact == 'High' events."""
        self.usd_event_timestamps.clear()
        if not isinstance(data, list):
            return

        for item in data:
            if not isinstance(item, dict):
                continue
            country = item.get("country", "")
            impact = item.get("impact", "")
            if country == "USD" and impact == "High":
                raw_date = item.get("date", "")
                ts = parse_iso_datetime(raw_date)
                if ts > 0.0:
                    self.usd_event_timestamps.append(ts)

    async def refresh_macro_calendar(self, now_ts: float | None = None) -> None:
        """Refresh ForexFactory calendar, obeying the 12-hour disk cache."""
        now = time.time() if now_ts is None else now_ts

        # 1. Check disk cache
        if self.load_cache_from_disk(now):
            return

        # 2. Fetch remote if cache missed or expired
        try:
            session = await self._get_session()
            async with session.get(FOREX_FACTORY_URL) as resp:
                if resp.status == 200:
                    raw_bytes = await resp.read()
                    data = orjson.loads(raw_bytes)
                    self._parse_calendar_data(data)
                    self.save_cache_to_disk(data)
        except Exception:
            pass

    def is_macro_blackout(self, now_ts: float | None = None) -> bool:
        """Return True if now_ts is within ±15 minutes (900s) of high-impact USD news."""
        now = time.time() if now_ts is None else now_ts
        for event_ts in self.usd_event_timestamps:
            if event_ts - MACRO_BLACKOUT_WINDOW_S <= now <= event_ts + MACRO_BLACKOUT_WINDOW_S:
                return True
        return False

    # ── 2. RSS Shock Scanner ────────────────────────────────────────

    async def scan_rss_feed(self, url: str, now_ts: float | None = None) -> bool:
        """Scan RSS feed XML for high-severity shock keywords in top 10 items.

        Triggers a 45-minute blackout if any keyword is detected.
        """
        now = time.time() if now_ts is None else now_ts
        try:
            session = await self._get_session()
            async with session.get(url) as resp:
                if resp.status == 200:
                    xml_bytes = await resp.read()
                    titles = parse_rss_titles(xml_bytes, max_items=10)
                    for title in titles:
                        if check_headline_shock(title):
                            self.shock_blackout_until = max(
                                self.shock_blackout_until,
                                now + NEWS_SHOCK_BLACKOUT_S,
                            )
                            return True
        except Exception:
            pass
        return False

    async def scan_all_rss_feeds(self, now_ts: float | None = None) -> bool:
        """Scan both CoinDesk and Cointelegraph RSS feeds."""
        now = time.time() if now_ts is None else now_ts
        triggered = False
        for feed_url in (COINDESK_RSS_URL, COINTELEGRAPH_RSS_URL):
            if await self.scan_rss_feed(feed_url, now):
                triggered = True
        return triggered

    def is_rss_shock_blackout(self, now_ts: float | None = None) -> bool:
        """Return True if current time is within active 45-minute shock blackout."""
        now = time.time() if now_ts is None else now_ts
        return now < self.shock_blackout_until

    # ── 3. Unified Veto Query ───────────────────────────────────────

    def is_blackout(self, now_ts: float | None = None) -> bool:
        """Return True if either macro calendar or RSS news shock vetoes trading."""
        now = time.time() if now_ts is None else now_ts
        return self.is_macro_blackout(now) or self.is_rss_shock_blackout(now)

    def __repr__(self) -> str:
        return (
            f"MacroAndNewsVetoEngine(usd_events={len(self.usd_event_timestamps)}, "
            f"shock_blackout_until={self.shock_blackout_until})"
        )
