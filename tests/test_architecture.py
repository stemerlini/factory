import json
import shutil

import pytest

from factory import architecture
from factory.project import ProjectError


def test_load_render_export(make):
    p = make()
    arch = architecture.load(p.root)
    assert [b.id for b in arch.blocks] == ["head", "fee", "fsw"]
    assert [[b.id for b in col] for col in arch.layers()] == [["head"], ["fee"], ["fsw"]]
    assert arch.problems({r.id for r in p.requirements(p.tree())}) == []
    text, skipped = architecture.render(arch)
    assert "Sensor head" in text and "▶" in text and skipped == []
    assert "head -> fee" in arch.to_d2()
    canvas = json.loads(arch.to_canvas())
    assert {n["id"] for n in canvas["nodes"]} == {"head", "fee", "fsw"}
    assert len(canvas["edges"]) == 3


def test_problems_are_found(tmp_path):
    (tmp_path / "architecture.yaml").write_text(
        "blocks: [{id: a}, {id: a}]\ninterfaces: [{from: a.x, to: b.y}]\nallocate: {R-1: [c]}\n")
    problems = architecture.load(tmp_path).problems({"R-2"})
    assert any("duplicate block" in x for x in problems)
    assert any("unknown block 'b'" in x for x in problems)
    assert any("unknown block 'c'" in x for x in problems)
    assert any("unknown requirement R-1" in x for x in problems)


def test_decompose_creates_subsystem_with_derived_requirements(make):
    p = make("plasma-thruster")
    shutil.rmtree(p.root / "elements/feed")
    folder = architecture.decompose(p, p.tree(), "feed")
    e = p.element("feed")
    assert folder == e.dir and (e.kind, e.prefix) == ("mechanical", "ME")
    derived = p.requirements(e)
    assert [(r.id, r.parent) for r in derived] == [("ME-001", "SYS-002")]
    with pytest.raises(ProjectError, match="already exists"):
        architecture.decompose(p, p.tree(), "feed")
