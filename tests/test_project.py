import pytest

from factory.project import Project, ProjectError, create, templates


def test_three_templates():
    assert set(templates()) == {"hardware", "software", "publication"}


def test_create_and_load_tree(make):
    p = make()
    assert p.id == "p" and "geant4" in p.modules and p.toolchains == {"geant4": "11.4.3"}
    assert (p.root / ".git").is_dir()
    tree = p.tree()
    assert (tree.path, tree.kind, tree.prefix) == ("", "system", "SYS")
    assert [(e.path, e.kind, e.prefix) for e in tree.children] == [
        ("fee", "electronics", "EL"), ("fsw", "software", "SW"),
        ("sensor-head", "mechanical", "ME")]
    assert p.element("fsw").skip == ("research", "concept")


def test_requirements_per_element(make):
    p = make()
    assert [r.id for r in p.requirements(p.element("fee"))] == ["EL-001"]
    req = p.requirement("SYS-002")
    assert req.element == "" and req.allocated == ("head",)
    assert str(req.criterion) == "G_geom >= 0.05 cm2 sr"
    assert p.requirement("ME-001").parent == "SYS-005"


def test_bad_criterion_and_gate_are_reported(make):
    p = make()
    (p.root / "req/SYS-009.md").write_text(
        "---\nid: SYS-009\ncriterion: {metric: x, op: '~', value: 1}\n---\n")
    with pytest.raises(ProjectError, match="SYS-009"):
        p.requirements()
    (p.root / "element.md").write_text("---\nskip: [lunch]\n---\n")
    with pytest.raises(ProjectError, match="lunch"):
        p.tree()


def test_study_keys(make):
    p = make()
    assert [s.key for s in p.studies()] == [
        "S001", "S002", "fee/S001", "fsw/S001", "sensor-head/S001"]
    s = p.study("fsw/S001")
    assert (s.element, s.gate, s.task) == ("fsw", "design", "software.pytest")


def test_load_finds_root_from_subfolder(make):
    p = make()
    assert Project.load(p.root / "elements/fee/req").root == p.root


def test_create_refuses_non_empty(tmp_path):
    (tmp_path / "x").mkdir()
    (tmp_path / "x/file").write_text("")
    with pytest.raises(ProjectError):
        create(tmp_path / "x")


def test_unknown_template(tmp_path):
    with pytest.raises(ProjectError, match="unknown template"):
        create(tmp_path / "y", "warp-drive")
