"""TV Italia — Stremio addon for free Italian TV (free-to-air and FAST channels)."""
from __future__ import annotations

import asyncio
import html
import logging
import os
from contextlib import asynccontextmanager
from urllib.parse import parse_qsl

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

import hashlib

from fastapi.responses import Response

from catalog import GENRE_ORDER, ID_PREFIX, Catalog, Channel
from logos import TileCache

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")

PAGE_SIZE = 100
CATALOG_ID = "tvitalia"
ADDON_NAME = os.getenv("ADDON_NAME", "TV Italia")

MANIFEST = {
    "id": "org.cedro27.tvitalia",
    "version": "1.1.0",
    "name": ADDON_NAME,
    "description": "Tutta la TV italiana gratuita in diretta: canali nazionali, Rai, Mediaset, "
                   "sport, news, bambini e canali FAST. Solo fonti pubbliche e gratuite (iptv-org).",
    "logo": "https://upload.wikimedia.org/wikipedia/commons/thumb/0/03/Flag_of_Italy.svg/512px-Flag_of_Italy.svg.png",
    "types": ["tv"],
    "idPrefixes": [ID_PREFIX],
    "resources": ["catalog", "meta", "stream"],
    "catalogs": [{
        "type": "tv",
        "id": CATALOG_ID,
        "name": "TV Italia in diretta",
        "extra": [
            {"name": "genre", "options": GENRE_ORDER, "isRequired": False},
            {"name": "search", "isRequired": False},
            {"name": "skip", "isRequired": False},
        ],
    }],
    "behaviorHints": {"configurable": False, "configurationRequired": False},
}

tiles = TileCache()
# Render sets RENDER_EXTERNAL_URL; locally the request URL is used.
PUBLIC_URL = (os.getenv("PUBLIC_URL") or os.getenv("RENDER_EXTERNAL_URL") or "").rstrip("/")
catalog = Catalog(ttl=int(os.getenv("REFRESH_HOURS", "6")) * 3600)


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Load in the background so the health check passes immediately on cold start.
    task = asyncio.create_task(catalog.refresh())
    yield
    task.cancel()


app = FastAPI(title=ADDON_NAME, docs_url=None, redoc_url=None, lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])


def respond(data: dict, max_age: int = 3600) -> JSONResponse:
    return JSONResponse(data, headers={"Cache-Control": f"public, max-age={max_age}"})


def base_url(request: Request) -> str:
    return PUBLIC_URL or str(request.base_url).rstrip("/")


def tile_url(ch: Channel, base: str) -> str:
    version = hashlib.sha1((ch.logo or ch.name).encode()).hexdigest()[:8]
    return f"{base}/logo/{ch.id}.png?v={version}"


def meta_preview(ch: Channel, base: str) -> dict:
    return {
        "id": ch.stremio_id,
        "type": "tv",
        "name": ch.name,
        "poster": tile_url(ch, base),
        "posterShape": "square",
        "logo": ch.logo,
        "genres": ch.genres,
    }


def meta_full(ch: Channel, base: str) -> dict:
    details = [f"Canale {ch.lcn} del digitale terrestre" if ch.lcn else None, " · ".join(ch.genres)]
    meta = meta_preview(ch, base) | {
        "description": " — ".join(d for d in details if d),
        "website": ch.website,
    }
    return {k: v for k, v in meta.items() if v}


def stream_entry(ch: Channel, idx: int) -> dict:
    s = ch.streams[idx]
    origin = "Ufficiale" if s.official else "Sorgente alternativa"
    checked = "✓ verificata" if s.alive else None
    label = " · ".join(p for p in (origin, s.label, checked) if p)
    entry: dict = {"name": f"{ADDON_NAME}\n{s.label or 'Live'}", "title": f"{ch.name}\n{label}", "url": s.url}
    headers = {k: v for k, v in (("Referer", s.referrer), ("User-Agent", s.user_agent)) if v}
    if headers:
        # Custom headers need the native player; web players cannot set them.
        entry["behaviorHints"] = {"notWebReady": True, "proxyHeaders": {"request": headers}}
    return entry


