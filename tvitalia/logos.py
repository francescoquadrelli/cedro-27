"""Square channel tiles: Stremio crops posters to fill the card, so wide logos
lose their edges. Each logo is trimmed, centred with a margin on a square
canvas, and given a light or dark background depending on its colours."""
from __future__ import annotations

import io
import logging

import httpx
from PIL import Image, ImageDraw, ImageFont, ImageOps

log = logging.getLogger("tvitalia")

SIZE = 512
MARGIN = 0.14          # free space on each side
LIGHT_BG = (244, 244, 246)
DARK_BG = (30, 30, 36)
_MAX_CACHE = 600


def _luminance(img: Image.Image) -> float | None:
    """Mean luminance (0..1) of the visible pixels, or None if fully transparent."""
    px = img.resize((64, 64)).tobytes()
    total = weight = 0.0
    for i in range(0, len(px), 4):
        r, g, b, a = px[i], px[i + 1], px[i + 2], px[i + 3]
        if a < 32:
            continue
        total += (0.2126 * r + 0.7152 * g + 0.0722 * b) * a
        weight += a
    return total / weight / 255 if weight else None


def _is_opaque(img: Image.Image) -> bool:
    return img.getchannel("A").getextrema()[0] > 250


def render_tile(data: bytes) -> bytes:
    img = Image.open(io.BytesIO(data))
    img = ImageOps.exif_transpose(img).convert("RGBA")
    opaque = _is_opaque(img)
    if bbox := img.getchannel("A").getbbox():
        img = img.crop(bbox)
    lum = _luminance(img)
    if opaque:
        # Logo with its own background: extend that colour (corner pixel).
        bg = img.getpixel((0, 0))[:3]
    else:
        bg = DARK_BG if lum is not None and lum > 0.62 else LIGHT_BG
    box = int(SIZE * (1 - 2 * MARGIN))
    img.thumbnail((box, box), Image.LANCZOS)
    if max(img.size) < box:  # upscale tiny logos so they are not a dot
        scale = box / max(img.size)
        img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))), Image.LANCZOS)
    canvas = Image.new("RGBA", (SIZE, SIZE), bg + (255,))
    canvas.alpha_composite(img, ((SIZE - img.width) // 2, (SIZE - img.height) // 2))
    out = io.BytesIO()
    canvas.convert("RGB").save(out, "PNG", optimize=True)
    return out.getvalue()


def placeholder_tile(name: str) -> bytes:
    canvas = Image.new("RGB", (SIZE, SIZE), DARK_BG)
    draw = ImageDraw.Draw(canvas)
    words = name.split()
    text = name if len(name) <= 10 else "\n".join(
        " ".join(words[i:i + 2]) for i in range(0, len(words), 2))
    font_size = 96 if len(name) <= 6 else 64
    try:
        font = ImageFont.load_default(size=font_size)
    except TypeError:  # Pillow < 10.1
        font = ImageFont.load_default()
    draw.multiline_text((SIZE / 2, SIZE / 2), text, fill=(240, 240, 240), font=font,
                        anchor="mm", align="center", spacing=12)
    out = io.BytesIO()
    canvas.save(out, "PNG", optimize=True)
    return out.getvalue()


class TileCache:
    def __init__(self) -> None:
        self._cache: dict[str, bytes] = {}

    async def get(self, key: str, logo_url: str | None, name: str) -> bytes:
        if key in self._cache:
            return self._cache[key]
        tile = None
        if logo_url:
            try:
                async with httpx.AsyncClient(timeout=15, follow_redirects=True,
                                             headers={"User-Agent": "Mozilla/5.0"}) as client:
                    r = await client.get(logo_url)
                    r.raise_for_status()
                tile = render_tile(r.content)
            except Exception as exc:  # unreachable, SVG or broken image
                log.info("Logo for %s not usable (%s): using placeholder", name, exc)
        tile = tile or placeholder_tile(name)
        if len(self._cache) >= _MAX_CACHE:
            self._cache.pop(next(iter(self._cache)))
        self._cache[key] = tile
        return tile
