"""The Factory logo for the top bar: the icon's factory as a small sprite, drawn with half
blocks (two pixels per cell), as in Citadel and Toploader."""

from __future__ import annotations

from rich.style import Style
from rich.text import Text

PALETTE = {
    "S": "#8FA3BF",  # steel walls
    "L": "#D7E0EC",  # lit steel: roof edges, chimney rims and bands
    "d": "#5E7290",  # steel in shadow
    "R": "#C8472B",  # brick chimney
    "r": "#8E2F1D",  # brick in shadow
    "O": "#FF7A1A",  # glowing window
    "Y": "#FFD23F",  # glazed roof teeth
    "s": "#B9B4C8",  # smoke
    "m": "#8C86A3",  # smoke in shadow
    "K": "#141414",  # door
}

# The banner's factory at Citadel's size: 15 x 8 pixels, four terminal rows.
FACTORY = """
.ssm..ssm......
.Rr...Rr.......
.Rr...Rr.......
.Rr...Rr.......
YLL..YLL..YLL..
YSSLLYSSLLYSSLL
YSOOSYSKKSYSOOS
dddddddKKdddddd
"""

def sprite(art: str = FACTORY) -> list[str]:
    rows = art.strip().splitlines()
    assert len({len(r) for r in rows}) == 1, "sprite rows must have equal length"
    return rows


def render(art: str = FACTORY) -> Text:
    rows = sprite(art)
    if len(rows) % 2:
        rows.append("." * len(rows[0]))
    text = Text()
    for top, bottom in zip(rows[::2], rows[1::2], strict=True):
        for t, b in zip(top, bottom, strict=True):
            ct, cb = PALETTE.get(t), PALETTE.get(b)
            if ct and cb:
                text.append("▀", Style(color=ct, bgcolor=cb))
            elif ct:
                text.append("▀", Style(color=ct))
            elif cb:
                text.append("▄", Style(color=cb))
            else:
                text.append(" ")
        text.append("\n")
    text.rstrip()
    return text
