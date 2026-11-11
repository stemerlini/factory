"""Human review of pending work: uncommitted changes, grouped by element.

An agent never commits. What it changed sits in the working tree with a *proposal*
(``.factory/proposal.md``: a title line, then what changed and why). The human approves
(commit, with the proposal's title as the message) or rejects (discard) per file,
per element or all at once.
"""

from __future__ import annotations

import datetime as dt
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .model import Element
from .project import Project, ProjectError

PROPOSAL = ".factory/proposal.md"
STATUS_TEXT = {"??": "new", "A": "added", "M": "modified", "D": "deleted", "R": "renamed"}


@dataclass
class Change:
    path: str  # relative to the project root
    code: str  # porcelain XY, e.g. " M", "??"
    element: str  # element path the file belongs to

    @property
    def kind(self) -> str:
        if self.code == "??":
            return "new"
        for ch in self.code.strip() or " ":
            if ch in STATUS_TEXT:
                return STATUS_TEXT[ch]
        return "changed"


def changes(project: Project) -> list[Change]:
    # Raw output: the leading space of " M path" is significant, so no stripping here.
    try:
        out = subprocess.run(["git", "-C", str(project.root), "status", "--porcelain=v1",
                              "-uall", "-z"], capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return []
    elements = sorted(project.elements(), key=lambda e: len(e.dir.parts), reverse=True)
    found = []
    entries = out.split("\0")
    i = 0
    while i < len(entries):
        entry = entries[i]
        i += 1
        if len(entry) < 4:
            continue
        code, path = entry[:2], entry[3:]
        if code[0] == "R":  # renames carry the old path as the next entry
            i += 1
        if path.startswith(".factory/"):
            continue
        found.append(Change(path, code, owner(project, elements, path)))
    return found


def owner(project: Project, elements: list[Element], path: str) -> str:
    full = project.root / path
    for e in elements:  # deepest first
        if full == e.dir or e.dir in full.parents:
            return e.path
    return ""


def diff(project: Project, change: Change) -> str:
    if change.code == "??":
        try:
            return (project.root / change.path).read_text()
        except (OSError, UnicodeDecodeError):
            return "(binary file)"
    head = project.git("rev-parse", "--verify", "-q", "HEAD")
    args = ["diff", "HEAD", "--", change.path] if head else ["diff", "--cached", "--",
                                                              change.path]
    return project.git(*args) or ""


def proposal(project: Project) -> str:
    path = project.root / PROPOSAL
    return path.read_text() if path.is_file() else ""


def title(project: Project) -> str:
    for line in proposal(project).splitlines():
        line = line.strip().lstrip("#").strip()
        if line:
            return line
    return ""


def approve(project: Project, paths: list[str], message: str) -> str:
    """Commit exactly these paths. Returns the new commit."""
    if not paths:
        raise ProjectError("nothing to approve")
    if not message.strip():
        raise ProjectError("an approval needs a commit message")
    project.git_checked("add", "-A", "--", *paths)
    project.git_checked("commit", "-q", "-m", message.strip(), "--", *paths)
    if not changes(project):
        archive_proposal(project)
    head, _ = project.commit()
    return head or ""


def reject(project: Project, path: str) -> None:
    """Discard the change to one file: restore it, or delete it if it is new."""
    match = next((c for c in changes(project) if c.path == path), None)
    if match is None:
        raise ProjectError(f"{path} has no pending change")
    full = project.root / path
    if match.code == "??":
        if full.is_dir():
            shutil.rmtree(full)
        else:
            full.unlink()
        return
    if project.git("rev-parse", "--verify", "-q", "HEAD") is None:
        project.git_checked("rm", "-q", "--cached", "-f", "--", path)
        full.unlink(missing_ok=True)
        return
    project.git_checked("restore", "--staged", "--worktree", "--source=HEAD", "--", path)


def archive_proposal(project: Project) -> Path | None:
    path = project.root / PROPOSAL
    if not path.is_file():
        return None
    target = path.parent / "proposals" / f"{dt.datetime.now():%Y%m%d-%H%M%S}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    path.replace(target)
    return target
