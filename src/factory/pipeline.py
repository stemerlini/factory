"""Studies as a DAG: order, input resolution, fingerprints and staleness.

A study's fingerprint hashes everything that could change its result: the task and its
version, the toolchain version, the parameters, the files in the study folder, the input
files and the runs of upstream studies it consumes. A finished run whose fingerprint no
longer matches is stale; staleness flows downstream.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from . import runs
from .model import Run, Study
from .modules import registry
from .project import Project, ProjectError

OK, STALE, PENDING, RUNNING, FAILED = "ok", "stale", "pending", "running", "failed"
MARKS = {OK: "✓", STALE: "●", PENDING: "○", RUNNING: "⟳", FAILED: "✗"}


@dataclass
class StudyState:
    study: Study
    status: str
    fingerprint: str | None
    last: Run | None  # latest run of any status
    good: Run | None  # latest successful run
    reason: str = ""


def order(studies: list[Study]) -> list[Study]:
    """Topological order (upstream first); raises on unknown inputs or cycles."""
    by_id = {s.key: s for s in studies}
    done: list[Study] = []
    seen: set[str] = set()
    visiting: set[str] = set()

    def visit(s: Study) -> None:
        if s.key in seen:
            return
        if s.key in visiting:
            raise ProjectError(f"study dependency cycle through {s.key}")
        visiting.add(s.key)
        for up in s.upstream():
            if up not in by_id:
                raise ProjectError(f"{s.key} uses outputs of unknown study {up}")
            visit(by_id[up])
        visiting.discard(s.key)
        seen.add(s.key)
        done.append(s)

    for s in sorted(studies, key=lambda s: s.key):
        visit(s)
    return done


def downstream(studies: list[Study], key: str) -> list[str]:
    out, frontier = set(), {key}
    while frontier:
        frontier = {s.key for s in studies if set(s.upstream()) & frontier} - out
        out |= frontier
    return sorted(out)


def toolchain_version(project: Project, name: str | None) -> str | None:
    if not name:
        return None
    if name in project.toolchains:
        return project.toolchains[name]
    for mod in registry.all_modules().values():
        for tc in mod.toolchains:
            if tc.name == name and tc.versions():
                return tc.versions()[-1]
    return None


def input_path(project: Project, study: Study, ref: str) -> Path:
    """A file input: relative to the study's element, or to the project root if it starts
    with ``/``."""
    if ref.startswith("/"):
        return project.root / ref[1:]
    return study.dir.parent.parent / ref


def resolve_inputs(project: Project, study: Study) -> dict[str, Path]:
    """Input name → path. ``@S001:name`` is ``name`` in the latest good run of S001."""
    resolved = {}
    for name, ref in study.inputs.items():
        if ref.startswith("@"):
            up_ref, _, out = ref[1:].partition(":")
            up_key = study.resolve_key(up_ref)
            good = runs.latest(project, project.study(up_key), status="done")
            if good is None:
                raise ProjectError(
                    f"{study.key}: input {name!r} needs a successful run of {up_key}")
            resolved[name] = runs.run_dir(project, good) / "outputs" / out
        else:
            resolved[name] = input_path(project, study, ref)
    return resolved


def fingerprint(project: Project, study: Study) -> str:
    _, task = registry.find_task(study.task)
    h = hashlib.sha256()

    def add(label: str, value: str | bytes) -> None:
        h.update(label.encode() + b"\0")
        h.update(value if isinstance(value, bytes) else value.encode())
        h.update(b"\0")

    params = task.resolve_params(study.params)
    add("task", f"{study.task}@{task.version}")
    add("toolchain", str(toolchain_version(project, task.toolchain_for(params))))
    add("params", json.dumps(params, sort_keys=True, default=str))
    add("study", hash_tree(study.dir, skip=("runs",)))
    for name in sorted(study.inputs):
        ref = study.inputs[name]
        if ref.startswith("@"):
            up_key = study.resolve_key(ref[1:].split(":")[0])
            up = runs.latest(project, project.study(up_key), status="done")
            add(f"in:{name}", f"run:{up.id if up else 'none'}")
        else:
            add(f"in:{name}", hash_tree(input_path(project, study, ref)))
    return h.hexdigest()[:16]


def hash_tree(path: Path, skip: tuple[str, ...] = ()) -> str:
    h = hashlib.sha256()
    if path.is_file():
        h.update(path.read_bytes())
    elif path.is_dir():
        for f in sorted(path.rglob("*")):
            rel = f.relative_to(path)
            if f.is_file() and not (rel.parts[0] in skip or "__pycache__" in rel.parts):
                h.update(str(rel).encode() + b"\0" + f.read_bytes() + b"\0")
    else:
        h.update(b"missing")
    return h.hexdigest()


def states(project: Project) -> dict[str, StudyState]:  # keyed by study key
    """Status of every study, upstream first, with staleness propagated downstream."""
    found: dict[str, StudyState] = {}
    for s in order(project.studies()):
        history = runs.history(project, s)
        last = history[-1] if history else None
        good = next((r for r in reversed(history) if r.status == "done"), None)
        try:
            fp = fingerprint(project, s)
        except (LookupError, ProjectError) as e:
            found[s.key] = StudyState(s, FAILED, None, last, good, str(e))
            continue
        bad_up = [u for u in s.upstream() if found[u].status != OK]
        if last and last.status in ("queued", "running"):
            st = StudyState(s, RUNNING, fp, last, good)
        elif last and last.status == "failed":
            st = StudyState(s, FAILED, fp, last, good, f"run {last.id} failed")
        elif good is None:
            st = StudyState(s, PENDING, fp, last, good, "never ran")
        elif bad_up:
            st = StudyState(s, STALE, fp, last, good, f"upstream {', '.join(bad_up)} not ok")
        elif good.fingerprint != fp:
            st = StudyState(s, STALE, fp, last, good, "inputs changed since the last run")
        else:
            st = StudyState(s, OK, fp, last, good)
        found[s.key] = st
    return found
