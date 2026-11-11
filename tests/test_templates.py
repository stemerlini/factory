"""The three shipped templates start clean: they load, check, and run what needs no tools."""

import shutil

import pytest

from factory import gates, runs, trace
from factory.cli import problems


@pytest.mark.parametrize("template", ["hardware", "software", "publication"])
def test_template_is_clean(make, template):
    p = make(template)
    assert problems(p) == []
    assert (p.root / ".claude/settings.json").is_file() and (p.root / "CLAUDE.md").is_file()
    g = {x.gate: x.status for x in gates.gates(p, p.tree())}
    assert g["describe"] == gates.DONE and g["architect"] == gates.DONE


def test_hardware_budgets_verify_mass_and_power(make):
    p = make("hardware")
    run = runs.execute(p, p.study("S001"))
    assert run.status == "done"
    assert run.metrics["mass"].value == pytest.approx(660.0)  # (150 + 400) × 1.2
    status = {t.requirement.id: t.status for t in trace.trace(p)}
    assert status["SYS-002"] == status["SYS-003"] == trace.PASS
    assert [e.kind for e in p.tree().children] == ["electronics", "software", "mechanical"]


def test_software_tests_run(make):
    p = make("software")
    run = runs.execute(p, p.study("S001"))
    assert run.status == "done", runs.run_dir(p, run).joinpath("log.txt").read_text()
    assert run.metrics["tests"].value == 1
    assert {t.requirement.id: t.status for t in trace.trace(p)}["SW-001"] == trace.PASS


def test_publication_counts_words_and_builds_when_latex_is_there(make):
    p = make("publication")
    assert p.tree().kind == "manuscript"
    assert runs.execute(p, p.study("S001")).status == "done"
    build = runs.execute(p, p.study("S002"))
    if shutil.which("latexmk") is None:
        assert build.status == "failed" and "latexmk" in build.error
    else:
        assert build.status == "done" and build.metrics["pages"].value >= 1
        assert (p.root / "out/main.pdf").is_file()


def test_tests_never_touch_the_real_config(tmp_path):
    from factory import config

    assert config.config_path() == tmp_path / "config/factory/config.toml"
