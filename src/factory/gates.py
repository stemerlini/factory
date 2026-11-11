"""The universal loop, computed from what is on disk.

Each element goes Describe → Research → Architect → Concept → Analyse → Lock → Design →
Deliver. A gate's status is derived from content (requirements, references, the
architecture, runs, tags, files), never ticked by hand; an element may skip gates.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import architecture, pipeline, trace
from .model import GATES, Element
from .project import Project, ProjectError

DONE, PARTIAL, TODO, SKIPPED, UNLOCKED = "done", "partial", "todo", "skipped", "unlocked"
MARKS = {DONE: "✓", PARTIAL: "◐", TODO: "○", SKIPPED: "–", UNLOCKED: "●"}
IGNORED = {"README.md", ".gitkeep"}


@dataclass
class GateState:
    gate: str
    title: str
    status: str
    detail: str = ""


def tag_prefix(element: Element) -> str:
    return f"lock/{element.path or 'system'}/"


def _files(folder) -> list:
    if not folder.is_dir():
        return []
    return [f for f in folder.rglob("*") if f.is_file() and f.name not in IGNORED]


def gates(project: Project, element: Element,
          states: dict[str, pipeline.StudyState] | None = None,
          traces: list[trace.Trace] | None = None) -> list[GateState]:
    states = states if states is not None else pipeline.states(project)
    traces = traces if traces is not None else trace.trace(project, states)
    mine = [t for t in traces if t.requirement.element == element.path]
    studies = [s for s in states.values() if s.study.element == element.path]
    out = []
    for gate, title in GATES:
        if gate in element.skip:
            out.append(GateState(gate, title, SKIPPED, "skipped"))
            continue
        status, detail = CHECKS[gate](project, element, mine, studies)
        out.append(GateState(gate, title, status, detail))
    return out


def current(states: list[GateState]) -> GateState | None:
    """The first gate that still needs work."""
    return next((g for g in states if g.status not in (DONE, SKIPPED)), None)


# --- one check per gate ----------------------------------------------------------------------


def _describe(project, element, traces, studies):
    has_text, n = bool(element.description.strip()), len(traces)
    if has_text and n:
        return DONE, f"{n} requirement(s)"
    if has_text or n:
        return PARTIAL, "needs a description" if n else "needs requirements"
    return TODO, "describe the element and write its requirements"


def _research(project, element, traces, studies):
    refs = len(element.references)
    vault = project.vault_dir(element)
    notes = len([f for f in _files(vault) if f.suffix == ".md"]) if vault else 0
    if refs or notes:
        return DONE, f"{refs} reference(s), {notes} note(s)"
    return TODO, "no references or notes" + ("" if vault else " (vault not configured)")


def _architect(project, element, traces, studies):
    try:
        arch = architecture.load(element.dir)
    except Exception as e:  # noqa: BLE001 - a broken file is a gate problem, not a crash
        return PARTIAL, f"architecture.yaml: {e}"
    if arch is None:
        return TODO, "no architecture.yaml"
    issues = arch.problems({t.requirement.id for t in traces})
    if issues:
        return PARTIAL, issues[0] + (f" (+{len(issues) - 1} more)" if len(issues) > 1 else "")
    # A requirement is allocated when blocks are named for it, or when derived requirements
    # already carry it into the subsystems.
    unallocated = [t.requirement.id for t in traces
                   if not t.requirement.allocated and t.requirement.id not in arch.allocate
                   and not t.children]
    if unallocated:
        return PARTIAL, f"not allocated: {', '.join(unallocated)}"
    return DONE, f"{len(arch.blocks)} blocks, {len(arch.interfaces)} interfaces"


def _concept(project, element, traces, studies):
    n = len(_files(element.dir / "concept"))
    return (DONE, f"{n} file(s)") if n else (TODO, "nothing in concept/")


def _analyse(project, element, traces, studies):
    relevant = [t for t in traces if t.requirement.method == "analysis"]
    if not relevant:
        ok = [s for s in studies if s.study.gate == "analyse"]
        if ok and all(s.status == pipeline.OK for s in ok):
            return DONE, f"{len(ok)} study(ies) up to date"
        return TODO, "no analysis requirements"
    counts = {k: sum(t.status == k for t in relevant) for k in trace.MARKS}
    if counts[trace.PASS] == len(relevant):
        return DONE, f"all {len(relevant)} analysis requirement(s) pass"
    detail = ", ".join(f"{n} {k}" for k, n in counts.items() if n and k != trace.PASS)
    return PARTIAL, f"{counts[trace.PASS]}/{len(relevant)} pass; {detail}"


def _lock(project, element, traces, studies):
    tag = latest_lock(project, element)
    if tag is None:
        return TODO, "not locked"
    if changed_since(project, element, tag):
        return UNLOCKED, f"changed since {tag}"
    return DONE, tag


def _design(project, element, traces, studies):
    return _files_and_studies(element, studies, "design", "nothing in design/")


def _deliver(project, element, traces, studies):
    return _files_and_studies(element, studies, "deliver", "nothing in out/", folder="out")


def _files_and_studies(element, studies, gate, empty, folder=None):
    files = _files(element.dir / (folder or gate))
    checks = [s for s in studies if s.study.gate == gate]
    bad = [s.study.id for s in checks if s.status != pipeline.OK]
    if not files and not checks:
        return TODO, empty
    if files and not bad:
        return DONE, f"{len(files)} file(s), {len(checks)} check(s) ok"
    return PARTIAL, f"checks not ok: {', '.join(bad)}" if bad else empty


CHECKS = {
    "describe": _describe,
    "research": _research,
    "architect": _architect,
    "concept": _concept,
    "analyse": _analyse,
    "lock": _lock,
    "design": _design,
    "deliver": _deliver,
}


# --- locking ---------------------------------------------------------------------------------


def latest_lock(project: Project, element: Element) -> str | None:
    tags = project.git("tag", "--list", tag_prefix(element) + "v*", "--sort=-v:refname")
    return tags.splitlines()[0] if tags else None


def _pathspec(project: Project, element: Element) -> list[str]:
    """The element's own content: not its children, not run records."""
    rel = element.dir.relative_to(project.root)
    base = "" if str(rel) == "." else f"{rel}/"
    return [f"{base or '.'}", f":(exclude,glob){base}elements/**",
            f":(exclude,glob){base}studies/*/runs/**"]


def changed_since(project: Project, element: Element, tag: str) -> bool:
    out = project.git("diff", "--name-only", tag, "--", *_pathspec(project, element))
    return bool(out)


def lock(project: Project, element: Element, force: bool = False) -> str:
    """Tag the element's current state as the next baseline."""
    head, _ = project.commit()
    if head is None:
        raise ProjectError("commit the project before locking (no commits yet)")
    if project.git("status", "--porcelain", "--", *_pathspec(project, element)):
        raise ProjectError(f"{element.title} has uncommitted changes; commit them first")
    states = gates(project, element)
    analyse = next(g for g in states if g.gate == "analyse")
    if analyse.status not in (DONE, SKIPPED) and not force:
        raise ProjectError(f"analysis not proven ({analyse.detail}); use --force to lock anyway")
    previous = latest_lock(project, element)
    n = int(previous.rsplit("v", 1)[-1]) + 1 if previous else 1
    tag = f"{tag_prefix(element)}v{n}"
    summary = "\n".join(f"{g.title}: {g.status} {g.detail}" for g in states)
    project.git_checked("tag", "-a", tag, "-m", f"Lock {element.title} v{n}\n\n{summary}")
    return tag
