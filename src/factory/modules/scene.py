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
Line = tuple[list[tuple[float, float, float]], int]


def _circle(r: float, z: float, n: int = 48) -> list[tuple[float, float, float]]:
    return [(r * math.cos(2 * math.pi * i / n), r * math.sin(2 * math.pi * i / n), z)
            for i in range(n + 1)]


def _turn(p: tuple[float, float, float], deg: tuple[float, float, float]):
    """A point moved as Geant4 does for a GDML ``<rotation>``: the inverse of Rz·Ry·Rx."""
    x, y, z = p
    for axis, a in (("z", deg[2]), ("y", deg[1]), ("x", deg[0])):
        c, s = math.cos(math.radians(-a)), math.sin(math.radians(-a))
        if axis == "x":
            y, z = y * c - z * s, y * s + z * c
        elif axis == "y":
            x, z = x * c + z * s, -x * s + z * c
        else:
            x, y = x * c - y * s, x * s + y * c
    return x, y, z


def cad_outlines(cad: list) -> list[Line]:
    """The box around each Brick part (its tight world), at its place in the stack."""
    lines: list[Line] = []
    for c in cad:
        hx, hy, hz = c.half
        corner = {(i, j, k): _turn((i * hx, j * hy, k * hz), c.rotation)
                  for i in (-1, 1) for j in (-1, 1) for k in (-1, 1)}
        at = {k: (p[0] + c.position[0], p[1] + c.position[1], p[2] + c.position[2])
              for k, p in corner.items()}
        for (i, j, k), a in at.items():
            for b in ((-i, j, k), (i, -j, k), (i, j, -k)):
                if (i, j, k) < b:
                    lines.append(([a, at[b]], OUTLINE))
    return lines


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


def build(stack: dict | None, tracks: Path | None, path: Path, cad: list = ()) -> Path:
    lines = outlines(stack) + cad_outlines(list(cad)) if stack else []
    if tracks is not None and tracks.is_file():
        lines += read_tracks(tracks)
    write_vtk(path, lines)
    return path
