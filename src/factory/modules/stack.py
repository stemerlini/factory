"""A layer stack (``stack.yaml``) to GDML, for ``factory-g4``.

The stack is described front to back along +z, the front face at z = 0::

    world_margin_mm: 50          # vacuum around the stack (default 50)
    materials:                   # optional: Geant4 NIST names (G4_Si) need no entry
      GAGG: {density_g_cm3: 6.63, formula: {Gd: 3, Al: 2, Ga: 3, O: 12}}
    layers:
      - {name: Kapton, material: G4_KAPTON, thickness_um: 50, half_mm: 10}   # square
      - {name: D1, material: G4_Si, thickness_um: 300, radius_mm: 5}         # disc
      - {name: D2, material: G4_Si, thickness_um: 300, radius_mm: 5, gap_mm: 1}
    volumes:                     # housing, collimators: tubes at an absolute z
      - {name: Collimator, material: G4_W, rmin_mm: 5, rmax_mm: 15, z_mm: -10, length_mm: 8}

A layer's thickness is ``thickness_nm``, ``_um`` or ``_mm``; ``gap_mm`` is the vacuum before it.
"""

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import quoteattr

import yaml

_SCALE_MM = {"nm": 1e-6, "um": 1e-3, "mm": 1.0}


class StackError(ValueError):
    pass


def _length_mm(entry: dict, key: str, required: bool = True) -> float | None:
    for unit, scale in _SCALE_MM.items():
        if f"{key}_{unit}" in entry:
            return float(entry[f"{key}_{unit}"]) * scale
    if required:
        raise StackError(f"{entry.get('name', '?')}: needs {key}_nm, {key}_um or {key}_mm")
    return None


def load(path: Path) -> dict:
    data = yaml.safe_load(Path(path).read_text()) or {}
    if not data.get("layers"):
        raise StackError(f"{path}: no layers")
    return data


def sensitive(stack: dict) -> list[str]:
    """The stack's layers: the volumes whose deposits are recorded by default."""
    return [str(layer["name"]) for layer in stack["layers"]]


def placements(stack: dict) -> list[dict]:
    """Every volume with its shape and place: ``name, material, shape ("tube"|"box"), rmin, rmax
    (a box's ``rmax`` is its half-width), z0, length``, all in mm, z0 the front face."""
    out: list[dict] = []
    z = 0.0
    for layer in stack["layers"]:
        name = str(layer["name"])
        z += float(layer.get("gap_mm", 0.0))
        t = _length_mm(layer, "thickness")
        if "radius_mm" in layer:
            shape, rmax = "tube", float(layer["radius_mm"])
        elif "half_mm" in layer:
            shape, rmax = "box", float(layer["half_mm"])
        else:
            raise StackError(f"{name}: needs radius_mm (disc) or half_mm (square)")
        out.append({"name": name, "material": str(layer["material"]), "shape": shape,
                    "rmin": 0.0, "rmax": rmax, "z0": z, "length": t})
        z += t
    for vol in stack.get("volumes", []):
        out.append({"name": str(vol["name"]), "material": str(vol["material"]), "shape": "tube",
                    "rmin": float(vol.get("rmin_mm", 0.0)), "rmax": float(vol["rmax_mm"]),
                    "z0": float(vol["z_mm"]), "length": float(vol["length_mm"])})
    return out


def to_gdml(stack: dict) -> str:
    solids: list[str] = []
    volumes: list[str] = []
    placed: list[str] = []
    used: set[str] = set()
    extent, zmin, zmax = 0.0, 0.0, 0.0
    for v in placements(stack):
        name, t, z0 = v["name"], v["length"], v["z0"]
        if name in used:
            raise StackError(f"duplicate volume name {name!r}")
        used.add(name)
        if v["shape"] == "box":
            h = v["rmax"]
            solids.append(f'<box name="{name}_s" lunit="mm" x="{2 * h:.9g}" y="{2 * h:.9g}" '
                          f'z="{t:.9g}"/>')
            extent = max(extent, h * 2 ** 0.5)
        else:
            solids.append(f'<tube name="{name}_s" aunit="deg" lunit="mm" rmin="{v["rmin"]:.9g}" '
                          f'rmax="{v["rmax"]:.9g}" z="{t:.9g}" startphi="0" deltaphi="360"/>')
            extent = max(extent, v["rmax"])
        volumes.append(f'<volume name="{name}"><materialref ref={quoteattr(v["material"])}/>'
                       f'<solidref ref="{name}_s"/></volume>')
        placed.append(f'<physvol name="{name}_pv"><volumeref ref="{name}"/>'
                      f'<position unit="mm" x="0" y="0" z="{z0 + t / 2:.9g}"/></physvol>')
        zmin, zmax = min(zmin, z0), max(zmax, z0 + t)

    margin = float(stack.get("world_margin_mm", 50.0))
    half_xy, half_z = extent + margin, max(-zmin, zmax) + margin  # world centred on z = 0
    solids.append(f'<box name="World_s" lunit="mm" x="{2 * half_xy:.9g}" y="{2 * half_xy:.9g}" '
                  f'z="{2 * half_z:.9g}"/>')

    materials = []
    for name, spec in (stack.get("materials") or {}).items():
        comp = "".join(f'<composite n="{int(n)}" ref="{el}"/>' for el, n in spec["formula"].items())
        density = float(spec["density_g_cm3"])
        materials.append(f'<material name="{name}"><D unit="g/cm3" value="{density}"/>'
                         f"{comp}</material>")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<gdml xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xsi:noNamespaceSchemaLocation="http://service-spi.web.cern.ch/service-spi/app/releases/GDML/schema/gdml.xsd">\n'
        "<define/>\n"
        f"<materials>{''.join(materials)}</materials>\n"
        f"<solids>\n{chr(10).join(solids)}\n</solids>\n"
        f"<structure>\n{chr(10).join(volumes)}\n"
        f'<volume name="World"><materialref ref="G4_Galactic"/><solidref ref="World_s"/>'
        f"{''.join(placed)}</volume>\n</structure>\n"
        '<setup name="Default" version="1.0"><world ref="World"/></setup>\n</gdml>\n'
    )

