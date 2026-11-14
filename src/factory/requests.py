"""Requests from projects to the tools they are built with.

A project agent that finds a tool short of what the work needs (a module or task Factory
does not have, a material Materia does not know, a bug in Citadel) does not work around it
quietly: it files a request addressed to that tool. Requests from every project collect in
one inbox, ``~/.local/share/factory/requests``, one Markdown file each.

The life of a request: *open* (filed) → *sent* (the user opened a development session in
the tool's repository, briefed with it) → *delivered* (that session put the change on a
branch, usually with a pull request, and did not merge it) → *accepted* (the user merged
it) or *rejected* (the user turned it down). The session can also *decline* a request that
does not belong in its tool. Whatever the end, the project's agent is told: to pick the
change up, or to find another way.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import yaml

from . import config, journal
from .project import Project, ProjectError, read_frontmatter

KINDS = ("feature", "bug", "material", "data", "update")
OPEN, SENT, DELIVERED = "open", "sent", "delivered"
ACCEPTED, REJECTED, DECLINED = "accepted", "rejected", "declined"
WAITING = (OPEN, SENT, DELIVERED)
ENDED = (ACCEPTED, REJECTED, DECLINED)
FACTORY = Path(__file__).resolve().parents[2]

# How a development session works when its repository has no AGENTS.md of its own: the
# same loop Factory, Citadel and Materia are maintained by.
LOOP = """\
1. Sync the default branch, then work on a branch `feat/<topic>` or `fix/<topic>`.
2. Red: a test that fails for the reason the project needs this. Keep tests lean: only
   ones that would fail without the change; no tests of constants, defaults or plumbing.
3. Green: the whole test suite and the linter pass. A visible change (a TUI or GUI view)
   is looked at in an offscreen screenshot before it is called done.
4. Record: user-visible behaviour in the README (and the in-app help); decisions in
   PLAN.md, dated, if the repository keeps one.
5. Commit with short imperative messages, push the branch and open a pull request
   (`gh pr create`): what was asked, why, how it was verified."""


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
    branch: str = ""
    pr: str = ""
    told: bool = False
    session: str = ""
    body: str = ""
    path: Path | None = None


FIELDS = ("to", "kind", "title", "status", "created", "project", "root", "element", "note",
          "branch", "pr", "told", "session")


def read(path: Path) -> Request:
    meta, body = read_frontmatter(path)
    return Request(id=path.stem, to=meta.get("to", ""), kind=meta.get("kind", "feature"),
                   title=meta.get("title", ""), status=meta.get("status", OPEN),
                   created=str(meta.get("created", "")), project=meta.get("project", ""),
                   root=meta.get("root", ""), element=meta.get("element", ""),
                   note=meta.get("note", ""), branch=meta.get("branch", ""),
                   pr=meta.get("pr", ""), told=bool(meta.get("told", False)),
                   session=meta.get("session", ""),
                   body=body.strip(), path=path)


def write(r: Request) -> None:
    meta = {k: getattr(r, k) for k in FIELDS if getattr(r, k)}
    text = "---\n" + yaml.safe_dump(meta, sort_keys=False, allow_unicode=True) + "---\n"
    tmp = r.path.with_name(r.path.name + ".tmp")
    tmp.write_text(text + (r.body + "\n" if r.body else ""))
    tmp.replace(r.path)


def all_requests(to: str | None = None, everything: bool = False) -> list[Request]:
    """Requests, oldest first; only those still waiting (open, sent or delivered) unless
    ``everything``."""
    if not inbox().is_dir():
        return []
    found = [read(p) for p in sorted(inbox().glob("*.md"))]
    return [r for r in found if (to is None or r.to == to)
            and (everything or r.status in WAITING)]


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


def _tell_project(r: Request, title: str, detail: str = "") -> None:
    if not r.root:
        return
    try:
        project = Project.load(r.root)
    except ProjectError:
        return
    journal.record(project, "request", title, r.element, detail)


def mark_sent(request_id: str, session: str = "") -> Request:
    r = get(request_id)
    r.status = SENT if r.status == OPEN else r.status
    r.session = session or r.session
    write(r)
    return r


def deliver(request_id: str, note: str, branch: str = "", pr: str = "") -> Request:
    """The development session's hand-back: the change is on ``branch`` (and ``pr``),
    waiting for the user to accept or reject it."""
    r = get(request_id)
    if r.status in ENDED:
        raise ValueError(f"{r.id} is already {r.status}")
    r.status, r.note = DELIVERED, note or r.note
    r.branch, r.pr = branch or r.branch, pr or r.pr
    write(r)
    _tell_project(r, f"{r.to} delivered {r.id}: {r.title} (to review)", note)
    return r


def _end(r: Request, status: str, note: str, title: str) -> Request:
    r.status, r.note, r.told = status, note or r.note, False
    write(r)
    _tell_project(r, title, note)
    return r


def decline(request_id: str, note: str) -> Request:
    """The development session's answer when the request does not belong in its tool."""
    r = get(request_id)
    if r.status in ENDED:
        raise ValueError(f"{r.id} is already {r.status}")
    return _end(r, DECLINED, note, f"{r.to} declined {r.id}: {r.title}")


