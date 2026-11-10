import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as addon  # noqa: E402
from catalog import build_channels, is_pay_tv, normalize  # noqa: E402

CHANNELS = [
    {"id": "Rai1.it", "name": "Rai 1", "country": "IT", "categories": ["general"], "is_nsfw": False, "closed": None},
    {"id": "Canale5.it", "name": "Canale 5", "country": "IT", "categories": ["entertainment"], "is_nsfw": False},
    {"id": "RaiSport.it", "name": "Rai Sport", "country": "IT", "categories": ["sports"], "is_nsfw": False},
    {"id": "SkyTG24.it", "name": "Sky TG24", "country": "IT", "categories": ["news"], "is_nsfw": False},
    {"id": "SkySportUno.it", "name": "Sky Sport Uno", "country": "IT", "categories": ["sports"], "is_nsfw": False},
    {"id": "DAZN1.it", "name": "DAZN 1", "country": "IT", "categories": ["sports"], "is_nsfw": False},
    {"id": "Adult.it", "name": "Adult", "country": "IT", "categories": [], "is_nsfw": True},
    {"id": "Old.it", "name": "Old TV", "country": "IT", "categories": [], "is_nsfw": False, "closed": "2020-01-01"},
    {"id": "TF1.fr", "name": "TF1", "country": "FR", "categories": ["general"], "is_nsfw": False},
    {"id": "Local.it", "name": "Tele Locale", "country": "IT", "categories": [], "is_nsfw": False},
    {"id": "NoStream.it", "name": "No Stream", "country": "IT", "categories": [], "is_nsfw": False},
]
STREAMS = [
    {"channel": "Rai1.it", "feed": None, "url": "https://example.test/rai1-720.m3u8", "quality": "720p"},
    {"channel": "Rai1.it", "feed": None, "url": "https://example.test/rai1-1080.m3u8", "quality": "1080p"},
    {"channel": "Canale5.it", "feed": "SD", "url": "https://example.test/c5.m3u8", "quality": "576p",
     "referrer": "https://example.test/", "user_agent": "UA"},
    {"channel": "RaiSport.it", "url": "https://example.test/raisport.m3u8"},
    {"channel": "SkyTG24.it", "url": "https://example.test/tg24.m3u8"},
    {"channel": "SkySportUno.it", "url": "https://example.test/x.m3u8"},
    {"channel": "DAZN1.it", "url": "https://example.test/y.m3u8"},
    {"channel": "Adult.it", "url": "https://example.test/z.m3u8"},
    {"channel": "Old.it", "url": "https://example.test/o.m3u8"},
    {"channel": "TF1.fr", "url": "https://example.test/tf1.m3u8"},
    {"channel": "Local.it", "url": "https://example.test/local.m3u8"},
]
LOGOS = [
    {"channel": "Rai1.it", "feed": None, "width": 100, "url": "https://example.test/small.png"},
    {"channel": "Rai1.it", "feed": None, "width": 512, "url": "https://example.test/big.png"},
]
FEEDS = [{"channel": "Canale5.it", "id": "SD", "is_main": True}]


@pytest.fixture
def client(monkeypatch):
    addon.catalog.channels = build_channels(CHANNELS, STREAMS, LOGOS, FEEDS)
    addon.catalog.updated_at = 1e12  # never stale

    async def no_refresh(*a, **k):
        return None

    monkeypatch.setattr(addon.catalog, "refresh", no_refresh)
    with TestClient(addon.app) as c:
        yield c


def test_helpers():
    assert normalize("Rai 1 HD") == "rai1"
    assert normalize("Super!") == "super"
    assert is_pay_tv("Sky Sport Uno") and is_pay_tv("DAZN 1") and is_pay_tv("Eurosport 1")
    assert not is_pay_tv("Sky TG24") and not is_pay_tv("Rai Sport")


def test_filtering():
    chans = build_channels(CHANNELS, STREAMS, LOGOS, FEEDS)
    assert set(chans) == {"Rai1.it", "Canale5.it", "RaiSport.it", "SkyTG24.it", "Local.it"}
    assert chans["Rai1.it"].streams[0].quality == "1080p"
    assert chans["Rai1.it"].logo == "https://example.test/big.png"
    assert chans["Local.it"].genres == ["Locali e altri"]


def test_manifest(client):
    m = client.get("/manifest.json").json()
    assert m["types"] == ["tv"] and m["catalogs"][0]["id"] == "tvitalia"
    r = client.get("/manifest.json", headers={"Origin": "https://web.stremio.com"})
    assert r.headers["access-control-allow-origin"] == "*"


def test_catalog_sorted_by_lcn(client):
    names = [m["name"] for m in client.get("/catalog/tv/tvitalia.json").json()["metas"]]
    assert names == ["Rai 1", "Canale 5", "Sky TG24", "Rai Sport", "Tele Locale"]


def test_catalog_genre_search_skip(client):
    r = client.get("/catalog/tv/tvitalia/genre=Sport.json").json()
    assert [m["name"] for m in r["metas"]] == ["Rai Sport"]
    r = client.get("/catalog/tv/tvitalia/search=rai.json").json()
    assert [m["name"] for m in r["metas"]] == ["Rai 1", "Rai Sport"]
    r = client.get("/catalog/tv/tvitalia/skip=4.json").json()
    assert [m["name"] for m in r["metas"]] == ["Tele Locale"]
    assert client.get("/catalog/tv/other.json").status_code == 404


def test_meta_and_streams(client):
    meta = client.get("/meta/tv/itv:Rai1.it.json").json()["meta"]
    assert meta["name"] == "Rai 1" and "Canale 1" in meta["description"]
    streams = client.get("/stream/tv/itv:Rai1.it.json").json()["streams"]
    assert [s["url"] for s in streams][0].endswith("rai1-1080.m3u8")
    c5 = client.get("/stream/tv/itv:Canale5.it.json").json()["streams"][0]
    assert c5["behaviorHints"]["proxyHeaders"]["request"] == {"Referer": "https://example.test/", "User-Agent": "UA"}
    assert client.get("/stream/tv/itv:DAZN1.it.json").status_code == 404
    assert client.get("/stream/tv/tt123.json").status_code == 404


def test_home_and_health(client):
    assert "Installa su Stremio" in client.get("/").text
    assert client.get("/health").json()["channels"] == 5