def parse_extra(extra: str | None) -> dict:
    return dict(parse_qsl(extra or "", keep_blank_values=False))


@app.get("/", response_class=HTMLResponse)
async def home(request: Request) -> str:
    base = str(request.base_url).rstrip("/")
    manifest_url = f"{base}/manifest.json"
    stremio_url = "stremio://" + manifest_url.split("://", 1)[1]
    n = len(catalog.channels)
    return f"""<!doctype html><html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(ADDON_NAME)}</title>
<style>body{{font-family:system-ui,sans-serif;max-width:560px;margin:48px auto;padding:0 16px;line-height:1.5}}
a.btn{{display:inline-block;background:#7b5bf5;color:#fff;padding:12px 20px;border-radius:8px;text-decoration:none}}
code{{background:#8882;padding:2px 6px;border-radius:4px;word-break:break-all}}</style></head><body>
<h1>📺 {html.escape(ADDON_NAME)}</h1>
<p>Tutta la TV italiana gratuita in diretta su Stremio. Canali disponibili ora: <b>{n}</b>.</p>
<p><a class="btn" href="{html.escape(stremio_url)}">Installa su Stremio</a></p>
<p>Oppure incolla in Stremio → Addon → cerca: <code>{html.escape(manifest_url)}</code></p>
</body></html>"""


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "channels": len(catalog.channels), "updated_at": catalog.updated_at,
            "streams_checked_at": catalog.probed_at}


@app.get("/manifest.json")
async def manifest() -> JSONResponse:
    return respond(MANIFEST, max_age=86400)


@app.get("/logo/{channel_id}.png")
async def logo(channel_id: str) -> Response:
    await catalog.ensure_fresh()
    ch = catalog.channels.get(channel_id)
    if ch is None:
        raise HTTPException(404)
    png = await tiles.get(ch.logo or ch.name, ch.logo, ch.name)
    return Response(png, media_type="image/png", headers={"Cache-Control": "public, max-age=604800"})


@app.get("/catalog/{type}/{id}.json")
@app.get("/catalog/{type}/{id}/{extra}.json")
async def catalog_route(request: Request, type: str, id: str, extra: str | None = None) -> JSONResponse:
    if type != "tv" or id != CATALOG_ID:
        raise HTTPException(404)
    await catalog.ensure_fresh()
    params = parse_extra(extra)
    items = catalog.sorted_channels()
    if genre := params.get("genre"):
        items = [c for c in items if genre in c.genres]
    if query := params.get("search", "").strip().lower():
        items = [c for c in items if query in c.name.lower()]
    try:
        skip = max(int(params.get("skip", 0)), 0)
    except ValueError:
        skip = 0
    page = items[skip: skip + PAGE_SIZE]
    base = base_url(request)
    return respond({"metas": [meta_preview(c, base) for c in page]}, max_age=1800)


@app.get("/meta/{type}/{id}.json")
async def meta_route(request: Request, type: str, id: str) -> JSONResponse:
    ch = await _channel(type, id)
    return respond({"meta": meta_full(ch, base_url(request))})


@app.get("/stream/{type}/{id}.json")
async def stream_route(type: str, id: str) -> JSONResponse:
    ch = await _channel(type, id)
    return respond({"streams": [stream_entry(ch, i) for i in range(len(ch.streams))]}, max_age=600)


async def _channel(type: str, id: str) -> Channel:
    if type != "tv" or not id.startswith(ID_PREFIX):
        raise HTTPException(404)
    await catalog.ensure_fresh()
    ch = catalog.channels.get(id[len(ID_PREFIX):])
    if ch is None:
        raise HTTPException(404)
    return ch


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=os.getenv("HOST", "0.0.0.0"), port=int(os.getenv("PORT", "8080")),
                proxy_headers=True, forwarded_allow_ips="*")
