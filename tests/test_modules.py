import json

import pytest

from factory.modules import kicad, registry
from factory.modules.api import Toolchain
from factory.project import Project


def test_registry_has_builtins():
    mods = registry.all_modules()
    assert {"core", "script", "geant4", "irene", "pic", "ngspice", "kicad", "freecad",
            "software"} <= set(mods)
    assert {"system", "generic", "electronics", "mechanical", "software"} <= set(registry.kinds())
    assert registry.kind("nonsense").id == "generic"
    assert registry.find_task("script")[1].id == "main"
    assert registry.find_task("geant4.run")[1].toolchain == "geant4"
    with pytest.raises(LookupError):
        registry.find_task("geant4.nope")


def test_params_are_typed_and_defaulted():
    _, task = registry.find_task("pic.run")
    params = task.resolve_params({"ranks": "4", "extra": 1})
    assert params == {"code": "warpx", "input": "input.deck", "ranks": 4, "extra": 1}
    assert task.toolchain_for(params) == "warpx"


def test_toolchain_versions_and_env(tmp_path):
    for v in ("11.2.0", "11.10.1", "11.4.3"):
        (tmp_path / "g4" / v / "bin").mkdir(parents=True)
    script = tmp_path / "g4/11.4.3/bin/env.sh"
    script.write_text('export G4FOO="$(pwd)/data"\n')
    tc = Toolchain("g4", env_script="bin/env.sh", root=tmp_path)
    assert tc.versions() == ["11.2.0", "11.4.3", "11.10.1"]
    assert tc.environment("11.4.3")["G4FOO"].endswith("11.4.3/data")
    with pytest.raises(LookupError, match="g4 9.0"):
        tc.environment("9.0")


def test_kicad_erc_counts_errors(tmp_path):
    from types import SimpleNamespace

    (tmp_path / "erc.json").write_text(json.dumps({"sheets": [
        {"violations": [{"severity": "error"}, {"severity": "warning"}]},
        {"violations": [{"severity": "error"}]},
    ]}))
    ctx = SimpleNamespace(outputs_dir=tmp_path)
    assert kicad.erc_collect(ctx)["erc_errors"].value == 2


def test_ngspice_meas_parsing(tmp_path):
    from types import SimpleNamespace

    from factory.modules import ngspice

    (tmp_path / "ngspice.log").write_text(
        "vmin                =  -3.67e-04 at=  2.1e-06\ngain = 3.670000e-01\n")
    found = ngspice.collect(SimpleNamespace(outputs_dir=tmp_path))
    assert found["gain"].value == pytest.approx(0.367)
    assert found["vmin"].value == pytest.approx(-3.67e-4)


def test_viewer_lookup(tmp_path, monkeypatch):
    import shutil
    from pathlib import Path
    monkeypatch.setattr(shutil, "which", lambda exe: f"/usr/bin/{exe}")
    assert registry.viewer_for(Path("field.vtu")).id == "paraview"
    assert registry.viewer_for(Path("head.step")).id == "f3d"
    assert registry.viewer_for(Path("plot.png")).id == "image"
    assert registry.viewer_for(Path("notes.txt")) is None



def test_suite_lists_the_projects_tools_first(make):
    p = make()
    p.enable("irene")
    p.enable("geant4", False)  # a study still runs geant4.run, so the project still uses it
    p = Project.load(p.root)
    assert p.modules[-1] == "irene" and "geant4" not in p.modules
    rows = {m.id: (uses, studies) for m, uses, studies in registry.suite(p)}
    assert rows["irene"] == (True, []) and rows["geant4"][0] and rows["geant4"][1]
    assert not rows["pic"][0]
    flags = [uses for _, uses, _ in registry.suite(p)]
    assert flags == sorted(flags, reverse=True)


STACK = {
    "layers": [
        {"name": "Kap", "material": "G4_KAPTON", "thickness_um": 50, "half_mm": 10},
        {"name": "D1", "material": "G4_Si", "thickness_um": 300, "radius_mm": 5, "gap_mm": 1},
    ],
    "volumes": [{"name": "Coll", "material": "G4_W", "rmin_mm": 6, "rmax_mm": 15,
                 "z_mm": 0, "length_mm": 5}],
}


def test_stack_becomes_gdml_with_the_front_face_at_zero():
    import xml.etree.ElementTree as ET

    from factory.modules import stack

    root = ET.fromstring(stack.to_gdml(STACK))
    z = {pv.get("name"): float(pv.find("position").get("z")) for pv in root.iter("physvol")}
    assert z["Kap_pv"] == pytest.approx(0.025)  # 50 um centred behind z = 0
    assert z["D1_pv"] == pytest.approx(0.05 + 1 + 0.15)
    assert stack.sensitive(STACK) == ["Kap", "D1"]
    with pytest.raises(stack.StackError, match="radius_mm"):
        stack.to_gdml({"layers": [{"name": "X", "material": "G4_Si", "thickness_um": 1}]})