def _run(repo: Path, *argv: str) -> str:
    done = subprocess.run(argv, cwd=repo, capture_output=True, text=True)
    if done.returncode:
        raise RuntimeError(f"{' '.join(argv[:3])}: {(done.stderr or done.stdout).strip()}")
    return done.stdout.strip()


def default_branch(repo: Path) -> str:
    try:
        return _run(repo, "git", "rev-parse", "--abbrev-ref", "origin/HEAD").split("/", 1)[1]
    except (RuntimeError, IndexError):
        branches = _run(repo, "git", "branch", "--format=%(refname:short)").split()
        return "master" if "master" in branches and "main" not in branches else "main"


def _home(repo: Path, base: str) -> None:
    """Put the checkout back on the default branch, if it is on another one and clean."""
    if _run(repo, "git", "branch", "--show-current") != base:
        if _run(repo, "git", "status", "--porcelain"):
            raise RuntimeError(f"{repo} has uncommitted changes: switch it to {base} by hand")
        _run(repo, "git", "switch", "-q", base)


def changes(r: Request) -> str:
    """The delivered change in short: its commits and the files it touches."""
    repo = targets().get(r.to)
    if not (repo and r.branch):
        return ""
    base = default_branch(repo)
    try:
        log = _run(repo, "git", "log", "--oneline", f"{base}..{r.branch}")
        stat = _run(repo, "git", "diff", "--stat", f"{base}...{r.branch}")
    except RuntimeError:
        return ""
    return f"{log}\n\n{stat}".strip()


def accept(request_id: str) -> Request:
    """Merge the delivered change (the pull request with gh, or else the branch locally) and
    bring the checkout up to date on the default branch, so the tool runs the new code."""
    r = get(request_id)
    if r.status != DELIVERED:
        raise ValueError(f"{r.id} is {r.status}: only a delivered request can be accepted")
    repo = targets()[r.to]
    base = default_branch(repo)
    if r.pr:
        _run(repo, "gh", "pr", "merge", r.pr, "--merge", "--delete-branch")
        _home(repo, base)
        _run(repo, "git", "pull", "-q", "--ff-only")
    elif r.branch:
        _home(repo, base)
        _run(repo, "git", "merge", "-q", "--no-edit", r.branch)
        _run(repo, "git", "branch", "-q", "-d", r.branch)
    return _end(r, ACCEPTED, r.note, f"You accepted {r.to}'s {r.id}: {r.title}")


