"""The Factory logo for the top bar: the icon's anvil as a small sprite, drawn with half
blocks (two pixels per cell), as in Citadel and Toploader."""

from __future__ import annotations

from rich.style import Style
from rich.text import Text

PALETTE = {
    "S": "#8FA3BF",  # steel
    "L": "#D7E0EC",  # lit steel
    "d": "#5E7290",  # steel in shadow
    "O": "#FF7A1A",  # hot ingot
    "Y": "#FFD23F",  # white-hot, sparks
    }

ANVIL = """
...Y...Y...Y....
................
.....OOOOO......
LLLLLLLLLLLLLLL.
..SSSSSSSSSSSSd.
.....SSSSSd.....
...SSSSSSSSSd...
..ddddddddddddd.
"""


def sprite(art: str = ANVIL) -> list[str]:
    rows = art.strip().splitlines()
    assert len({len(r) for r in rows}) == 1, "sprite rows must have equal length"
    return rows


def render(art: str = ANVIL) -> Text:
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
