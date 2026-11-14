"""Start a Claude Code session to work on a project with the human in the loop.

The session's behaviour comes from the ``factory`` skill and the project's
``.claude/settings.json`` (which blocks committing, locking and deciding); this module only
builds the opening brief for the selected element and the commands that open, continue and
find a session. Claude Code keeps every conversation under ``~/.claude/projects/<folder>``,
so a session stopped by a usage limit can be continued once the limit resets.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import decisions, gates, guide, review
from .model import GATES, Element
from .project import Project

APP_ID = "org.omarchy.factory-agent"


def brief(project: Project, element: Element, task: str = "", gate: str | None = None) -> str:
    """The opening message. With ``gate``, the session is about that gate: its open items
    and its playbook come with the brief."""
    g = gates.gates(project, element)
    cur = gates.current(g)
    where = element.title + (f" ({element.path})" if element.path else " (the system)")
    lines = [
        f"Factory project \"{project.name}\" at {project.root}.",
        f"Focus: {where}; next gate: "
        + (f"{cur.title} — {cur.detail}." if cur else "none, every gate is done."),
    ]
    open_q = [d.key for d in decisions.all_decisions(project) if d.status == "proposed"]
    if open_q:
        lines.append(f"Decisions waiting for me: {', '.join(open_q)}.")
    if (project.root / review.PROPOSAL).is_file():
        lines.append(f"The last session's handover is in {review.PROPOSAL}: read it first and "
                     "carry on from where it stopped.")
    elif review.changes(project):
        lines.append("There are unreviewed changes in the working tree.")
    if gate:
        title = dict(GATES)[gate]
        items = [c for c in guide.checklist(project, element)[gate] if not c.done]
        lines.append(f"\nWe are working on the {title} gate. Still open:")
        lines += [f"- {c.label}" + (f" ({c.detail})" if c.detail else "") for c in items] or [
            "- nothing: review the gate and say whether it is really done"]
        book = guide.playbook(project, gate).strip()
        if book:
            lines += ["", "How we run this gate:", "", book, ""]
    default = ("Load the factory skill, then propose how you will close the open items of this "
               "gate. Wait for my go-ahead before you start." if gate else
               "Load the factory skill, summarise where this element stands, and propose the "
               "next piece of work. Wait for my go-ahead before you start.")
    lines.append(task.strip() or default)
    return "\n".join(lines)


def kickoff_brief(project: Project, idea: str) -> str:
    """The opening message of a new project: the user's idea and how a kickoff runs."""
    return "\n".join([
        f"We are starting a new Factory project, \"{project.name}\", at {project.root} "
        f"(template: {project.template}). Load the factory skill.",
        "", "My idea:", "", idea.strip(), "",
        "How we run the kickoff:", "", guide.playbook(project, "kickoff").strip(), "",
        "Start with your questions; don't write anything until I have answered them."])


def command(project: Project, element: Element, task: str = "",
            gate: str | None = None) -> list[str]:
    """``claude`` started in the project folder with the brief."""
    name = f"factory · {project.id}" + (f"/{element.path}" if element.path else "")
    return ["claude", "-n", name, brief(project, element, task, gate)]


def resume_command(session: Session, fork: bool = False) -> list[str]:
    """Continue a saved session; ``fork`` continues a copy and leaves the original as it was."""
    return ["claude", "--resume", session.id, *(["--fork-session"] if fork else [])]


def window_command(project: Project, element: Element, task: str = "",
                   gate: str | None = None) -> list[str]:
    """The same session in its own Omarchy terminal window."""
    return window(project, command(project, element, task, gate))


def window(project: Project, argv: list[str]) -> list[str]:
    """Run ``argv`` in the project folder, in its own Omarchy terminal window."""
    inner = ["sh", "-c", 'cd "$1" && shift && exec "$@"', "sh", str(project.root), *argv]
    if shutil.which("omarchy-launch-tui"):
        return ["omarchy-launch-tui", f"--app-id={APP_ID}", *inner]
    return ["xdg-terminal-exec", f"--app-id={APP_ID}", "-e", *inner]


@dataclass
class Session:
    id: str
    modified: float  # mtime of the transcript
    prompt: str  # the last thing the user asked
    path: Path


def session_dir(project: Project) -> Path:
    """Where Claude Code keeps this folder's conversations."""
    base = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    return base / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(project.root))


_prompts: dict[Path, tuple[float, str]] = {}


def sessions(project: Project) -> list[Session]:
    """Saved conversations started in the project folder, newest first."""
    found = []
    for path in session_dir(project).glob("*.jsonl"):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if _prompts.get(path, (None,))[0] != mtime:
            _prompts[path] = (mtime, last_prompt(path))
        found.append(Session(path.stem, mtime, _prompts[path][1], path))
    return sorted(found, key=lambda s: s.modified, reverse=True)


