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


def to_gdml(stack: dict) -> str:
    solids: list[str] = []
    volumes: list[str] = []
    placed: list[str] = []
    used: set[str] = set()
    extent = 0.0

    def add(name: str, material: str, solid: str, z0: float, length: float) -> None:
        if name in used:
            raise StackError(f"duplicate volume name {name!r}")
        used.add(name)
        solids.append(solid)
        volumes.append(f'<volume name="{name}"><materialref ref={quoteattr(material)}/>'
                       f'<solidref ref="{name}_s"/></volume>')
        placed.append(f'<physvol name="{name}_pv"><volumeref ref="{name}"/>'
                      f'<position unit="mm" x="0" y="0" z="{z0 + length / 2:.9g}"/></physvol>')

    z = 0.0
    zmin, zmax = 0.0, 0.0
    for layer in stack["layers"]:
        name = str(layer["name"])
        z += float(layer.get("gap_mm", 0.0))
        t = _length_mm(layer, "thickness")
        if "radius_mm" in layer:
            r = float(layer["radius_mm"])
            solid = (f'<tube name="{name}_s" aunit="deg" lunit="mm" rmin="0" rmax="{r:.9g}" '
                     f'z="{t:.9g}" startphi="0" deltaphi="360"/>')
            extent = max(extent, r)
        elif "half_mm" in layer:
            h = float(layer["half_mm"])
            solid = (f'<box name="{name}_s" lunit="mm" x="{2 * h:.9g}" y="{2 * h:.9g}" '
                     f'z="{t:.9g}"/>')
            extent = max(extent, h * 2 ** 0.5)
        else:
            raise StackError(f"{name}: needs radius_mm (disc) or half_mm (square)")
        add(name, str(layer["material"]), solid, z, t)
        z += t
        zmax = max(zmax, z)
    for vol in stack.get("volumes", []):
        name = str(vol["name"])
        rmin, rmax = float(vol.get("rmin_mm", 0.0)), float(vol["rmax_mm"])
        length, z0 = float(vol["length_mm"]), float(vol["z_mm"])
        solid = (f'<tube name="{name}_s" aunit="deg" lunit="mm" rmin="{rmin:.9g}" '
                 f'rmax="{rmax:.9g}" '
                 f'z="{length:.9g}" startphi="0" deltaphi="360"/>')
        add(name, str(vol["material"]), solid, z0, length)
        extent = max(extent, rmax)
        zmin, zmax = min(zmin, z0), max(zmax, z0 + length)

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

