"""Builds the Italian free-TV catalogue from the public iptv-org database.

Data source: https://github.com/iptv-org/api — a community-maintained index of
publicly available (free-to-air / FAST) channels. Only channels registered in
Italy that are still on air and not NSFW are kept, and pay-TV brands are
filtered out as an extra safety net.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field

import httpx

log = logging.getLogger("tvitalia")

API_BASE = "https://iptv-org.github.io/api"
COUNTRY = "IT"
ID_PREFIX = "itv:"

# Pay-TV brands never belong in a free-TV addon. Sky TG24 is the exception:
# it is free-to-air on the Italian DTT (LCN 50).
_PAY_TV = re.compile(r"\b(sky|dazn|eurosport|premium|infinity\+|nba tv)\b", re.I)
_PAY_TV_ALLOWED = {"skytg24"}

# Italian names for iptv-org categories; also the catalogue's genre filter.
GENRES = {
    "general": "Generalisti",
    "news": "Informazione",
    "sports": "Sport",
    "movies": "Cinema",
    "series": "Serie TV",
    "entertainment": "Intrattenimento",
    "kids": "Bambini",
    "animation": "Bambini",
    "documentary": "Documentari",
    "education": "Cultura",
    "culture": "Cultura",
    "music": "Musica",
    "lifestyle": "Lifestyle",
    "cooking": "Lifestyle",
    "travel": "Lifestyle",
    "outdoor": "Lifestyle",
    "shop": "Televendite",
    "religious": "Religione",
    "weather": "Informazione",
    "business": "Informazione",
    "legislative": "Informazione",
    "public": "Generalisti",
    "comedy": "Intrattenimento",
    "classic": "Cinema",
    "family": "Intrattenimento",
    "auto": "Lifestyle",
    "science": "Documentari",
    "relax": "Lifestyle",
}
OTHER_GENRE = "Locali e altri"
GENRE_ORDER = [
    "Generalisti", "Sport", "Informazione", "Cinema", "Serie TV",
    "Intrattenimento", "Bambini", "Documentari", "Cultura", "Musica",
    "Lifestyle", "Religione", "Televendite", OTHER_GENRE,
]

# Italian DTT channel numbers (LCN), used to sort the catalogue like a TV remote.
LCN = {
    "rai1": 1, "rai2": 2, "rai3": 3, "rete4": 4, "canale5": 5, "italia1": 6,
    "la7": 7, "tv8": 8, "nove": 9, "20mediaset": 20, "rai4": 21, "iris": 22,
    "rai5": 23, "raimovie": 24, "raipremium": 25, "cielo": 26,
    "twentyseven": 27, "tv2000": 28, "la7d": 29, "la5": 30, "realtime": 31,
    "qvc": 32, "foodnetwork": 33, "cine34": 34, "focus": 35, "rtl1025": 36,
    "warnertv": 37, "giallo": 38, "topcrime": 39, "boing": 40, "k2": 41,
    "raigulp": 42, "raiyoyo": 43, "frisbee": 44, "cartoonito": 46,
    "super": 47, "rainews24": 48, "italia2": 49, "skytg24": 50,
    "tgcom24": 51, "dmax": 52, "raistoria": 54, "mediasetextra": 55,
    "hgtv": 56, "raiscuola": 57, "raisport": 58, "motortrend": 59,
    "sportitalia": 60,
}

_QUALITY_RANK = {"2160p": 6, "1440p": 5, "1080p": 4, "720p": 3, "576p": 2, "480p": 1}


def normalize(name: str) -> str:
    """'Rai 1 HD' -> 'rai1', 'Super!' -> 'super'."""
    n = re.sub(r"\b(hd|fhd|uhd|4k|sd)\b", "", name.lower())
    return re.sub(r"[^a-z0-9]", "", n)


def is_pay_tv(name: str) -> bool:
    return bool(_PAY_TV.search(name)) and normalize(name) not in _PAY_TV_ALLOWED


@dataclass
class Stream:
    url: str
    quality: str | None = None
    title: str | None = None
    referrer: str | None = None
    user_agent: str | None = None
    is_main_feed: bool = True

    @property
    def rank(self) -> tuple[int, int]:
        return (int(self.is_main_feed), _QUALITY_RANK.get(self.quality or "", 0))


@dataclass
class Channel:
    id: str
    name: str
    genres: list[str]
    logo: str | None = None
    website: str | None = None
    lcn: int | None = None
    streams: list[Stream] = field(default_factory=list)

    @property
    def stremio_id(self) -> str:
        return ID_PREFIX + self.id

    @property
    def sort_key(self) -> tuple:
        return (self.lcn is None, self.lcn or 0, self.name.lower())


def build_channels(channels: list, streams: list, logos: list, feeds: list) -> dict[str, Channel]:
    """Pure function: raw iptv-org JSON lists -> {channel_id: Channel} with streams."""
    main_feeds = {(f["channel"], f["id"]) for f in feeds if f.get("is_main")}

    logo_by_channel: dict[str, tuple] = {}
    for lg in logos:
        if not lg.get("url"):
            continue
        # Prefer channel-level logos, then the largest one.
        score = (lg.get("feed") is None, lg.get("width") or 0)
        prev = logo_by_channel.get(lg["channel"])
        if prev is None or score > prev[0]:
            logo_by_channel[lg["channel"]] = (score, lg["url"])

    result: dict[str, Channel] = {}
    for ch in channels:
        if ch.get("country") != COUNTRY or ch.get("is_nsfw") or ch.get("closed"):
            continue
        if is_pay_tv(ch["name"]):
            continue
        genres: list[str] = []
        for cat in ch.get("categories") or []:
            g = GENRES.get(cat)
            if g and g not in genres:
                genres.append(g)
        logo = logo_by_channel.get(ch["id"], (None, ch.get("logo")))[1]
        result[ch["id"]] = Channel(
            id=ch["id"],
            name=ch["name"],
            genres=genres or [OTHER_GENRE],
            logo=logo,
            website=ch.get("website"),
            lcn=LCN.get(normalize(ch["name"])),
        )

    for s in streams:
        ch = result.get(s.get("channel") or "")
        if ch is None or not s.get("url"):
            continue
        feed = s.get("feed")
        ch.streams.append(Stream(
            url=s["url"],
            quality=s.get("quality"),
            title=s.get("title"),
            referrer=s.get("referrer"),
            user_agent=s.get("user_agent"),
            is_main_feed=feed is None or (ch.id, feed) in main_feeds,
        ))

    # A channel without a working source is useless in Stremio.
    result = {cid: ch for cid, ch in result.items() if ch.streams}
    for ch in result.values():
        ch.streams.sort(key=lambda s: s.rank, reverse=True)
    return result


class Catalog:
    """In-memory catalogue refreshed from iptv-org every `ttl` seconds.

    On a failed refresh the previous data is kept, so a temporary outage of
    the upstream API never empties the addon.
    """

    def __init__(self, ttl: int = 6 * 3600, api_base: str = API_BASE):
        self.ttl = ttl
        self.api_base = api_base
        self.channels: dict[str, Channel] = {}
        self.updated_at = 0.0
        self._lock = asyncio.Lock()

    @property
    def stale(self) -> bool:
        return time.time() - self.updated_at > self.ttl

    async def _fetch(self, client: httpx.AsyncClient, name: str) -> list:
        r = await client.get(f"{self.api_base}/{name}.json")
        r.raise_for_status()
        return r.json()

    async def refresh(self, force: bool = False) -> None:
        async with self._lock:
            if not force and self.channels and not self.stale:
                return
            try:
                async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                    data = await asyncio.gather(*(
                        self._fetch(client, n) for n in ("channels", "streams", "logos", "feeds")
                    ))
                self.channels = build_channels(*data)
                self.updated_at = time.time()
                log.info("Catalogue updated: %d channels", len(self.channels))
            except Exception:
                log.exception("Catalogue refresh failed; keeping %d cached channels", len(self.channels))
                # Retry in 5 minutes rather than on every request.
                self.updated_at = time.time() - self.ttl + 300

    async def ensure_fresh(self) -> None:
        if not self.channels:
            await self.refresh()
        elif self.stale and not self._lock.locked():
            asyncio.create_task(self.refresh())

    def sorted_channels(self) -> list[Channel]:
        return sorted(self.channels.values(), key=lambda c: c.sort_key)

    def genres(self) -> list[str]:
        present = {g for ch in self.channels.values() for g in ch.genres}
        return [g for g in GENRE_ORDER if g in present] or GENRE_ORDER
