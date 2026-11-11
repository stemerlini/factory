import subprocess

import pytest

from factory import gates, runs
from factory.project import ProjectError


def statuses(p, path=""):
    return {g.gate: g.status for g in gates.gates(p, p.element(path))}


def commit(p):
    subprocess.run(["git", "-C", str(p.root), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(p.root), "commit", "-qm", "x"], check=True)


def test_gates_are_computed(make):
    p = make()
    g = statuses(p)
    assert g["describe"] == gates.DONE
    assert g["research"] == gates.TODO
    assert g["architect"] == gates.DONE
    assert g["concept"] == gates.TODO  # only a README
    assert g["analyse"] == gates.PARTIAL
    assert statuses(p, "fsw")["research"] == gates.SKIPPED
    (p.root / "concept/sketch.md").write_text("# idea\n")
    assert statuses(p)["concept"] == gates.DONE


def test_research_counts_references(make):
    p = make("blank")
    (p.root / "element.md").write_text("---\nreferences: [2004_X_Verma]\n---\nA system.\n")
    assert statuses(p)["research"] == gates.DONE


def test_design_gate_needs_files_and_checks(make):
    p = make()
    assert statuses(p, "fsw")["design"] == gates.PARTIAL  # files, but tests never ran
    runs.execute(p, p.study("fsw/S001"))
    assert statuses(p, "fsw")["design"] == gates.DONE


def test_lock_and_unlock(make):
    p = make()
    runs.execute(p, p.study("sensor-head/S001"))
    head = p.element("sensor-head")
    with pytest.raises(ProjectError, match="no commits"):
        gates.lock(p, head)
    commit(p)
    tag = gates.lock(p, head)
    assert tag == "lock/sensor-head/v1"
    assert statuses(p, "sensor-head")["lock"] == gates.DONE
    # a change in a sibling element or a new run record does not unlock it
    (p.root / "elements/fee/element.md").write_text("---\ntitle: FEE\n---\nChanged.\n")
    runs.execute(p, p.study("sensor-head/S001"))
    commit(p)
    assert statuses(p, "sensor-head")["lock"] == gates.DONE
    mass = head.dir / "studies/S001-mass/study.toml"
    mass.write_text(mass.read_text().replace("60.0", "80.0"))
    commit(p)
    assert statuses(p, "sensor-head")["lock"] == gates.UNLOCKED
    with pytest.raises(ProjectError, match="analysis not proven"):
        gates.lock(p, head)  # the study is stale after the change
    assert gates.lock(p, head, force=True) == "lock/sensor-head/v2"