def reject(request_id: str, reason: str) -> Request:
    """Turn the request down, closing its pull request or dropping its branch. The project's
    agent is then told to find another way."""
    r = get(request_id)
    if r.status in ENDED:
        raise ValueError(f"{r.id} is already {r.status}")
    repo = targets().get(r.to)
    if repo and r.status == DELIVERED:
        if r.pr:
            _run(repo, "gh", "pr", "close", r.pr, "--delete-branch", "--comment",
                 f"Rejected from Factory: {reason}")
        if r.branch:
            _home(repo, default_branch(repo))
            if r.branch in _run(repo, "git", "branch", "--format=%(refname:short)").split():
                _run(repo, "git", "branch", "-q", "-D", r.branch)
    return _end(r, REJECTED, reason, f"You rejected {r.id} to {r.to}: {r.title}")


def mark_told(request_id: str) -> None:
    r = get(request_id)
    r.told = True
    write(r)


def report(r: Request) -> str:
    """What the project's agent hears when a request has ended."""
    head = f"Your request {r.id} to {r.to} ({r.title})"
    if r.status == ACCEPTED:
        return (f"{head} was delivered and the user accepted it: {r.note}\n\nPick it up: "
                "use the new capability where you had worked around it, rerun the studies "
                "it affects, and update the proposal.")
    who = "the user rejected it" if r.status == REJECTED else f"{r.to} declined it"
    return (f"{head} will not happen: {who}. Reason: {r.note or '(none given)'}\n\n"
            "Find another way: with the tools the project already has, a different method "
            "or tool, or a request to another tool. If no way is good enough, put the "
            "trade-off to the user with `factory ask`. Then update the proposal.")


def prompt(r: Request, repo: Path | None = None) -> str:
    """The brief for a development session in the tool's repository."""
    repo = repo or targets().get(r.to)
    origin = f" from the Factory project {r.project!r}" if r.project else ""
    where = f" (element {r.element})" if r.element else ""
    rules = [n for n in ("AGENTS.md", "CLAUDE.md") if repo and (repo / n).is_file()]
    how = (f"Work by this repository's {rules[0]}: its change loop and guardrails bind you."
           if rules else f"This repository has no AGENTS.md; work by this loop:\n\n{LOOP}")
    project = (f" The project is at {r.root}; read what you need there, but do not change "
               "it." if r.root else "")
    return (f"A {r.kind} request{origin}{where}, filed by its agent: {r.title}\n\n"
            f"{r.body or '(no detail given)'}\n\nThe request is {r.path}.{project}\n\n"
            f"{how}\n\n"
            "First say whether this belongs in this tool and how you would do it, and wait "
            "for my go-ahead. Deliver on a branch, with a pull request when the repository "
            "has a GitHub remote. Never merge it and never commit to the default branch: I "
            "accept or reject it in Factory. When it is ready, run\n"
            f"`factory requests --close {r.id} --branch <branch> --pr <url> -n \"<what "
            "changed and how the project uses it>\"`.\n"
            "If it does not belong in this tool, say why and run "
            f"`factory requests --close {r.id} --decline -n \"<why, and what the project "
            "could do instead>\"`.")


def agent_kind() -> str:
    agent = config.load().get("agent", {}).get("default", "claude")
    return agent if agent in ("copilot", "claude") and shutil.which(agent) else "claude"


def dev_command(r: Request) -> list[str]:
    """The user's agent, interactive, in the tool's repository, briefed with the request."""
    brief = prompt(r)
    if agent_kind() == "copilot":
        return ["copilot", "-n", f"{r.to}: {r.id}", "-i", brief]
    return ["claude", "-n", f"{r.to}: {r.id}", brief]


def repo_of(r: Request) -> Path:
    repo = targets().get(r.to)
    if repo is None or not repo.is_dir():
        raise LookupError(f"no repository for {r.to!r}: add [tools] {r.to} = \"<path>\" to "
                          "~/.config/factory/config.toml")
    return repo


