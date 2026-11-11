"""Decision records: the questions a design raises and the answers the human gives.

An agent (or the user) *asks* by writing a proposed decision with options and a
recommendation; only the human *decides* (``factory decide``, or the Review tab), which
makes it accepted or rejected. Accepted decisions bind later work.

``decisions/D001-slug.md`` in an element's folder, frontmatter + context in the body.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .model import Element
from .project import Project, ProjectError, read_frontmatter, slug

STATUSES = ("proposed", "accepted", "rejected")
FILE = re.compile(r"^(D\d{3,})(?:-.*)?\.md$")


@dataclass
class Option:
    id: str
    title: str
    summary: str = ""


@dataclass
class Decision:
    id: str
    title: str
    element: str
    status: str = "proposed"
    options: list[Option] = field(default_factory=list)
    recommended: str | None = None
    chosen: str | None = None
    raised_by: str = "user"
    date: str = ""
    decided: str | None = None
    verdict: str = ""  # the human's note when deciding
    context: str = ""
    path: Path | None = None

    @property
    def key(self) -> str:
        return f"{self.element}/{self.id}" if self.element else self.id

    def option(self, option_id: str | None) -> Option | None:
        return next((o for o in self.options if o.id == option_id), None)


def folder(element: Element) -> Path:
    return element.dir / "decisions"


def read(path: Path, element: str = "") -> Decision:
    meta, body = read_frontmatter(path)
    m = FILE.match(path.name)
    status = meta.get("status", "proposed")
    if status not in STATUSES:
        raise ProjectError(f"{path}: status must be one of {', '.join(STATUSES)}")
    options = [Option(str(o.get("id")), str(o.get("title", o.get("id"))), o.get("summary", ""))
               for o in meta.get("options") or []]
    context, _, verdict = body.partition("\n## Decision\n")
    return Decision(
        id=str(meta.get("id") or (m.group(1) if m else path.stem)),
        title=str(meta.get("title", "")),
        element=element,
        status=status,
        options=options,
        recommended=meta.get("recommended"),
        chosen=meta.get("chosen"),
        raised_by=str(meta.get("raised_by", "user")),
        date=str(meta.get("date", "")),
        decided=meta.get("decided"),
        verdict=verdict.strip(),
        context=context.strip(),
        path=path,
    )


def write(d: Decision) -> Path:
    meta: dict[str, Any] = {
        "id": d.id, "title": d.title, "status": d.status, "raised_by": d.raised_by,
        "date": d.date,
        "options": [{"id": o.id, "title": o.title, **({"summary": o.summary} if o.summary else {})}
                    for o in d.options],
        "recommended": d.recommended, "chosen": d.chosen, "decided": d.decided,
    }
    meta = {k: v for k, v in meta.items() if v not in (None, [], "")}
    body = d.context or ""
    if d.verdict or d.status != "proposed":
        body += "\n\n## Decision\n\n" + (d.verdict or d.status)
    d.path.parent.mkdir(parents=True, exist_ok=True)
    d.path.write_text("---\n" + yaml.safe_dump(meta, sort_keys=False, allow_unicode=True)
                      + "---\n" + body.strip() + "\n")
    return d.path


def all_decisions(project: Project, element: Element | None = None) -> list[Decision]:
    found = []
    for e in [element] if element else project.elements():
        d = folder(e)
        if d.is_dir():
            found += [read(f, e.path) for f in sorted(d.glob("D*.md"))]
    return found


def get(project: Project, key: str) -> Decision:
    for d in all_decisions(project):
        if d.key == key.strip("/"):
            return d
    raise ProjectError(f"no decision {key!r}")


def ask(project: Project, element: Element, title: str, options: list[str],
        recommended: str | None = None, context: str = "", raised_by: str = "agent") -> Decision:
    """Record a question for the human. Options are "Title" or "Title: summary"."""
    if len(options) < 2:
        raise ProjectError("a decision needs at least two options")
    opts = []
    for n, text in enumerate(options):
        title_, _, summary = text.partition(":")
        opts.append(Option(chr(ord("A") + n), title_.strip(), summary.strip()))
    if recommended and recommended not in {o.id for o in opts}:
        raise ProjectError(f"recommended option {recommended!r} is not one of the options")
    numbers = [int(x.id[1:]) for x in all_decisions(project, element) if x.id[1:].isdigit()]
    did = f"D{max(numbers, default=0) + 1:03d}"
    d = Decision(id=did, title=title, element=element.path, options=opts,
                 recommended=recommended, raised_by=raised_by,
                 date=dt.date.today().isoformat(), context=context,
                 path=folder(element) / f"{did}-{slug(title)[:40]}.md")
    write(d)
    return d


def decide(project: Project, key: str, verdict: str, option: str | None = None,
           note: str = "") -> Decision:
    """The human's answer: ``accept`` (an option) or ``reject`` (the question or all options)."""
    d = get(project, key)
    if verdict not in ("accept", "reject"):
        raise ProjectError("verdict must be accept or reject")
    if verdict == "accept":
        option = option or d.recommended
        if d.options and d.option(option) is None:
            raise ProjectError(f"{d.key}: choose one of {', '.join(o.id for o in d.options)}")
        d.status, d.chosen = "accepted", option
        chosen = d.option(option)
        d.verdict = (f"Accepted option {option}: {chosen.title}." if chosen else "Accepted.")
    else:
        d.status, d.chosen = "rejected", None
        d.verdict = "Rejected."
    if note:
        d.verdict += f"\n\n{note.strip()}"
    d.decided = dt.date.today().isoformat()
    write(d)
    return d
