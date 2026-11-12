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
