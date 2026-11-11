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

from . import decisions, gates, review
from .model import Element
from .project import Project

APP_ID = "org.omarchy.factory-agent"


def brief(project: Project, element: Element, task: str = "") -> str:
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
    lines.append(task.strip() or "Load the factory skill, summarise where this element stands, "
                 "and propose the next piece of work. Wait for my go-ahead before you start.")
    return "\n".join(lines)


def command(project: Project, element: Element, task: str = "") -> list[str]:
    """``claude`` started in the project folder with the brief."""
    name = f"factory · {project.id}" + (f"/{element.path}" if element.path else "")
    return ["claude", "-n", name, brief(project, element, task)]


def resume_command(session: Session, fork: bool = False) -> list[str]:
    """Continue a saved session; ``fork`` continues a copy and leaves the original as it was."""
    return ["claude", "--resume", session.id, *(["--fork-session"] if fork else [])]


def window_command(project: Project, element: Element, task: str = "") -> list[str]:
    """The same session in its own Omarchy terminal window."""
    return window(project, command(project, element, task))


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
CLAUDE_MD = """# Factory project

This folder is a Factory project. Load the `factory` skill before changing anything here.
The user reviews and approves your work in Factory's Review tab; committing, locking and
deciding are theirs.
"""


def ensure_files(project: Project) -> list[str]:
    """Write the agent guardrails if a project lacks them. Returns the files written."""
    written = []
    for rel, text in ((".claude/settings.json", SETTINGS), ("CLAUDE.md", CLAUDE_MD)):
        path = project.root / rel
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
            written.append(rel)
    return written
