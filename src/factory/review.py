"""Human review of pending work: uncommitted changes, grouped by element.

An agent never commits. What it changed sits in the working tree with a *proposal*
(``.factory/proposal.md``: a title line, then what changed and why). The human approves
(commit, with the proposal's title as the message) or rejects (discard) per file,
per element or all at once.

In the app the human reviews file by file, as on a pull request: each file is marked
accepted or rejected and may carry a comment. *Submitting* the review commits what was
accepted, discards what was rejected, and turns the comments into feedback for the agent;
files with only a comment stay pending for the agent to revise.
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .model import Element
from .project import Project, ProjectError

PROPOSAL = ".factory/proposal.md"
MARKS = ".factory/review.json"  # the review in progress: {path: {"verdict", "comment"}}
ACCEPT, REJECT = "accept", "reject"
FEEDBACK = "Review of your proposal"
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


def marks(project: Project) -> dict[str, dict]:
    """The review in progress, for files that are still pending."""
    try:
        found = json.loads((project.root / MARKS).read_text())
    except (OSError, ValueError):
        return {}
    pending = {c.path for c in changes(project)}
    return {p: m for p, m in found.items() if p in pending and (m.get("verdict")
                                                                or m.get("comment"))}


def save_marks(project: Project, found: dict[str, dict]) -> None:
    path = project.root / MARKS
    path.parent.mkdir(parents=True, exist_ok=True)
    kept = {p: m for p, m in found.items() if m.get("verdict") or m.get("comment")}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(kept, indent=1))
    tmp.replace(path)


@dataclass
class Outcome:
    head: str = ""
    accepted: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    feedback: str = ""  # for the agent; empty when there is nothing to tell it


def submit(project: Project, found: dict[str, dict], message: str, overall: str = "") -> Outcome:
    """Apply a review: discard the rejected files, commit the accepted ones, and write the
    feedback for the agent. Comments on files left pending ask the agent to revise them."""
    out = Outcome(accepted=[p for p, m in found.items() if m.get("verdict") == ACCEPT],
                  rejected=[p for p, m in found.items() if m.get("verdict") == REJECT])
    if out.accepted and not message.strip():
        raise ProjectError("accepting files commits them: write a commit message")
    for path in out.rejected:
        reject(project, path)
    if out.accepted:
        out.head = approve(project, out.accepted, message)
    comments = [(p, m.get("verdict"), m["comment"].strip()) for p, m in found.items()
                if (m.get("comment") or "").strip()]
    if comments or overall.strip() or out.rejected:
        lines = [f"{FEEDBACK} (the human's review in Factory).", ""]
        if out.accepted:
            lines.append(f"Accepted and committed as {out.head}: {', '.join(out.accepted)}")
        if out.rejected:
            lines.append(f"Discarded: {', '.join(out.rejected)}")
        if comments:
            lines += ["", "Changes requested:"]
            tag = {ACCEPT: " (accepted)", REJECT: " (discarded)"}
            lines += [f"- {p}{tag.get(v, '')}: {c}" for p, v, c in comments]
        if overall.strip():
            lines += ["", f"Overall: {overall.strip()}"]
        lines += ["", "Revise what the comments ask for, leave the committed files as they are, "
                  "update .factory/proposal.md, and stop again for review."]
        out.feedback = "\n".join(lines)
    save_marks(project, {})
    return out
