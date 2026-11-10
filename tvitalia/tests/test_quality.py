import io
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from catalog import Channel, Stream, apply_probes  # noqa: E402
from logos import DARK_BG, LIGHT_BG, SIZE, placeholder_tile, render_tile  # noqa: E402
from probe import ProbeResult, parse_playlist  # noqa: E402


def _png(img: Image.Image) -> bytes:
    b = io.BytesIO()
    img.save(b, "PNG")
    return b.getvalue()


def test_wide_logo_becomes_padded_square():
    logo = Image.new("RGBA", (800, 200), (0, 0, 0, 0))
    logo.paste((20, 20, 20, 255), (100, 50, 700, 150))  # dark mark with transparent border
    tile = Image.open(io.BytesIO(render_tile(_png(logo)))).convert("RGB")
    assert tile.size == (SIZE, SIZE)
    assert tile.getpixel((5, 5)) == LIGHT_BG          # dark logo -> light background
    assert tile.getpixel((5, SIZE // 2)) == LIGHT_BG  # side margin kept: nothing cropped
    assert tile.getpixel((SIZE // 2, SIZE // 2)) == (20, 20, 20)


def test_light_logo_gets_dark_background():
    logo = Image.new("RGBA", (300, 300), (0, 0, 0, 0))
    logo.paste((250, 250, 250, 255), (50, 50, 250, 250))
    tile = Image.open(io.BytesIO(render_tile(_png(logo)))).convert("RGB")
    assert tile.getpixel((3, 3)) == DARK_BG


def test_placeholder():
    assert Image.open(io.BytesIO(placeholder_tile("Tele Locale"))).size == (SIZE, SIZE)


def test_parse_playlist():
    master = ("#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360\nlow.m3u8\n"
              "#EXT-X-STREAM-INF:AVERAGE-BANDWIDTH=4000000,BANDWIDTH=5000000,RESOLUTION=1920x1080\nhi.m3u8\n")
    assert parse_playlist(master) == (1080, 5000000, True)
    assert parse_playlist("#EXTM3U\n#EXTINF:6,\nseg.ts\n") == (None, None, True)


def _ch(*streams):
    return {"c": Channel(id="c", name="C", genres=["Sport"], streams=list(streams))}


def test_apply_probes_ranks_real_quality_and_drops_dead_and_poor():
    official_sd = Stream(url="https://rai.it/a", quality="576p", official=True)
    alt_hd = Stream(url="https://x.test/hd")
    alt_low = Stream(url="https://x.test/low", quality="360p")
    dead = Stream(url="https://x.test/dead", quality="1080p")
    res = {
        "https://rai.it/a": ProbeResult(alive=True, height=1080, latency=0.2),
        "https://x.test/hd": ProbeResult(alive=True, height=720, latency=0.5),
        "https://x.test/low": ProbeResult(alive=True, height=360),
        "https://x.test/dead": ProbeResult(alive=False),
    }
    out = apply_probes(_ch(alt_low, dead, alt_hd, official_sd), res)
    urls = [s.url for s in out["c"].streams]
    assert urls == ["https://rai.it/a", "https://x.test/hd"]
    assert out["c"].streams[0].label == "Full HD"


def test_apply_probes_keeps_unknown_and_hides_all_dead_channel():
    geo = Stream(url="https://rai.it/geo", official=True)
    assert apply_probes(_ch(geo), {"https://rai.it/geo": ProbeResult(alive=None)})["c"].streams == [geo]
    off = Stream(url="https://rai.it/404", official=True)
    assert apply_probes(_ch(off), {"https://rai.it/404": ProbeResult(alive=False)})["c"].streams == [off]
    gone = Stream(url="https://x.test/gone")
    assert apply_probes(_ch(gone), {"https://x.test/gone": ProbeResult(alive=False)}) == {}
