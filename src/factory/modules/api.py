"""The module contract. A module is how a domain (radiation, plasma, circuits…) plugs in.

A module bundles:
- tasks:      runnable task types used by studies (``geant4.run``, ``pic.run``, ``script``);
- tools:      launchers for external apps on project files (KiCad, Geant4 viewer…);
- viewers:    how results are shown (inline plots, ParaView, f3d…);
- kinds:      element kinds it brings (electronics, mechanical, software…);
- file_kinds: how the file browser labels project files;
- toolchains: how to find an installed external program and load its environment;
- checks:     health of the module on this machine.

A task does not run anything itself: ``command`` describes the process and the core's
executor runs it, logs it and records provenance. ``collect`` then turns the outputs
into metrics, which is what requirements are verified against.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..model import Metric

if TYPE_CHECKING:
    from ..model import Study
    from ..project import Project


@dataclass(frozen=True)
class Param:
    name: str
    type: type = str
    default: Any = None
    unit: str = ""
    help: str = ""
    required: bool = False


@dataclass
class Command:
    argv: list[str]
    env: dict[str, str] = field(default_factory=dict)
    cwd: Path | None = None


@dataclass
class RunContext:
    """Everything a task needs to build its command and collect its results."""

    project: Project
    study: Study
    run_id: str
    run_dir: Path  # in the run store: log.txt, outputs/, metrics.json
    params: dict[str, Any]
    inputs: dict[str, Path]  # resolved: project files or upstream run outputs
    toolchain_env: dict[str, str] = field(default_factory=dict)

    @property
    def outputs_dir(self) -> Path:
        return self.run_dir / "outputs"

    @property
    def element_dir(self) -> Path:
        return self.study.dir.parent.parent


@dataclass(frozen=True)
class Task:
    id: str  # local id; the full id is "<module>.<id>" (or just "<module>" for "main")
    title: str
    command: Callable[[RunContext], Command]
    params: tuple[Param, ...] = ()
    toolchain: str | Callable[[dict[str, Any]], str] | None = None  # may depend on params
    outputs: tuple[str, ...] = ()
    collect: Callable[[RunContext], dict[str, Metric]] | None = None
    version: str = "1"  # bump when results would change; makes old runs stale
    description: str = ""

    def toolchain_for(self, params: dict[str, Any]) -> str | None:
        return self.toolchain(params) if callable(self.toolchain) else self.toolchain

    def resolve_params(self, given: dict[str, Any]) -> dict[str, Any]:
        known = {p.name for p in self.params}
        values = {k: v for k, v in given.items() if k not in known}  # pass extras through
        for p in self.params:
            if p.name in given:
                values[p.name] = p.type(given[p.name])
            elif p.required:
                raise ValueError(f"missing parameter {p.name!r}")
            elif p.default is not None:
                values[p.name] = p.default
        return values


@dataclass(frozen=True)
class Tool:
    id: str
    title: str
    argv: Callable[[Path], list[str]]  # file -> command line
    globs: tuple[str, ...] = ()  # files it opens; empty = project-level tool
    toolchain: str | None = None


@dataclass(frozen=True)
class Viewer:
    """Shows results: inline in the TUI (``argv`` None) or in its own window."""

    id: str
    title: str
    globs: tuple[str, ...]
    argv: Callable[[Path], list[str]] | None = None
    executable: str | None = None  # for the health check / availability

    def available(self) -> bool:
        import shutil

        return self.executable is None or shutil.which(self.executable) is not None


@dataclass(frozen=True)
class ElementKind:
    """What an element is (electronics, mechanical, software…): its icon and the modules
    whose tasks and tools it normally uses."""

    id: str
    title: str
    icon: str = "·"
    modules: tuple[str, ...] = ()
    prefix: str = ""  # default requirement prefix
    description: str = ""


@dataclass(frozen=True)
class FileKind:
    label: str
    globs: tuple[str, ...]
    icon: str = "·"


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class Toolchain:
    """An external program installed per version under ``<root>/<name>/<version>``.

    ``env_script`` (relative to the version dir) is sourced in a subshell and the
    resulting environment is passed to the task's process. Nothing touches the user's shell.
    """

    name: str
    env_script: str | None = None
    executable: str | None = None  # relative to the version dir, used as a health probe
    root: Path = field(default_factory=lambda: Path.home() / ".local/opt")

    def versions(self) -> list[str]:
        base = self.root / self.name
        if not base.is_dir():
            return []
        return sorted((p.name for p in base.iterdir() if p.is_dir()), key=_version_key)

    def prefix(self, version: str | None = None) -> Path | None:
        found = self.versions()
        if not found:
            return None
        if version is None:
            version = found[-1]
        return self.root / self.name / version if version in found else None

    def environment(self, version: str | None = None) -> dict[str, str]:
        prefix = self.prefix(version)
        if prefix is None:
            wanted = f"{self.name} {version}" if version else self.name
            raise LookupError(f"toolchain {wanted} is not installed")
        if not self.env_script:
            return {"PATH": f"{prefix / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}"}
        return source_env(prefix / self.env_script, cwd=prefix)


def source_env(script: Path, cwd: Path | None = None) -> dict[str, str]:
    """Environment variables a shell script sets or changes."""
    import subprocess

    out = subprocess.run(
        ["bash", "-c", f'source "{script}" >/dev/null 2>&1; env -0'],
        capture_output=True,
        cwd=cwd,
        check=True,
    ).stdout.decode()
    after = dict(line.split("=", 1) for line in out.split("\0") if "=" in line)
    return {k: v for k, v in after.items() if os.environ.get(k) != v}


def _version_key(v: str) -> list[Any]:
    return [int(p) if p.isdigit() else p for p in re.split(r"[.\-]", v)]


@dataclass
class Module:
    id: str
    title: str
    description: str = ""
    tasks: list[Task] = field(default_factory=list)
    tools: list[Tool] = field(default_factory=list)
    viewers: list[Viewer] = field(default_factory=list)
    kinds: list[ElementKind] = field(default_factory=list)
    file_kinds: list[FileKind] = field(default_factory=list)
    toolchains: list[Toolchain] = field(default_factory=list)
    checks: Callable[[], list[Check]] | None = None

    def task(self, local_id: str) -> Task | None:
        return next((t for t in self.tasks if t.id == local_id), None)

    def health(self) -> list[Check]:
        found = []
        for tc in self.toolchains:
            versions = tc.versions()
            found.append(
                Check(
                    f"toolchain {tc.name}",
                    bool(versions),
                    ", ".join(versions) or f"not in {tc.root / tc.name}",
                )
            )
        if self.checks:
            found.extend(self.checks())
        return found
