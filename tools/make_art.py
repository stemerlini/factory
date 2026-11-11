"""Factory's pixel art: a sawtooth-roofed factory at dusk, and a pixel-font banner.

    python tools/make_art.py assets      # writes icon.svg and banner.svg
    rsvg-convert -w 256 assets/icon.svg -o assets/icon-256.png   (etc.)

Same family as Pathfinder and Citadel: a night scene, hard outlines and drop shadows,
drawn on a pixel grid with no fonts (the 5 × 7 letters are below).
"""

import random
import sys
from pathlib import Path

INK = "#141414"
SKY_TOP = "#12091f"
SKY_MID = "#2b1245"
SKY_LOW = "#5a1f5e"
GROUND = "#0d0a14"
STEEL = "#8FA3BF"
STEEL_LIGHT = "#D7E0EC"
STEEL_DARK = "#5E7290"
BRICK = "#C8472B"
BRICK_DARK = "#8E2F1D"
ORANGE = "#FF7A1A"
HOT = "#FFD23F"
PINK = "#FF4F8B"
SMOKE = "#B9B4C8"
SMOKE_DARK = "#8C86A3"
CREAM = "#FFF6E3"

def build_factory():
    """Returns {(x, y): colour}: two striped chimneys behind a sawtooth roof whose teeth
    glow, a row of lit windows and a door, with smoke rising."""
    px = {}
    top, base, teeth, tw = 13, 27, 4, 8
    width = teeth * tw

    for cx in (3, 11):  # chimneys: brick with a shaded edge, a steel rim, light bands
        for y in range(4, base):
            for dx in range(4):
                px[(cx + dx, y)] = BRICK_DARK if dx == 3 else BRICK
            if y % 5 == 0:
                for dx in range(4):
                    px[(cx + dx, y)] = STEEL_LIGHT if dx < 3 else STEEL
        for x in range(cx - 1, cx + 5):
            px[(x, 3)] = STEEL
            px[(x, 4)] = STEEL_DARK
    puffs = (
        (4, 1, 2), (5, 0, 2), (3, -2, 3), (6, -4, 3), (4, -6, 2),
        (12, 1, 2), (13, 0, 2), (14, -2, 3), (12, -4, 3), (15, -6, 2),
    )
    for x, y, r in puffs:  # round-ish puffs, lighter on top
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                if dx * dx + dy * dy <= r * r + 1:
                    px[(x + dx, y + dy)] = SMOKE if dy <= 0 else SMOKE_DARK

    for t in range(teeth):
        x0 = t * tw
        for dx in range(tw):
            ytop = top + dx * 5 // tw
            for y in range(ytop, base):
                px[(x0 + dx, y)] = STEEL
            px[(x0 + dx, ytop)] = STEEL_LIGHT  # lit roof edge
        for y in range(top, top + 5):  # the glazed vertical face of the tooth, glowing
            px[(x0, y)] = HOT
            px[(x0 + 1, y)] = ORANGE if y > top else HOT
    for x in range(width):  # shaded base
        px[(x, base - 1)] = STEEL_DARK
        px[(x, base)] = STEEL_DARK
    for wx in range(2, width - 2, 5):  # lit windows
        if 13 <= wx <= 19:
            continue  # the door sits here
        for y in range(top + 8, top + 11):
            for dx in range(3):
                px[(wx + dx, y)] = HOT if y == top + 8 else ORANGE
    for x in range(14, 19):  # door
        for y in range(base - 7, base - 1):
            px[(x, y)] = INK
        px[(x, base - 8)] = STEEL_DARK
    return px


def to_grid(px, w, h, ox=0, oy=0):
    g = [[None] * w for _ in range(h)]
    for (x, y), c in px.items():
        if 0 <= x + ox < w and 0 <= y + oy < h:
            g[y + oy][x + ox] = c
    return g


def bounds(px):
    xs = [p[0] for p in px]
    ys = [p[1] for p in px]
    return min(xs), min(ys), max(xs), max(ys)


# A 5 × 7 pixel font.
FONT = {
    "A": ["01110", "10001", "10001", "11111", "10001", "10001", "10001"],
    "B": ["11110", "10001", "10001", "11110", "10001", "10001", "11110"],
    "C": ["01111", "10000", "10000", "10000", "10000", "10000", "01111"],
    "D": ["11110", "10001", "10001", "10001", "10001", "10001", "11110"],
    "E": ["11111", "10000", "10000", "11110", "10000", "10000", "11111"],
    "F": ["11111", "10000", "10000", "11110", "10000", "10000", "10000"],
    "I": ["11111", "00100", "00100", "00100", "00100", "00100", "11111"],
    "L": ["10000", "10000", "10000", "10000", "10000", "10000", "11111"],
    "O": ["01110", "10001", "10001", "10001", "10001", "10001", "01110"],
    "P": ["11110", "10001", "10001", "11110", "10000", "10000", "10000"],
    "R": ["11110", "10001", "10001", "11110", "10100", "10010", "10001"],
    "S": ["01111", "10000", "10000", "01110", "00001", "00001", "11110"],
    "T": ["11111", "00100", "00100", "00100", "00100", "00100", "00100"],
    "U": ["10001", "10001", "10001", "10001", "10001", "10001", "01110"],
    "V": ["10001", "10001", "10001", "10001", "10001", "01010", "00100"],
    "Y": ["10001", "10001", "01010", "00100", "00100", "00100", "00100"],
    "·": ["00000", "00000", "00000", "00100", "00000", "00000", "00000"],
    " ": ["00000"] * 7,
}


