"""Checks each stream: is it reachable, and what resolution does it really offer?

iptv-org quality labels are often missing or wrong, so the HLS playlist is read
to find the highest RESOLUTION it advertises. Results drive stream ranking and
let the addon hide sources that are clearly dead.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass

import httpx

log = logging.getLogger("tvitalia")

_RES = re.compile(r"RESOLUTION=\d+x(\d+)", re.I)
_BW = re.compile(r"[^-]BANDWIDTH=(\d+)", re.I)
_MAX_BYTES = 256 * 1024
# Statuses that mean "gone", not "not allowed from this server" (geo-blocks give 403/451).
_DEAD_STATUS = {404, 410}


@dataclass
class ProbeResult:
    alive: bool | None        # True ok, False dead, None unknown (timeout, geo-block…)
    height: int | None = None
    bandwidth: int | None = None
    latency: float | None = None


def parse_playlist(text: str) -> tuple[int | None, int | None, bool]:
    """-> (max height, max bandwidth, looks like HLS)."""
    heights = [int(h) for h in _RES.findall(text)]
    bws = [int(b) for b in _BW.findall(" " + text)]
    is_hls = "#EXTM3U" in text
    return (max(heights) if heights else None, max(bws) if bws else None, is_hls)


async def probe_one(client: httpx.AsyncClient, url: str, headers: dict) -> ProbeResult:
    start = time.monotonic()
    try:
        async with client.stream("GET", url, headers=headers) as r:
            if r.status_code in _DEAD_STATUS:
                return ProbeResult(alive=False)
            if r.status_code >= 400:
                return ProbeResult(alive=None)
            body = b""
            async for chunk in r.aiter_bytes():
                body += chunk
                if len(body) >= _MAX_BYTES:
                    break
            latency = time.monotonic() - start
    except (httpx.ConnectError, httpx.UnsupportedProtocol, httpx.InvalidURL):
        return ProbeResult(alive=False)
    except Exception:
        return ProbeResult(alive=None)
    ctype = r.headers.get("content-type", "")
    if "mpegurl" in ctype or body.lstrip().startswith(b"#EXTM3U"):
        height, bw, _ = parse_playlist(body.decode("utf-8", "replace"))
        return ProbeResult(alive=True, height=height, bandwidth=bw, latency=latency)
    if "text/html" in ctype:
        return ProbeResult(alive=None, latency=latency)  # error page / landing page
    return ProbeResult(alive=True, latency=latency)


async def probe_all(streams: list, concurrency: int = 24, timeout: float = 10) -> dict[str, ProbeResult]:
    """Probe the given Stream objects; returns {url: ProbeResult}."""
    sem = asyncio.Semaphore(concurrency)
    results: dict[str, ProbeResult] = {}
    limits = httpx.Limits(max_connections=concurrency)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, limits=limits) as client:
        async def run(s) -> None:
            headers = {"User-Agent": s.user_agent or "Mozilla/5.0"}
            if s.referrer:
                headers["Referer"] = s.referrer
            async with sem:
                results[s.url] = await probe_one(client, s.url, headers)

        await asyncio.gather(*(run(s) for s in streams))
    ok = sum(r.alive is True for r in results.values())
    dead = sum(r.alive is False for r in results.values())
    log.info("Probed %d streams: %d ok, %d dead, %d unknown", len(results), ok, dead, len(results) - ok - dead)
    return results
