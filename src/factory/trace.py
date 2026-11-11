"""Requirement verification: which studies verify what, do their metrics pass, and how
child requirements roll up into their parents (flow-down)."""

from __future__ import annotations

from dataclasses import dataclass, field

from . import pipeline
from .model import Metric, Requirement
from .project import Project

OPEN, PENDING, STALE, PASS, FAIL = "open", "pending", "stale", "pass", "fail"
MARKS = {OPEN: "○", PENDING: "◌", STALE: "●", PASS: "✓", FAIL: "✗"}
# When several results combine, the worst one wins.
SEVERITY = (FAIL, STALE, PENDING, OPEN, PASS)


@dataclass
class Trace:
    requirement: Requirement
    status: str
    studies: list[str] = field(default_factory=list)  # study keys verifying it directly
    children: list[str] = field(default_factory=list)  # requirement ids derived from it
    value: Metric | None = None
    reason: str = ""


def trace(project: Project, states: dict[str, pipeline.StudyState] | None = None) -> list[Trace]:
    """Every requirement's status, in project order (parents before children)."""
    states = states if states is not None else pipeline.states(project)
    requirements = project.requirements()
    verifiers: dict[str, list[str]] = {}
    for st in states.values():
        for req_id in st.study.verifies:
            verifiers.setdefault(req_id, []).append(st.study.key)
    children: dict[str, list[str]] = {}
    for r in requirements:
        if r.parent:
            children.setdefault(r.parent, []).append(r.id)

    by_id = {r.id: r for r in requirements}
    done: dict[str, Trace] = {}

    def solve(req_id: str, path: tuple[str, ...] = ()) -> Trace:
        if req_id in done:
            return done[req_id]
        r = by_id[req_id]
        if req_id in path:  # a parent loop: report it instead of recursing forever
            return Trace(r, FAIL, reason=f"parent loop through {req_id}")
        t = _direct(r, verifiers.get(req_id, []), states)
        kids = [k for k in children.get(req_id, []) if k in by_id]
        t.children = kids
        if kids:
            rolled = [solve(k, (*path, req_id)) for k in kids]
            worst = min((c.status for c in rolled), key=SEVERITY.index)
            if t.status == OPEN:
                t.status = worst
                t.reason = f"from {len(kids)} derived requirement(s)" + (
                    "" if worst == PASS else f": {_names(rolled, worst)} {worst}")
            elif worst in (FAIL, STALE) and SEVERITY.index(worst) < SEVERITY.index(t.status):
                # A requirement verified at its own level is only pulled down by derived
                # requirements that fail or went stale, not by ones not yet verified.
                t.status = worst
                t.reason += f"; derived {_names(rolled, worst)} {worst}"
            elif worst != PASS:
                t.reason += f"; derived {_names(rolled, worst)} {worst}"
        done[req_id] = t
        return t

    return [solve(r.id) for r in requirements]


def _names(traces: list[Trace], status: str) -> str:
    return ", ".join(t.requirement.id for t in traces if t.status == status)


def _direct(req: Requirement, keys: list[str], states: dict[str, pipeline.StudyState]) -> Trace:
    if not keys:
        return Trace(req, OPEN, reason="no study verifies it")
    sts = [states[k] for k in keys]
    if req.criterion is None:
        if all(s.status == pipeline.OK for s in sts):
            return Trace(req, PASS, keys, reason="verifying studies are up to date")
        if all(s.good for s in sts):
            return Trace(req, STALE, keys, reason="a verifying study is stale")
        return Trace(req, PENDING, keys, reason="a verifying study has not run")

    name = req.criterion.metric
    with_metric = [s for s in sts if s.good and name in s.good.metrics]
    if not with_metric:
        if any(s.good for s in sts):
            return Trace(req, PENDING, keys, reason=f"no run reports metric {name!r}")
        return Trace(req, PENDING, keys, reason="verifying studies have not run")
    st = with_metric[0]
    value = st.good.metrics[name]
    if req.criterion.unit and value.unit and value.unit != req.criterion.unit:
        return Trace(req, FAIL, keys, value=value,
                     reason=f"unit mismatch: {value.unit!r} vs {req.criterion.unit!r}")
    ok = req.criterion.check(value.value)
    if st.status != pipeline.OK:
        return Trace(req, STALE, keys, value=value,
                     reason=f"{'passed' if ok else 'failed'} on a stale run of {st.study.key}")
    return Trace(req, PASS if ok else FAIL, keys, value=value,
                 reason=f"{value.value:g} {value.unit} from {st.study.key}".strip())


def problems(project: Project) -> list[str]:
    """Broken references: unknown verified requirements, unknown parents."""
    reqs = project.requirements()
    known = {r.id for r in reqs}
    found = [f"{s.key} verifies unknown requirement {r}"
             for s in project.studies() for r in s.verifies if r not in known]
    found += [f"{r.id}: unknown parent {r.parent}"
              for r in reqs if r.parent and r.parent not in known]
    ids = [r.id for r in reqs]
    found += [f"duplicate requirement id {i}" for i in sorted({i for i in ids if ids.count(i) > 1})]
    return found