def rects(cells, scale, x0=0, y0=0):
    """Pixels as SVG rects, merging horizontal runs of one colour."""
    out = []
    for y, row in enumerate(cells):
        x = 0
        while x < len(row):
            c = row[x]
            run = 1
            while x + run < len(row) and row[x + run] == c:
                run += 1
            if c:
                out.append(f'<rect x="{x0 + x * scale}" y="{y0 + y * scale}" '
                           f'width="{run * scale}" height="{scale}" fill="{c}"/>')
            x += run
    return "\n  ".join(out)


def text_cells(text, color, top=None):
    """Letters as a pixel grid; `top` colours the first two rows differently (Citadel-style)."""
    rows = [[] for _ in range(7)]
    for k, ch in enumerate(text):
        for y in range(7):
            if k:
                rows[y].append(None)  # one pixel between letters
            c = top if top and y < 2 else color
            rows[y].extend(c if b == "1" else None for b in FONT[ch][y])
    return rows


def svg(w, h, body, bg=None):
    back = f'<rect width="{w}" height="{h}" fill="{bg}"/>\n  ' if bg else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" '
            f'height="{h}" shape-rendering="crispEdges">\n  {back}{body}\n</svg>\n')


def factory_art():
    """The factory cropped to its bounds, as (grid, width, height)."""
    art = build_factory()
    x0, y0, x1, y1 = bounds(art)
    w, h = x1 - x0 + 1, y1 - y0 + 1
    return to_grid(art, w, h, -x0, -y0), w, h


def sky_bands(w, h, horizon, s):
    """Dusk gradient in flat bands, as full-width rectangles."""
    stops = [SKY_TOP, "#1b0d30", SKY_MID, "#40184f", SKY_LOW]
    band = horizon // len(stops) + 1
    return "\n  ".join(
        f'<rect x="0" y="{i * band}" width="{w}" height="{min(band, horizon - i * band)}" '
        f'fill="{c}"/>' for i, c in enumerate(stops) if i * band < horizon)


def stars(w, horizon, seed, keep_out=()):
    rnd = random.Random(seed)
    out = []
    for _ in range(70):
        x, y = rnd.randrange(8, w - 8), rnd.randrange(6, int(horizon * 0.7))
        if any(a <= x <= c and b <= y <= d for a, b, c, d in keep_out):
            continue
        size = rnd.choice((2, 2, 3, 4))
        col = rnd.choice((CREAM, CREAM, "#8f86b8", HOT))
        out.append(f'<rect x="{x}" y="{y}" width="{size}" height="{size}" fill="{col}"/>')
    return "\n  ".join(out)


def icon():
    """The factory on a dusk tile with rounded corners."""
    n = 48
    art, w, h = factory_art()
    g = [[None] * n for _ in range(n)]
    for y in range(1, n - 1):
        for x in range(1, n - 1):
            if x in (1, n - 2) and y in (1, n - 2):
                continue
            g[y][x] = SKY_MID if y < 30 else SKY_LOW
    ox, oy = (n - w) // 2, n - h - 5
    for y in range(h):
        for x in range(w):
            if art[y][x]:
                g[y + oy][x + ox] = art[y][x]
    return svg(n, n, rects(g, 1))


def banner():
    """The factory on the left of a dusk skyline, wordmark and tagline on the right."""
    art, aw, ah = factory_art()
    w, h, s = 1280, 360, 6
    ground = 300
    left = 110
    fy = ground - ah * s
    ts, gs = 15, 4
    title, tag = "FACTORY", "DESCRIBE · PROVE · BUILD"
    tw = (len(title) * 6 - 1) * ts
    tx = left + aw * s + 100
    ty = 62
    skyline = []
    rnd = random.Random(7)
    x = 0
    while x < w:  # distant roofline of other plants, as dark steps
        bw, bh = rnd.choice((40, 56, 72)), rnd.choice((16, 24, 32, 40))
        skyline.append(f'<rect x="{x}" y="{ground - bh}" width="{bw}" height="{bh}" '
                       f'fill="#1a0f2b"/>')
        x += bw
    body = "\n  ".join([
        sky_bands(w, h, ground, s),
        stars(w, ground, 11, keep_out=[(left - 20, ty - 20, tx + tw + 40, ty + 150)]),
        "\n  ".join(skyline),
        f'<rect x="0" y="{ground}" width="{w}" height="{h - ground}" fill="{GROUND}"/>',
        f'<rect x="0" y="{ground}" width="{w}" height="4" fill="{PINK}"/>',
        rects(art, s, left, fy),
        rects(text_cells(title, INK), ts, tx + ts // 2, ty + ts // 2),  # pop shadow
        rects(text_cells(title, ORANGE, top=HOT), ts, tx, ty),
        rects(text_cells(tag, "#9fd8f0"), gs, tx + 2, ty + 7 * ts + 28),
    ])
    return svg(w, h, body)


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "assets")
    out.mkdir(parents=True, exist_ok=True)
    (out / "icon.svg").write_text(icon())
    (out / "banner.svg").write_text(banner())
    print("ok")