def last_prompt(path: Path) -> str:
    prompt = ""
    try:
        with path.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"last-prompt"' in line:
                    try:
                        prompt = json.loads(line).get("lastPrompt") or prompt
                    except ValueError:
                        pass
    except OSError:
        pass
    return " ".join(prompt.split())


def running(project: Project) -> list[int]:
    """Process ids of Claude Code sessions working in the project folder right now."""
    root = str(project.root.resolve())
    pids = []
    for proc in Path("/proc").glob("[0-9]*"):
        try:
            if (proc / "comm").read_text().strip() == "claude" and \
                    os.readlink(proc / "cwd") == root:
                pids.append(int(proc.name))
        except OSError:
            continue
    return pids


def focus(pid: int) -> bool:
    """Raise the terminal window a session runs in (Hyprland). False if it has none."""
    try:
        out = subprocess.run(["hyprctl", "clients", "-j"], capture_output=True, text=True,
                             timeout=2).stdout
        windows = {c["pid"]: c["address"] for c in json.loads(out)}
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        return False
    while pid > 1:
        if pid in windows:
            address = windows[pid]
            # Hyprland's Lua dispatch (0.55+), then the classic one, as omarchy-launch-or-focus.
            for args in ([f'hl.dsp.focus({{ window = "address:{address}" }})'],
                         ["focuswindow", f"address:{address}"]):
                done = subprocess.run(["hyprctl", "dispatch", *args], capture_output=True,
                                      text=True)
                if done.returncode == 0 and "error" not in done.stdout.lower():
                    return True
            return False
        try:
            pid = int(Path(f"/proc/{pid}/stat").read_text().rpartition(")")[2].split()[1])
        except (OSError, ValueError, IndexError):
            return False
    return False


# What every project carries so any Claude Code session started in it follows the rules,
# however it was started. Written by `factory new` and, if missing, by `factory agent`.
SETTINGS = """{
  "permissions": {
    "deny": [
      "Bash(git commit *)",
      "Bash(git push *)",
      "Bash(git reset *)",
      "Bash(git checkout *)",
      "Bash(git restore *)",
      "Bash(git tag *)",
      "Bash(git rebase *)",
      "Bash(factory lock *)",
      "Bash(factory decide *)",
      "Bash(factory approve *)",
      "Bash(factory reject *)"
    ]
  }
}
"""
AGENTS_MD = """# Factory project

This folder is a Factory project: one product as a tree of elements (system, subsystems,
components), each taken through the gates Describe, Research, Architect, Concept, Analyse,
Lock, Design and Deliver. Load the `factory` skill before changing anything here (install it
with `factory skill install`). `factory home` says where things stand, `factory gates` what
each gate still needs.

- Work in the files. The user reviews and approves every change in Factory's Review tab.
- Never commit, push, reset, checkout, restore, tag or rebase, and never run `factory lock`,
  `decide`, `approve` or `reject`: those are the user's.
- A trade-off that is the user's to make becomes a question (`factory ask`), not a guess.
- Use the user's tools (Citadel for literature, Materia for materials, Factory's modules
  and tasks for simulation). When one falls short, don't hack around it: file
  `factory request "<what>" --to <tool>` and say so in the proposal.
- Before you stop, write what you did and what comes next in `.factory/proposal.md`.
- A message starting "Review of your proposal" is the user's review: committed files are
  done; revise the ones with comments, then update the proposal and stop again.
"""
# Claude Code reads CLAUDE.md, and imports AGENTS.md from it; the other agents read AGENTS.md.
CLAUDE_MD = "@AGENTS.md\n"
GUARDRAILS = ((".claude/settings.json", SETTINGS), ("AGENTS.md", AGENTS_MD),
              ("CLAUDE.md", CLAUDE_MD))
SKILL = Path(__file__).resolve().parents[2] / "omarchy/skill/factory"
SKILL_HOMES = ("~/.claude/skills", "~/.copilot/skills", "~/.agents/skills")


def install_skill() -> list[Path]:
    """Link the factory skill into every agent's skill folder that exists (Claude Code,
    Copilot, and the shared ~/.agents used by opencode and others). Returns the links made."""
    made = []
    for home in SKILL_HOMES:
        folder = Path(home).expanduser()
        if not folder.parent.is_dir():
            continue
        link = folder / "factory"
        if link.is_symlink() and link.resolve() == SKILL:
            continue
        folder.mkdir(exist_ok=True)
        if link.is_symlink():
            link.unlink()
        elif link.exists():
            continue  # the user's own; leave it
        link.symlink_to(SKILL)
        made.append(link)
    return made


def ensure_files(project: Project) -> list[str]:
    """Write the agent guardrails if a project lacks them. Returns the files written."""
    written = []
    for rel, text in GUARDRAILS:
        path = project.root / rel
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
            written.append(rel)
    return written
