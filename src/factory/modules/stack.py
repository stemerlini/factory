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

    cad:                         # parts from Brick (the 3D parts library), pinned
      - {name: Housing, ref: "repe-housing/geant4@v1#sha256:<pin>", position_mm: [0, 0, 20],
         rotation_deg: [0, 0, 0], sensitive: false}

A layer's thickness is ``thickness_nm``, ``_um`` or ``_mm``; ``gap_mm`` is the vacuum before it.

A ``cad`` entry is exported from Brick as GDML (``brick export REF -f gdml``, hash-checked) and its
volumes are merged into the world at ``position_mm`` (the origin of the part's frame, mm), turned
by ``rotation_deg`` as in GDML's ``<rotation>`` (Geant4 rotates the frame: a part turned +30
degrees about z is ``[0, 0, -30]``). The ref must carry its pin; ``sensitive: true`` records
every volume of the part.
"""

from __future__ import annotations

import json
import math
import subprocess
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
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


@dataclass
class Cad:
    """A Brick part fetched for a run: the pinned ref it came from and its GDML."""

    name: str
    ref: str
    pin: str
    path: Path
    position: tuple[float, float, float]
    rotation: tuple[float, float, float]
    sensitive: bool
    behind: object = None
    volumes: list[str] = field(default_factory=list)
    radius: float = 0.0  # of the sphere around the part's frame origin that holds its world box
    half: tuple[float, float, float] = (0.0, 0.0, 0.0)  # half-sizes of the part's world box


def brick_export(ref: str, dest: Path) -> dict:
    """``brick export REF -f gdml -o DEST --json``: Brick checks the file against the pin."""
    dest.mkdir(parents=True, exist_ok=True)
    done = subprocess.run(["brick", "export", ref, "-f", "gdml", "-o", str(dest), "--json"],
                          capture_output=True, text=True)
    if done.returncode:
        raise StackError(f"brick export {ref}: {(done.stderr or done.stdout).strip()}")
    return json.loads(done.stdout)


def _vec(entry: dict, key: str) -> tuple[float, float, float]:
    v = entry.get(key) or [0, 0, 0]
    if len(v) != 3:
        raise StackError(f"{entry.get('name', '?')}: {key} needs three numbers")
    return float(v[0]), float(v[1]), float(v[2])


def fetch(stack: dict, dest: Path, export: Callable[[str, Path], dict] = brick_export) -> list[Cad]:
    """Export every ``cad`` part of the stack from Brick into ``dest/<name>/``, refusing a ref
    without a pin or a file whose pin is not the one asked for."""
    found: list[Cad] = []
    for entry in stack.get("cad", []) or []:
        name, ref = str(entry["name"]), str(entry["ref"])
        asked = ref.partition("#sha256:")[2]
        if not asked or "@" not in ref:
            raise StackError(f"{name}: ref {ref!r} needs a version and its pin, "
                             "item[/variant]@vN#sha256:<pin> (brick export prints it)")
        got = export(ref, Path(dest) / name)
        if got.get("pin") != asked:
            raise StackError(f"{name}: Brick served pin {got.get('pin')!r}, not the pinned {asked}")
        cad = Cad(name, got.get("ref", ref), asked, Path(got["path"]), _vec(entry, "position_mm"),
                  _vec(entry, "rotation_deg"), bool(entry.get("sensitive", False)),
                  got.get("behind"))
        _read_part(cad)
        found.append(cad)
    return found


def _part_world(root: ET.Element) -> tuple[ET.Element, ET.Element]:
    """The part's world volume and its box solid."""
    world_ref = root.find("setup/world").get("ref")
    structure = root.find("structure")
    world = next(v for v in structure.findall("volume") if v.get("name") == world_ref)
    solid_ref = world.find("solidref").get("ref")
    box = next(b for b in root.find("solids").findall("box") if b.get("name") == solid_ref)
    return world, box


