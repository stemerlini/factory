"""Guided gates: each gate as a short checklist, computed from disk like the gates.

A gate's status says *whether* it is done; its checklist says *what* is left, item by
item, and what to do about each one. Every item has an action for the human (open the
right file or dialog) and a playbook for an agent: a short Markdown recipe per gate,
shipped in ``factory/playbooks/`` and overridable per project in ``playbooks/<gate>.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files

from . import architecture, gates, pipeline, review, trace
from .model import Element
from .project import Project, ProjectError

# Actions the TUI knows how to do for the human.
#   edit:<file>   open the element's file in the editor (created from a stub if missing)
#   new:req       a new requirement stub        new:study   the New Study dialog
#   tab:<tab>     switch to a tab               lock        lock the element
#   open:<dir>    open a folder of the element  vault       open the element's vault folder


@dataclass
class Check:
    gate: str
    label: str
    done: bool
    action: str
    detail: str = ""


def checklist(project: Project, element: Element,
              states: dict[str, pipeline.StudyState] | None = None,
              traces: list[trace.Trace] | None = None) -> dict[str, list[Check]]:
    """Every gate's checks for one element. Skipped gates have none."""
    states = states if states is not None else pipeline.states(project)
    traces = traces if traces is not None else trace.trace(project, states)
    mine = [t for t in traces if t.requirement.element == element.path]
    studies = [s for s in states.values() if s.study.element == element.path]
    out: dict[str, list[Check]] = {}
    for gate, fn in BUILDERS.items():
        if gate in element.skip:
            out[gate] = []
            continue
        out[gate] = [Check(gate, *c) for c in fn(project, element, mine, studies)]
    return out


def next_check(checks: dict[str, list[Check]],
               gate_states: list[gates.GateState]) -> Check | None:
    """The first open item of the first gate that still needs work."""
    cur = gates.current(gate_states)
    if cur is None:
        return None
    return next((c for c in checks.get(cur.gate, []) if not c.done), None)


def playbook(project: Project, gate: str) -> str:
    """How to run a gate: the project's own recipe if it has one, else Factory's."""
    own = project.root / "playbooks" / f"{gate}.md"
    if own.is_file():
        return own.read_text()
    shipped = files("factory.playbooks") / f"{gate}.md"
    return shipped.read_text() if shipped.is_file() else ""


def _ids(items: list[str], n: int = 4) -> str:
    return ", ".join(items[:n]) + (f" (+{len(items) - n})" if len(items) > n else "")


# --- one builder per gate: (label, done, action, detail) -----------------------------------


def _describe(project, element, traces, studies):
    analysis = [t for t in traces if t.requirement.method == "analysis"]
    no_criterion = [t.requirement.id for t in analysis if t.requirement.criterion is None]
    yield ("Say what it is and what it does", bool(element.description.strip()),
           "edit:element.md", "")
    yield ("Write its requirements", bool(traces), "new:req",
           f"{len(traces)} so far" if traces else "")
    yield ("Give each analysed requirement a pass/fail criterion",
           bool(analysis) and not no_criterion, "tab:requirements",
           f"missing: {_ids(no_criterion)}" if no_criterion else "")
    if element.path:
        orphans = [t.requirement.id for t in traces if not t.requirement.parent]
        yield ("Trace each requirement to its parent's", bool(traces) and not orphans,
               "tab:requirements", f"no parent: {_ids(orphans)}" if orphans else "")


def _research(project, element, traces, studies):
    vault = project.vault_dir(element)
    notes = [f for f in gates._files(vault) if f.suffix == ".md"] if vault else []
    yield ("Cite the papers and standards it builds on", bool(element.references),
           "edit:element.md", f"{len(element.references)} reference(s)"
           if element.references else "Citadel keys under references:")
    yield ("Keep research notes in the vault", bool(notes), "vault",
           f"{len(notes)} note(s)" if vault else "vault not configured")


