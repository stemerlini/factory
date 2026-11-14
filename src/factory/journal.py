"""The project's story on one timeline.

Most of it is already on disk: approvals are commits, baselines are lock tags, decisions
carry their dates and runs their times. What git does not see (agent sessions, discarded
changes, gates passed) goes to ``.factory/journal.jsonl``. Gate passes are found by
comparing the computed gates with the last snapshot in ``.factory/gates.json``.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import asdict, dataclass

from . import decisions, gates, review, runs
from .project import Project, ProjectError

FILE = ".factory/journal.jsonl"
SNAPSHOT = ".factory/gates.json"
# What each kind of entry looks like, for text and the TUI.
MARKS = {"approve": "●", "lock": "◆", "decision": "?", "decided": "✓", "run": "▶",
         "failed": "✗", "agent": "◇", "reject": "↺", "gate": "★", "note": "·", "request": "⇢"}


@dataclass
class Event:
    time: str  # ISO 8601, local time zone
    kind: str
    title: str
    element: str = ""
    detail: str = ""

    @property
    def when(self) -> dt.datetime:
        return parse(self.time)

    @property
    def ago(self) -> str:
        return relative(self.when, day_only=len(self.time) == 10)


def parse(text: str) -> dt.datetime:
    """An ISO time or date; a date alone is midnight local time. Unreadable sorts first."""
    try:
        return dt.datetime.fromisoformat(text).astimezone()
    except ValueError:
        return dt.datetime.min.replace(tzinfo=dt.UTC)


def now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def record(project: Project, kind: str, title: str, element: str = "",
           detail: str = "") -> Event:
    """Add an entry that git does not keep by itself."""
    event = Event(now(), kind, title, element, detail)
    path = project.root / FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(event), ensure_ascii=False) + "\n")
    return event


def _recorded(project: Project) -> list[Event]:
    path = project.root / FILE
    found = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        try:
            found.append(Event(**json.loads(line)))
        except (ValueError, TypeError):
            continue
    return found


def _commits(project: Project, elements) -> list[Event]:
    out = project.git("log", "-n", "300", "--name-only", "--format=\x1e%aI\x1f%h\x1f%s")
    found = []
    for chunk in (out or "").split("\x1e"):
        head, _, files = chunk.strip("\n").partition("\n")
        parts = head.split("\x1f")
        if len(parts) != 3:
            continue
        when, short, subject = parts
        owners = {review.owner(project, elements, f) for f in files.splitlines() if f}
        found.append(Event(when, "approve", subject,
                           owners.pop() if len(owners) == 1 else "", short))
    return found


def _locks(project: Project) -> list[Event]:
    out = project.git("for-each-ref", "refs/tags/lock",
                      "--format=%(refname:short)\x1f%(creatordate:iso-strict)")
    found = []
    for line in (out or "").splitlines():
        tag, _, when = line.partition("\x1f")
        element = tag.split("/", 1)[1].rsplit("/", 1)[0] if tag.count("/") >= 2 else ""
        element = "" if element == "system" else element
        found.append(Event(when, "lock", f"Locked {element or 'the system'} "
                           f"{tag.rsplit('/', 1)[-1]}", element, tag))
    return found


def _decisions(project: Project) -> list[Event]:
    found = []
    for d in decisions.all_decisions(project):
        if d.date:
            found.append(Event(str(d.date), "decision", f"{d.key} asked: {d.title}", d.element,
                               d.raised_by))
        if d.decided and d.status != "proposed":
            option = d.option(d.chosen)
            found.append(Event(str(d.decided), "decided", f"{d.key} {d.status}: " + (
                option.title if option else d.title), d.element))
    return found


def _runs(project: Project) -> list[Event]:
    found = []
    for study in project.studies():
        for run in runs.history(project, study):
            if run.status not in ("done", "failed"):
                continue
            metrics = ", ".join(f"{k} {m.value:.4g}{(' ' + m.unit) if m.unit else ''}"
                                for k, m in list(run.metrics.items())[:3])
            found.append(Event(run.finished or run.started,
                               "run" if run.status == "done" else "failed",
                               f"{study.key} {study.title}: {run.status}", study.element,
                               metrics or run.error))
    return found


def events(project: Project, since: dt.datetime | None = None,
           element: str | None = None, limit: int | None = None) -> list[Event]:
    """Everything that happened, newest first."""
    try:
        elements = sorted(project.elements(), key=lambda e: len(e.dir.parts), reverse=True)
        found = (_recorded(project) + _commits(project, elements) + _locks(project)
                 + _decisions(project) + _runs(project))
    except ProjectError:
        found = _recorded(project)
    if element is not None:
        found = [e for e in found if e.element == element.strip("/")]
    if since is not None:
        found = [e for e in found if e.when >= since]
    found.sort(key=lambda e: e.when, reverse=True)
    return found[:limit] if limit else found


def streak(found: list[Event], today: dt.date | None = None) -> int:
    """Days in a row with something done, ending today (or yesterday, if today is young)."""
    days = {e.when.astimezone().date() for e in found if e.when.year > 1}
    day = today or dt.date.today()
    if day not in days:
        day -= dt.timedelta(days=1)
    n = 0
    while day in days:
        n += 1
        day -= dt.timedelta(days=1)
    return n


def passed_gates(project: Project, states: dict[str, list[gates.GateState]],
                 titles: dict[str, str]) -> list[Event]:
    """Record gates that became done since the last look. The first look only remembers."""
    path = project.root / SNAPSHOT
    current = {p: {g.gate: g.status for g in gs} for p, gs in states.items()}
    try:
        before = json.loads(path.read_text())
    except (OSError, ValueError):
        before = None
    if before == current:
        return []
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(current))
    tmp.replace(path)
    if before is None:
        return []
    passed = []
    for p, gs in states.items():
        old = before.get(p)
        if old is None:
            continue
        for g in gs:
            if g.status == gates.DONE and old.get(g.gate) not in (gates.DONE, None):
                passed.append(record(project, "gate", f"{titles.get(p, p or 'System')} passed "
                                     f"{g.title}", p, g.detail))
    return passed


def markdown(project: Project, found: list[Event]) -> str:
    """A handover note: the timeline grouped by day."""
    lines = [f"# {project.name}: journal", ""]
    day = None
    for e in found:
        d = e.when.astimezone().date() if e.when.year > 1 else None
        if d != day:
            day = d
            lines += ["", f"## {d.isoformat() if d else 'Undated'}", ""]
        where = f" ({e.element})" if e.element else ""
        lines.append(f"- {MARKS.get(e.kind, '·')} {e.title}{where}"
                     + (f": {e.detail}" if e.detail else ""))
    return "\n".join(lines).replace("\n\n\n", "\n\n").strip() + "\n"


def relative(when: dt.datetime, now_: dt.datetime | None = None, day_only: bool = False) -> str:
    """'just now', '5 min ago', '3 h ago', 'today', 'yesterday', '12 Nov'."""
    if when.year <= 1:
        return ""
    now_ = now_ or dt.datetime.now().astimezone()
    seconds = (now_ - when).total_seconds()
    if not day_only and seconds < 3600:
        return "just now" if seconds < 60 else f"{int(seconds // 60)} min ago"
    if when.date() == now_.date():
        return "today" if day_only else f"{int(seconds // 3600)} h ago"
    if when.date() == now_.date() - dt.timedelta(days=1):
        return "yesterday"
    return when.strftime("%-d %b")