def window(r: Request) -> list[str]:
    """``dev_command`` in its own terminal window, in the tool's repository."""
    inner = ["sh", "-c", 'cd "$1" && shift && exec "$@"', "sh", str(repo_of(r)),
             *dev_command(r)]
    if shutil.which("omarchy-launch-tui"):
        return ["omarchy-launch-tui", "--app-id=org.omarchy.factory-dev", *inner]
    return ["xdg-terminal-exec", "--app-id=org.omarchy.factory-dev", "-e", *inner]


# Herdr (a terminal workspace manager for coding agents) is where the user keeps the
# development sessions of their tools, one workspace each ("factory-dev", "citadel-dev").
# A request's session joins its tool's workspace as a tab, so the user can watch it, steer
# it and answer its permission prompts beside the others.

class HerdrError(RuntimeError):
    def __init__(self, message: str, code: str = "") -> None:
        super().__init__(message)
        self.code = code


def herdr(*args: str, timeout: float = 120) -> dict:
    done = subprocess.run(["herdr", *args], capture_output=True, text=True,
                          timeout=timeout)
    text = done.stdout.strip() or done.stderr.strip()
    reply = json.loads(text) if text.startswith("{") else {}
    if done.returncode or "error" in reply:
        error = reply.get("error", {})
        raise HerdrError(f"herdr {args[0]} {args[1]}: {error.get('message') or text}",
                         error.get("code", ""))
    return reply.get("result", {})


def herdr_running() -> bool:
    if not shutil.which("herdr"):
        return False
    done = subprocess.run(["herdr", "status", "server"], capture_output=True, text=True)
    return done.returncode == 0 and "status: running" in done.stdout


def herdr_name(r: Request) -> str:
    return re.sub(r"[^a-z0-9_-]", "-", f"req-{r.id}".lower())[:32]


def send_to_herdr(r: Request, say=lambda text: None) -> str:
    """Start (or bring forward) the request's session in its tool's Herdr workspace. If the
    agent stops at startup to ask something (Copilot asks to trust a new folder), it is
    brought forward for the user to answer, and the brief follows once it is ready."""
    name = herdr_name(r)
    try:
        herdr("agent", "get", name)
        herdr("agent", "focus", name)
        return f"{r.id}'s session is already in Herdr ({name}): focused it"
    except RuntimeError:
        pass
    repo, label = repo_of(r), f"{r.to}-dev"
    spaces = herdr("workspace", "list").get("workspaces", [])
    space = next((w for w in spaces if w.get("label", "").lower() == label), None)
    if space:
        made = herdr("tab", "create", "--workspace", space["workspace_id"], "--cwd", str(repo),
                     "--label", r.id, "--no-focus")
    else:
        made = herdr("workspace", "create", "--cwd", str(repo), "--label", label, "--no-focus")
    pane = made["root_pane"]["pane_id"]
    try:
        herdr("agent", "start", name, "--kind", agent_kind(), "--pane", pane,
              "--timeout", "60000")
    except HerdrError as e:
        if e.code != "agent_not_ready":
            raise
        herdr("agent", "focus", name)
        say(f"{name} asks something in Herdr ({label}): answer it there; the brief follows")
        herdr("agent", "wait", name, "--until", "idle", "--timeout", "600000", timeout=660)
    herdr("agent", "prompt", name, prompt(r))
    return f"{r.id} is with {agent_kind()} in Herdr: workspace {label}, agent {name}"


def send(r: Request, say=lambda text: None) -> str:
    """Hand the request to a development session: in Herdr when it runs, else in a new
    terminal window. Returns where it went."""
    if herdr_running():
        where = send_to_herdr(r, say)
        mark_sent(r.id, herdr_name(r))
        return where
    subprocess.Popen(window(r), start_new_session=True, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)
    mark_sent(r.id)
    return f"Opened a {r.to} development session for {r.id} in a new window"


def age(r: Request) -> str:
    try:
        return journal.relative(dt.datetime.fromisoformat(r.created).astimezone())
    except ValueError:
        return ""
