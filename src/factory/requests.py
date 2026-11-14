"""Requests from projects to the tools they are built with.

A project agent that finds a tool short of what the work needs (a module or task Factory
does not have, a material Materia does not know, a bug in Citadel) does not work around it
quietly: it files a request addressed to that tool. Requests from every project collect in
one inbox, ``~/.local/share/factory/requests``, one Markdown file each. The user sends a
request to a development session (Copilot or Claude Code started in the tool's repository
with the request as its brief), and that session closes it with
``factory requests --close <id>`` when the change is in. The project's journal records both
ends.
"""

from __future__ import annotations

import datetime as dt
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

import yaml

from . import config, journal
from .project import Project, ProjectError, read_frontmatter

KINDS = ("feature", "bug", "material", "data", "update")
OPEN, SENT, DONE, DISMISSED = "open", "sent", "done", "dismissed"
FACTORY = Path(__file__).resolve().parents[2]


def inbox() -> Path:
    base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    return base / "factory/requests"


def targets() -> dict[str, Path]:
    """The tools a request can go to, with their repositories: Factory itself, the git
    repositories beside it (the user's other tools), and any under ``[tools]`` in the
    config (``materia = "~/src/materia"``)."""
    found: dict[str, Path] = {}
    if (FACTORY / ".git").exists():
        found["factory"] = FACTORY
        found.update({d.name: d for d in sorted(FACTORY.parent.iterdir())
                      if d != FACTORY and (d / ".git").exists()})
    for name, path in config.load().get("tools", {}).items():
        found[name] = Path(str(path)).expanduser()
    return found


@dataclass
class Request:
    id: str
    to: str
    kind: str
    title: str
    status: str
    created: str
    project: str = ""
    root: str = ""
    element: str = ""
    note: str = ""
    body: str = ""
    path: Path | None = None


def read(path: Path) -> Request:
    meta, body = read_frontmatter(path)
    return Request(id=path.stem, to=meta.get("to", ""), kind=meta.get("kind", "feature"),
                   title=meta.get("title", ""), status=meta.get("status", OPEN),
                   created=str(meta.get("created", "")), project=meta.get("project", ""),
                   root=meta.get("root", ""), element=meta.get("element", ""),
                   note=meta.get("note", ""), body=body.strip(), path=path)


def write(r: Request) -> None:
    meta = {k: getattr(r, k) for k in ("to", "kind", "title", "status", "created", "project",
                                        "root", "element", "note") if getattr(r, k)}
    text = "---\n" + yaml.safe_dump(meta, sort_keys=False, allow_unicode=True) + "---\n"
    tmp = r.path.with_name(r.path.name + ".tmp")
    tmp.write_text(text + (r.body + "\n" if r.body else ""))
    tmp.replace(r.path)


def all_requests(to: str | None = None, everything: bool = False) -> list[Request]:
    """Requests, oldest first; only those still waiting (open or sent) unless ``everything``."""
    if not inbox().is_dir():
        return []
    found = [read(p) for p in sorted(inbox().glob("*.md"))]
    return [r for r in found if (to is None or r.to == to)
            and (everything or r.status in (OPEN, SENT))]


def get(request_id: str) -> Request:
    path = inbox() / f"{request_id}.md"
    if not path.is_file():
        raise LookupError(f"no request {request_id!r}")
    return read(path)


def file(to: str, title: str, body: str = "", kind: str = "feature",
         project: Project | None = None, element: str = "") -> Request:
    tools = targets()
    if to not in tools:
        raise ValueError(f"unknown tool {to!r} (have: {', '.join(tools) or 'none'})")
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    inbox().mkdir(parents=True, exist_ok=True)
    taken = {p.stem for p in inbox().glob(f"{to}-*.md")}
    n = 1 + max((int(m.group(1)) for s in taken if (m := re.fullmatch(rf"{to}-(\d+)", s))),
                default=0)
    r = Request(id=f"{to}-{n}", to=to, kind=kind, title=title.strip(), status=OPEN,
                created=journal.now(), project=project.name if project else "",
                root=str(project.root) if project else "", element=element,
                body=body.strip(), path=inbox() / f"{to}-{n}.md")
    write(r)
    if project:
        journal.record(project, "request", f"Asked {to} for: {r.title} ({r.id})", element)
    return r


def close(request_id: str, status: str = DONE, note: str = "") -> Request:
    """Mark a request done (or sent, or dismissed), and tell the project it came from."""
    r = get(request_id)
    r.status, r.note = status, note or r.note
    write(r)
    if status in (DONE, DISMISSED) and r.root:
        try:
            project = Project.load(r.root)
        except ProjectError:
            return r
        verb = "did" if status == DONE else "declined"
        journal.record(project, "request", f"{r.to} {verb} {r.id}: {r.title}", r.element,
                       note)
    return r


def prompt(r: Request) -> str:
    """The brief for a development session in the tool's repository."""
    origin = f" from the Factory project {r.project!r}" if r.project else ""
    where = f" (element {r.element})" if r.element else ""
    return (f"A {r.kind} request{origin}{where}, filed by its agent: {r.title}\n\n"
            f"{r.body or '(no detail given)'}\n\n"
            f"The request is {r.path}." + (f" The project is at {r.root}; read what you need "
                                           "there, but do not change it." if r.root else "")
            + "\n\nFirst say whether this belongs in this tool and how you would do it, and "
            "wait for my go-ahead. When the change is in, run `factory requests --close "
            f"{r.id} -n \"<what changed and how to use it>\"` so the project hears about it.")


def dev_command(r: Request) -> list[str]:
    """The user's agent, interactive, in the tool's repository, briefed with the request."""
    agent = config.load().get("agent", {}).get("default", "claude")
    brief = prompt(r)
    if agent == "copilot" and shutil.which("copilot"):
        return ["copilot", "-n", f"{r.to}: {r.id}", "-i", brief]
    return ["claude", "-n", f"{r.to}: {r.id}", brief]


def window(r: Request) -> list[str]:
    """``dev_command`` in its own terminal window, in the tool's repository."""
    repo = targets().get(r.to)
    if repo is None or not repo.is_dir():
        raise LookupError(f"no repository for {r.to!r}: add [tools] {r.to} = \"<path>\" to "
                          "~/.config/factory/config.toml")
    inner = ["sh", "-c", 'cd "$1" && shift && exec "$@"', "sh", str(repo), *dev_command(r)]
    if shutil.which("omarchy-launch-tui"):
        return ["omarchy-launch-tui", "--app-id=org.omarchy.factory-dev", *inner]
    return ["xdg-terminal-exec", "--app-id=org.omarchy.factory-dev", "-e", *inner]


def age(r: Request) -> str:
    try:
        return journal.relative(dt.datetime.fromisoformat(r.created).astimezone())
    except ValueError:
        return ""
