import pytest

from factory import pipeline, runs, trace
from factory.project import ProjectError, write_study


def by_id(p):
    return {t.requirement.id: t for t in trace.trace(p)}


def test_order_and_cycles(make):
    p = make("blank")
    write_study(p.root / "studies/S001-a", {"task": "script", "inputs": {"x": "@S002:out"}})
    write_study(p.root / "studies/S002-b", {"task": "script"})
    assert [s.key for s in pipeline.order(p.studies())] == ["S002", "S001"]
    assert pipeline.downstream(p.studies(), "S002") == ["S001"]
    write_study(p.root / "studies/S002-b", {"task": "script", "inputs": {"y": "@S001:out"}})
    with pytest.raises(ProjectError, match="cycle"):
        pipeline.order(p.studies())


def test_run_metrics_trace_and_staleness(make):
    p = make()
    assert pipeline.states(p)["S001"].status == pipeline.PENDING
    run = runs.execute(p, p.study("S001"))
    assert run.status == "done", runs.run_dir(p, run).joinpath("log.txt").read_text()
    assert run.metrics["G_geom"].unit == "cm2 sr"
    assert pipeline.states(p)["S001"].status == pipeline.OK
    assert by_id(p)["SYS-002"].status == trace.PASS
    assert by_id(p)["SYS-003"].status == trace.PENDING

    stack = p.root / "geometry/stack.yaml"
    stack.write_text(stack.read_text().replace("gap_mm: 20.0", "gap_mm: 500.0"))
    st = pipeline.states(p)
    assert st["S001"].status == pipeline.STALE
    assert st["sensor-head/S001"].status == pipeline.PENDING  # it never ran
    assert by_id(p)["SYS-002"].status == trace.STALE
    runs.execute(p, p.study("S001"))
    assert by_id(p)["SYS-002"].status == trace.FAIL


def test_cross_element_inputs_and_root_paths(make):
    p = make()
    s = p.study("sensor-head/S001")
    assert s.inputs["stack"] == "/geometry/stack.yaml"
    assert pipeline.resolve_inputs(p, s)["stack"] == p.root / "geometry/stack.yaml"
    assert runs.execute(p, s).metrics["mass"].unit == "g"
    write_study(p.root / "elements/fee/studies/S002-x",
                {"task": "script", "inputs": {"g": "@/S001:g", "m": "@sensor-head/S001:m"}})
    assert p.study("fee/S002").upstream() == ["S001", "sensor-head/S001"]


def test_staleness_flows_downstream(make):
    p = make("blank")
    d = p.root / "studies"
    write_study(d / "S001-a", {"task": "script", "params": {"file": "a.py"}})
    (d / "S001-a/a.py").write_text(
        "import os,pathlib\npathlib.Path(os.environ['FACTORY_OUTPUTS'],'v').write_text('1')\n")
    write_study(d / "S002-b", {"task": "script", "params": {"file": "b.py"},
                               "inputs": {"v": "@S001:v"}})
    (d / "S002-b/b.py").write_text(
        "import json,os\nprint(open(json.loads(os.environ['FACTORY_INPUTS'])['v']).read())\n")
    for key in ("S001", "S002"):
        assert runs.execute(p, p.study(key)).status == "done"
    assert {k: v.status for k, v in pipeline.states(p).items()} == {"S001": "ok", "S002": "ok"}
    (d / "S001-a/a.py").write_text((d / "S001-a/a.py").read_text() + "# changed\n")
    st = pipeline.states(p)
    assert st["S001"].status == st["S002"].status == pipeline.STALE
    assert "upstream" in st["S002"].reason


def test_failed_and_missing_tools(make):
    p = make("blank")
    write_study(p.root / "studies/S001-x", {"task": "script", "params": {"file": "x.py"}})
    (p.root / "studies/S001-x/x.py").write_text("raise SystemExit(3)\n")
    run = runs.execute(p, p.study("S001"))
    assert (run.status, run.returncode) == ("failed", 3)
    assert pipeline.states(p)["S001"].status == pipeline.FAILED
    t = make("plasma-thruster", "t")
    assert "warpx" in runs.execute(t, t.study("S002")).error


def test_flow_down_rolls_up(make):
    p = make()
    assert by_id(p)["SYS-005"].status == trace.PENDING  # ME-001 has not run
    runs.execute(p, p.study("sensor-head/S001"))
    t = by_id(p)["SYS-005"]
    assert (t.status, t.children) == (trace.PASS, ["ME-001"])
    # SYS-001 waits on SYS-003 (Geant4) and SYS-004 → EL-001 (ngspice)
    assert by_id(p)["SYS-001"].status == trace.PENDING
    assert trace.problems(p) == []


def test_software_tests_are_metrics(make):
    p = make()
    run = runs.execute(p, p.study("fsw/S001"))
    assert run.status == "done", runs.run_dir(p, run).joinpath("log.txt").read_text()
    assert run.metrics["tests"].value == 1 and run.metrics["tests_failed"].value == 0
    assert by_id(p)["SW-001"].status == trace.PASS


def test_failing_derived_requirement_fails_the_parent(make):
    p = make()
    me = p.root / "elements/sensor-head/req/ME-001.md"
    me.write_text(me.read_text().replace("value: 300", "value: 50"))
    runs.execute(p, p.study("sensor-head/S001"))
    assert by_id(p)["ME-001"].status == trace.FAIL
    assert by_id(p)["SYS-005"].status == trace.FAIL
    assert "ME-001" in by_id(p)["SYS-005"].reason