def _read_part(cad: Cad) -> None:
    root = ET.parse(cad.path).getroot()
    world, box = _part_world(root)
    scale = {"mm": 1.0, "cm": 10.0, "m": 1000.0}[box.get("lunit", "mm")]
    cad.half = tuple(float(box.get(a)) * scale / 2 for a in "xyz")
    cad.radius = math.sqrt(sum(h * h for h in cad.half))
    cad.volumes = [v.get("name") for v in root.find("structure").findall("volume")
                   if v is not world]


def save_cad(cad: list[Cad], path: Path) -> None:
    """Which geometry a run used: refs and pins, with what the 3D view needs."""
    path.write_text(json.dumps([{**asdict(c), "path": str(c.path)} for c in cad], indent=2))


def read_cad(path: Path) -> list[Cad]:
    if not path.is_file():
        return []
    return [Cad(**{**d, "path": Path(d["path"]), "position": tuple(d["position"]),
                   "rotation": tuple(d["rotation"]), "half": tuple(d["half"])})
            for d in json.loads(path.read_text())]


def sensitive(stack: dict, cad: list[Cad] = ()) -> list[str]:
    """The stack's layers, and the volumes of ``sensitive`` cad parts: the volumes whose
    deposits are recorded by default."""
    return [str(layer["name"]) for layer in stack["layers"]] + [
        v for c in cad if c.sensitive for v in c.volumes]


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


def _inner(el: ET.Element | None, skip: tuple[str, ...] = ()) -> list[ET.Element]:
    return [] if el is None else [c for c in el if c.get("name") not in skip]


def _merge(cad: list[Cad], used: set[str]) -> dict[str, list[str]]:
    """The GDML sections of the cad parts, as text, and the physvols to place in the world."""
    out: dict[str, list[str]] = {k: [] for k in ("define", "materials", "solids", "volumes",
                                                 "placed")}

    def add(section: str, els: list[ET.Element]) -> None:
        for el in els:
            key = f'{section}:{el.get("name")}'
            if key in used:
                raise StackError(f"duplicate {el.tag} {el.get('name')!r} from a cad part")
            used.add(key)
            out[section].append(ET.tostring(el, encoding="unicode"))

    for c in cad:
        root = ET.parse(c.path).getroot()
        world, box = _part_world(root)
        add("define", _inner(root.find("define")))
        add("materials", _inner(root.find("materials")))
        add("solids", _inner(root.find("solids"), (box.get("name"),)))
        add("volumes", _inner(root.find("structure"), (world.get("name"),)))
        for pv in world.findall("physvol"):
            for old in pv.findall("position") + pv.findall("rotation"):
                pv.remove(old)
            for tag, unit, vec in (("position", "mm", c.position), ("rotation", "deg", c.rotation)):
                xyz = zip("xyz", map(_num, vec), strict=True)
                pv.append(ET.Element(tag, unit=unit, **dict(xyz)))
            add("placed", [pv])
    return out


def _num(x: float) -> str:
    return f"{x:.9g}"


def to_gdml(stack: dict, cad: list[Cad] = ()) -> str:
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

    merged = _merge(list(cad), {f"{sec}:{n}{suf}" for n in used for sec, suf in
                             (("volumes", ""), ("solids", "_s"), ("placed", "_pv"))}
                            | {"volumes:World", "solids:World_s"})
    for c in cad:
        px, py, pz = c.position
        extent = max(extent, max(abs(px), abs(py)) + c.radius)
        zmin, zmax = min(zmin, pz - c.radius), max(zmax, pz + c.radius)

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
        f"<define>{''.join(merged['define'])}</define>\n"
        f"<materials>{''.join(materials + merged['materials'])}</materials>\n"
        f"<solids>\n{chr(10).join(solids + merged['solids'])}\n</solids>\n"
        f"<structure>\n{chr(10).join(volumes + merged['volumes'])}\n"
        f'<volume name="World"><materialref ref="G4_Galactic"/><solidref ref="World_s"/>'
        f"{''.join(placed + merged['placed'])}</volume>\n</structure>\n"
        '<setup name="Default" version="1.0"><world ref="World"/></setup>\n</gdml>\n'
    )

