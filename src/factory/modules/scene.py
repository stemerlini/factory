"""A Geant4 scene for f3d/ParaView: volume outlines and particle tracks in one VTK file.

``scene.vtk`` is legacy ASCII PolyData made only of polylines (outlines, so tracks inside solid
volumes stay visible), coloured by the cell scalar ``kind``: tracks e- 0, gamma 1, e+ 2,
proton 3, other 4; volume outlines 6.
"""

from __future__ import annotations

import csv
import math
from pathlib import Path

from . import stack as stacks

KINDS = {"e-": 0, "gamma": 1, "e+": 2, "proton": 3}
OTHER, OUTLINE = 4, 6
# Colormap stops (value/6, r, g, b): e- blue, gamma yellow, e+ red, proton green, other violet,
# outlines grey.
_COLORMAP = ("0,0.2,0.6,1,0.1667,1,0.9,0.2,0.3333,1,0.3,0.3,0.5,0.4,1,0.4,"
             "0.6667,0.8,0.5,1,1,0.5,0.5,0.5")
Line = tuple[list[tuple[float, float, float]], int]


def _circle(r: float, z: float, n: int = 48) -> list[tuple[float, float, float]]:
    return [(r * math.cos(2 * math.pi * i / n), r * math.sin(2 * math.pi * i / n), z)
            for i in range(n + 1)]


def outlines(stack: dict) -> list[Line]:
    lines: list[Line] = []
    for v in stacks.placements(stack):
        z0, z1, r = v["z0"], v["z0"] + v["length"], v["rmax"]
        if v["shape"] == "box":
            c = [(sx * r, sy * r) for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
            for z in (z0, z1):
                lines.append(([(x, y, z) for x, y in c + c[:1]], OUTLINE))
            lines += [([(x, y, z0), (x, y, z1)], OUTLINE) for x, y in c]
            continue
        radii = [v["rmax"]] + ([v["rmin"]] if v["rmin"] > 0 else [])
        for rad in radii:
            lines += [(_circle(rad, z0), OUTLINE), (_circle(rad, z1), OUTLINE)]
            lines += [([(rad * dx, rad * dy, z0), (rad * dx, rad * dy, z1)], OUTLINE)
                      for dx, dy in ((1, 0), (0, 1), (-1, 0), (0, -1))]
    return lines


def read_tracks(path: Path) -> list[Line]:
    """``tracks.csv`` (event, track, parent, particle, x, y, z in mm), one polyline per track."""
    tracks: dict[tuple[str, str], Line] = {}
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            key = (row["event"], row["track"])
            kind = KINDS.get(row["particle"], OTHER)
            tracks.setdefault(key, ([], kind))[0].append(
                (float(row["x"]), float(row["y"]), float(row["z"])))
    return [t for t in tracks.values() if len(t[0]) > 1]


def write_vtk(path: Path, lines: list[Line]) -> None:
    points = [p for pts, _ in lines for p in pts]
    out = ["# vtk DataFile Version 3.0", "Geant4 scene", "ASCII", "DATASET POLYDATA",
           f"POINTS {len(points)} float"]
    out += [f"{x:.6g} {y:.6g} {z:.6g}" for x, y, z in points]
    out.append(f"LINES {len(lines)} {len(points) + len(lines)}")
    i = 0
    for pts, _ in lines:
        out.append(" ".join([str(len(pts))] + [str(i + k) for k in range(len(pts))]))
        i += len(pts)
    out += [f"CELL_DATA {len(lines)}", "SCALARS kind float 1", "LOOKUP_TABLE default"]
    out += [str(kind) for _, kind in lines]
    Path(path).write_text("\n".join(out) + "\n")


def build(stack: dict | None, tracks: Path | None, path: Path) -> Path:
    lines = outlines(stack) if stack else []
    if tracks is not None and tracks.is_file():
        lines += read_tracks(tracks)
    write_vtk(path, lines)
    return path


def f3d_argv(path: Path) -> list[str]:
    return ["f3d", "--scalar-coloring", "--coloring-array=kind", "--coloring-by-cells",
            "--coloring-range=0,6", f"--colormap={_COLORMAP}", "--line-width=3", str(path)]