def _architect(project, element, traces, studies):
    try:
        arch = architecture.load(element.dir)
        error = ""
    except Exception as e:  # noqa: BLE001 - shown as an item, like the gate does
        arch, error = None, str(e)
    yield ("Draw the blocks and their interfaces", arch is not None,
           f"edit:{architecture.FILE}", error)
    issues = arch.problems({t.requirement.id for t in traces}) if arch else []
    yield ("Fix what the architecture check finds", arch is not None and not issues,
           f"edit:{architecture.FILE}", issues[0] if issues else "")
    loose = [t.requirement.id for t in traces if not t.requirement.allocated
             and not (arch and t.requirement.id in arch.allocate) and not t.children]
    yield ("Allocate every requirement to a block", arch is not None and not loose,
           "tab:architecture", f"not allocated: {_ids(loose)}" if loose else "")


def _concept(project, element, traces, studies):
    n = len(gates._files(element.dir / "concept"))
    yield ("Sketch the concept: principle, options, the choice and why", bool(n),
           "edit:concept/concept.md", f"{n} file(s)" if n else "")


def _analyse(project, element, traces, studies):
    analysis = [t for t in traces if t.requirement.method == "analysis"]
    unplanned = [t.requirement.id for t in analysis if not t.studies and not t.children]
    yield ("Plan a study for each analysed requirement", bool(analysis) and not unplanned,
           "new:study", f"no study: {_ids(unplanned)}" if unplanned else "")
    mine = [s for s in studies if s.study.gate == "analyse"]
    behind = [s.study.id for s in mine if s.status != pipeline.OK]
    yield ("Run the studies and keep them up to date", bool(mine) and not behind,
           "tab:studies", f"to run: {_ids(behind)}" if behind else "")
    failing = [t.requirement.id for t in analysis if t.status != trace.PASS]
    yield ("Make every analysed requirement pass", bool(analysis) and not failing,
           "tab:requirements", f"not passing: {_ids(failing)}" if failing else "")


def _lock(project, element, traces, studies):
    pending = [c.path for c in review.changes(project) if c.element == element.path]
    yield ("Approve or discard pending changes", not pending, "tab:review",
           f"{len(pending)} pending" if pending else "")
    try:
        tag = gates.latest_lock(project, element)
    except ProjectError:
        tag = None
    fresh = tag is not None and not gates.changed_since(project, element, tag)
    yield ("Lock a baseline", fresh, "lock", tag or "")


def _made(gate, folder, label, check_label):
    def build(project, element, traces, studies):
        n = len(gates._files(element.dir / folder))
        yield (label, bool(n), f"open:{folder}", f"{n} file(s)" if n else "")
        mine = [s for s in studies if s.study.gate == gate]
        bad = [s.study.id for s in mine if s.status != pipeline.OK]
        yield (check_label, bool(mine) and not bad, "new:study" if not mine else "tab:studies",
               f"not ok: {_ids(bad)}" if bad else "" if mine else "no check yet")
    return build


BUILDERS = {
    "describe": _describe,
    "research": _research,
    "architect": _architect,
    "concept": _concept,
    "analyse": _analyse,
    "lock": _lock,
    "design": _made("design", "design", "Make the design (schematics, CAD, code) in design/",
                    "Check the design with a study (ERC/DRC, FEA, tests)"),
    "deliver": _made("deliver", "out", "Produce the deliverables in out/ (BOM, drawings, "
                     "release)", "Check the deliverables with a study"),
}

# Stubs for files an action creates before opening the editor.
STUBS = {
    "concept/concept.md": "# Concept\n\n## Principle\n\n## Options\n\n## Choice and why\n",
    architecture.FILE: "blocks:\n  - {id: a, title: A}\n  - {id: b, title: B}\n"
                       "interfaces:\n  - {from: a, to: b, type: signal, label: data}\n",
}


def new_requirement(project: Project, element: Element) -> str:
    """Write the next requirement stub (``req/EL-004.md``); returns its path, relative."""
    folder = element.dir / "req"
    folder.mkdir(exist_ok=True)
    numbers = [int(r.id.rsplit("-", 1)[-1]) for r in project.requirements(element)
               if r.id.rsplit("-", 1)[-1].isdigit()]
    rid = f"{element.prefix}-{max(numbers, default=0) + 1:03d}"
    path = folder / f"{rid}.md"
    path.write_text(f"---\nid: {rid}\ntitle: \nmethod: analysis\nstatus: draft\n"
                    "# criterion: {metric: name, op: \">=\", value: 0, unit: \"\"}\n---\n"
                    "The element shall …\n")
    return str(path.relative_to(project.root))