PART_GDML = """<?xml version="1.0"?>
<gdml><define><position name="plate_v0" unit="mm" x="0" y="0" z="0"/></define>
<materials/><solids>
<tessellated name="plate_solid"><triangular vertex1="plate_v0" vertex2="plate_v0"
 vertex3="plate_v0"/></tessellated>
<box name="plate_world_solid" lunit="mm" x="20" y="20" z="10"/></solids>
<structure><volume name="plate"><materialref ref="G4_Al"/><solidref ref="plate_solid"/></volume>
<volume name="plate_world"><materialref ref="G4_Galactic"/><solidref ref="plate_world_solid"/>
<physvol name="plate_pv"><volumeref ref="plate"/></physvol></volume></structure>
<setup name="Default" version="1.0"><world ref="plate_world"/></setup></gdml>"""
CAD_STACK = {"layers": [{"name": "D1", "material": "G4_Si", "thickness_um": 300, "radius_mm": 5}],
             "cad": [{"name": "Plate", "ref": "plate/sim@v2#sha256:abc", "sensitive": True,
                      "position_mm": [1, 2, -20], "rotation_deg": [0, 0, 90]}]}


def fake_brick(tmp_path, pin="abc"):
    def export(ref, dest):
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "plate.gdml").write_text(PART_GDML)
        return {"ref": ref.split("#")[0] + f"#sha256:{pin}", "path": str(dest / "plate.gdml"),
                "pin": pin, "behind": None, "access": "open", "licence": "CC0"}
    return export


def test_cad_part_from_brick_is_merged_into_the_world(tmp_path):
    import xml.etree.ElementTree as ET

    from factory.modules import stack

    cad = stack.fetch(CAD_STACK, tmp_path / "inputs", export=fake_brick(tmp_path))
    root = ET.fromstring(stack.to_gdml(CAD_STACK, cad))
    world = next(v for v in root.iter("volume") if v.get("name") == "World")
    pv = next(p for p in world.iter("physvol") if p.get("name") == "plate_pv")
    assert pv.find("position").attrib["z"] == "-20"
    assert pv.find("rotation").attrib["z"] == "90"
    assert [v.get("name") for v in root.iter("volume")].count("plate_world") == 0
    assert stack.sensitive(CAD_STACK, cad) == ["D1", "plate"]
    assert cad[0].pin == "abc" and cad[0].ref.endswith("#sha256:abc")

    from factory.modules import scene

    box = scene.cad_outlines(cad)  # 20 x 20 x 10 box turned by Geant4's convention, then moved
    xs = {round(p[0], 6) for pts, _ in box for p in pts}
    assert len(box) == 12 and xs == {-9.0, 11.0}
    assert scene._turn((1, 0, 0), (0, 0, 90)) == pytest.approx((0, -1, 0))


def test_cad_part_must_be_pinned_and_match_its_pin(tmp_path):
    from factory.modules import stack

    unpinned = {**CAD_STACK, "cad": [{"name": "P", "ref": "plate/sim@latest"}]}
    with pytest.raises(stack.StackError, match="pin"):
        stack.fetch(unpinned, tmp_path, export=fake_brick(tmp_path))
    with pytest.raises(stack.StackError, match="pin"):
        stack.fetch(CAD_STACK, tmp_path, export=fake_brick(tmp_path, pin="other"))


def test_geant4_run_command_from_a_stack(make):
    import yaml

    from factory.modules.api import RunContext
    from factory.modules.geant4 import run_command

    p = make()
    stack_file = p.root / "stack.yaml"
    stack_file.write_text(yaml.safe_dump(STACK))
    study = p.study("S001")
    outputs = p.root / "out"
    ctx = RunContext(p, study, "r1", outputs.parent, {
        "app": "factory-g4", "macro": "m.mac", "physics": "QBBC", "sensitive": "", "threads": 4,
        "tracks": 5,
    }, {"stack": stack_file})
    argv = run_command(ctx).argv
    gdml = ctx.outputs_dir / "geometry.gdml"
    assert gdml.is_file()
    assert argv[:9] == ["factory-g4", "--gdml", str(gdml), "--physics", "QBBC",
                        "--tracks", "5", "--sensitive", "Kap,D1"]
    assert argv[-3:] == [str(study.dir / "m.mac"), "-t", "4"]


def test_scene_is_valid_vtk_with_outlines_and_tracks(tmp_path):
    from factory.modules import scene

    tracks = tmp_path / "tracks.csv"
    tracks.write_text("event,track,parent,particle,x,y,z\n"
                      "0,1,0,e-,0,0,-5\n0,1,0,e-,0,0,0\n0,1,0,e-,1,0,0.1\n"
                      "0,2,1,gamma,1,0,0.1\n0,2,1,gamma,2,0,3\n0,3,1,e-,1,0,0.1\n")
    out = scene.build(STACK, tracks, tmp_path / "scene.vtk")
    text = out.read_text().splitlines()
    n_points = int(next(t for t in text if t.startswith("POINTS")).split()[1])
    head = next(i for i, t in enumerate(text) if t.startswith("LINES"))
    n_lines = int(text[head].split()[1])
    cells = [list(map(int, t.split())) for t in text[head + 1:head + 1 + n_lines]]
    assert sum(len(c) - 1 for c in cells) == n_points
    assert max(i for c in cells for i in c[1:]) == n_points - 1
    kinds = [int(t) for t in text[-n_lines:]]
    assert kinds.count(0) == 1 and kinds.count(1) == 1  # the 1-point track is dropped
    assert kinds.count(scene.OUTLINE) == n_lines - 2
