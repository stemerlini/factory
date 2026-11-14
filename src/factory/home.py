"""What now? One summary of the project for the Home tab and ``factory home``.

It answers three questions: what is waiting on the human (review, decisions, broken
studies), what each element should do next, and how far the project has come.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import decisions, gates, guide, journal, pipeline, requests, review, trace
from .model import Element
from .project import Project


@dataclass
class Item:
    """Something waiting on the human. ``target`` says where Enter goes."""

    # review | decision | failed | stale | unlocked | problem | request | delivered | outcome
    kind: str
    text: str
    element: str = ""
    target: str = ""


@dataclass
class Step:
    element: str
    title: str
    gate: str
    gate_title: str
    detail: str


@dataclass
class Row:
    element: str
    title: str
    depth: int
    statuses: list[str]


@dataclass
class Home:
    waiting: list[Item] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    journey: list[Row] = field(default_factory=list)
    progress: float = 0.0
    recent: list[journal.Event] = field(default_factory=list)
    streak: int = 0


def build(project: Project, elements: list[Element],
          states: dict[str, pipeline.StudyState],
          gate_states: dict[str, list[gates.GateState]], recent: int = 8,
          checks: dict[str, dict[str, list[guide.Check]]] | None = None) -> Home:
    h = Home()
    pending = review.changes(project)
    if pending:
        title = review.title(project)
        h.waiting.append(Item("review", f"{len(pending)} change(s) to review"
                              + (f": {title}" if title else ""), "", "review"))
    for d in decisions.all_decisions(project):
        if d.status == "proposed":
            h.waiting.append(Item("decision", f"Decide {d.key}: {d.title}", d.element, d.key))
    for s in states.values():
        if s.status == pipeline.FAILED:
            h.waiting.append(Item("failed", f"{s.study.key} {s.study.title} failed",
                                  s.study.element, s.study.key))
        elif s.status == pipeline.STALE:
            h.waiting.append(Item("stale", f"{s.study.key} {s.study.title} is stale: {s.reason}",
                                  s.study.element, s.study.key))
    for e in elements:
        for g in gate_states.get(e.path, []):
            if g.status == gates.UNLOCKED:
                h.waiting.append(Item("unlocked", f"{e.title} {g.detail}", e.path, "lock"))
    h.waiting += [Item("problem", p) for p in trace.problems(project)]
    h.waiting += requests_waiting(project)

    done = total = 0
    for e in elements:
        gs = gate_states.get(e.path, [])
        cur = gates.current(gs)
        if cur:
            todo = guide.next_check(checks[e.path], gs) if checks and e.path in checks else None
            detail = (todo.label + (f" ({todo.detail})" if todo.detail else "")) if todo \
                else cur.detail
            h.steps.append(Step(e.path, e.title, cur.gate, cur.title, detail))
        h.journey.append(Row(e.path, e.title, e.path.count("/") + (1 if e.path else 0),
                             [g.status for g in gs]))
        done += sum(g.status == gates.DONE for g in gs)
        total += sum(g.status != gates.SKIPPED for g in gs)
    h.progress = done / total if total else 0.0
    found = journal.events(project)
    h.recent = found[:recent]
    h.streak = journal.streak(found)
    return h


def requests_waiting(project: Project) -> list[Item]:
    """This project's requests to its tools that need the user: to send one to a
    development session, to accept or reject a delivered change, or to tell the agent how
    one ended (pick it up, or find another way)."""
    items = []
    for r in requests.all_requests(everything=True):
        if r.root != str(project.root):
            continue
        if r.status == requests.OPEN:
            items.append(Item("request", f"Send {r.id} to {r.to}: {r.title}", r.element, r.id))
        elif r.status == requests.DELIVERED:
            items.append(Item("delivered", f"Accept or reject {r.id} from {r.to}: {r.title}",
                              r.element, r.id))
        elif r.status in requests.ENDED and not r.told:
            then = ("tell the agent to pick it up" if r.status == requests.ACCEPTED else
                    "tell the agent to find another way")
            items.append(Item("outcome", f"{r.id} {r.status}: {then}", r.element, r.id))
    return items


def load(project: Project) -> tuple[Home, dict[str, list[gates.GateState]]]:
    """Compute everything from disk (for the CLI)."""
    states = pipeline.states(project)
    traces = trace.trace(project, states)
    elements = project.elements()
    gs = {e.path: gates.gates(project, e, states, traces) for e in elements}
    checks = {e.path: guide.checklist(project, e, states, traces) for e in elements}
    journal.passed_gates(project, gs, {e.path: e.title for e in elements})
    return build(project, elements, states, gs, checks=checks), gs


def journey_bar(statuses: list[str]) -> str:
    """■ done, ◐ partial, □ to do, · skipped, ◆ changed since its lock."""
    return "".join({gates.DONE: "■", gates.PARTIAL: "◐", gates.SKIPPED: "·",
                    gates.UNLOCKED: "◆"}.get(s, "□") for s in statuses)
