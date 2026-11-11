"""A project: a git repository with ``factory.toml``, ``req/`` and ``studies/``."""

from __future__ import annotations

import re
import shutil
import subprocess
import tomllib
from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path
from typing import Any

import tomli_w
import yaml

from .model import GATE_IDS, METHODS, STUDY_GATES, Criterion, Element, Requirement, Study

MANIFEST = "factory.toml"
ELEMENT = "element.md"
DEFAULT_STORE = Path.home() / "Data/factory"
FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n?(.*)\Z", re.S)
STUDY_DIR = re.compile(r"^(S\d{3,})(?:-.*)?$")


class ProjectError(Exception):
    pass


@dataclass
class Project:
    root: Path
    id: str
    name: str
    template: str = "hardware"
    description: str = ""
    modules: list[str] = field(default_factory=list)
    toolchains: dict[str, str] = field(default_factory=dict)
    store_root: Path = DEFAULT_STORE
    vault_folder: str = ""  # folder inside the Obsidian vault; default Projects/<name>
    agent: str = ""  # the agent this project works with ([agent] default), else the user's

    # --- loading ---------------------------------------------------------------------------

    @classmethod
    def load(cls, path: Path | str = ".") -> Project:
        root = find_root(Path(path))
        try:
            data = tomllib.loads((root / MANIFEST).read_text())
        except tomllib.TOMLDecodeError as e:
            raise ProjectError(f"{root / MANIFEST}: {e}") from None
        p = data.get("project", {})
        store = data.get("store", {}).get("root")
        return cls(
            root=root,
            id=p.get("id") or root.name,
            name=p.get("name") or root.name,
            template=p.get("template", "hardware"),
            description=p.get("description", ""),
            modules=list(data.get("modules", {}).get("enabled", [])),
            toolchains=dict(data.get("toolchains", {})),
            store_root=Path(store).expanduser() if store else DEFAULT_STORE,
            vault_folder=data.get("obsidian", {}).get("folder", ""),
            agent=data.get("agent", {}).get("default", ""),
        )

    @property
    def store(self) -> Path:
        """This project's run store (outside git)."""
        return self.store_root / self.id

    # --- elements --------------------------------------------------------------------------

    def tree(self) -> Element:
        """The product breakdown, read from disk (root element first)."""
        return read_element(self.root, "")

    def elements(self) -> list[Element]:
        return list(self.tree().walk())

    def element(self, path: str) -> Element:
        for e in self.elements():
            if e.path == path.strip("/"):
                return e
        raise ProjectError(f"no element {path!r}")

    def vault_dir(self, element: Element | None = None) -> Path | None:
        """The element's folder in the Obsidian vault, or None if no vault is configured."""
        from . import config

        vault = config.vault()
        if vault is None:
            return None
        base = vault / (self.vault_folder or f"Projects/{self.name}")
        return base / element.path if element and element.path else base

    # --- requirements ----------------------------------------------------------------------

    def requirements(self, element: Element | None = None) -> list[Requirement]:
        found = []
        for e in [element] if element else self.elements():
            for f in sorted((e.dir / "req").glob("*.md")):
                r = read_requirement(f, e.path)
                if r.status != "deleted":
                    found.append(r)
        return found

    def requirement(self, req_id: str) -> Requirement | None:
        return next((r for r in self.requirements() if r.id == req_id), None)

    # --- studies ---------------------------------------------------------------------------

    def studies(self, element: Element | None = None) -> list[Study]:
        found = []
        for e in [element] if element else self.elements():
            base = e.dir / "studies"
            if base.is_dir():
                found += [read_study(d, e.path) for d in sorted(base.iterdir())
                          if (d / "study.toml").is_file()]
        return found

    def study(self, key: str) -> Study:
        key = key.strip("/")
        for s in self.studies():
            if s.key == key:
                return s
        raise ProjectError(f"no study {key!r}")

    # --- git -------------------------------------------------------------------------------

    def git(self, *args: str) -> str | None:
        try:
            out = subprocess.run(
                ["git", "-C", str(self.root), *args], capture_output=True, text=True, check=True
            )
        except (OSError, subprocess.CalledProcessError):
            return None
        return out.stdout.strip()

    def git_checked(self, *args: str) -> str:
        """Run git and raise ProjectError with git's own message if it fails."""
        out = subprocess.run(["git", "-C", str(self.root), *args], capture_output=True, text=True)
        if out.returncode:
            msg = (out.stderr.strip().splitlines() or ["failed"])[-1]
            raise ProjectError(f"git {args[0]}: {msg}")
        return out.stdout.strip()

    def commit(self) -> tuple[str | None, bool]:
        """Current commit and whether the working tree has uncommitted changes."""
        head = self.git("rev-parse", "--short=12", "HEAD")
        dirty = bool(self.git("status", "--porcelain"))
        return head, dirty

    def branch(self) -> str | None:
        return self.git("branch", "--show-current")


def find_root(start: Path) -> Path:
    start = start.expanduser().resolve()
    for d in (start, *start.parents):
        if (d / MANIFEST).is_file():
            return d
    raise ProjectError(f"not inside a Factory project (no {MANIFEST} in {start} or above)")


def read_frontmatter(path: Path) -> tuple[dict[str, Any], str]:
    text = path.read_text()
    m = FRONTMATTER.match(text)
    if not m:
        return {}, text
    return yaml.safe_load(m.group(1)) or {}, m.group(2).strip()


def read_element(folder: Path, path: str) -> Element:
    f = folder / ELEMENT
    meta, body = read_frontmatter(f) if f.is_file() else ({}, "")
    skip = tuple(meta.get("skip") or ())
    unknown = [g for g in skip if g not in GATE_IDS]
    if unknown:
        raise ProjectError(f"{f}: unknown gates in skip: {', '.join(unknown)}")
    element = Element(
        path=path,
        dir=folder,
        prefix=str(meta.get("prefix") or ("SYS" if not path else path.upper()[:3])),
        title=str(meta.get("title") or folder.name),
        kind=str(meta.get("kind") or ("system" if not path else "generic")),
        description=body,
        skip=skip,
        references=tuple(meta.get("references") or ()),
    )
    children = folder / "elements"
    if children.is_dir():
        for d in sorted(children.iterdir()):
            if (d / ELEMENT).is_file():
                element.children.append(read_element(d, f"{path}/{d.name}".strip("/")))
    return element


def read_requirement(path: Path, element: str = "") -> Requirement:
    meta, body = read_frontmatter(path)
    method = meta.get("method", "analysis")
    if method not in METHODS:
        raise ProjectError(f"{path}: method must be one of {', '.join(METHODS)}")
    crit = meta.get("criterion")
    try:
        criterion = Criterion.from_dict(crit) if crit else None
    except (KeyError, ValueError) as e:
        raise ProjectError(f"{path}: bad criterion: {e}") from None
    allocated = meta.get("allocated") or ()
    return Requirement(
        id=str(meta.get("id") or path.stem),
        title=str(meta.get("title", "")),
        element=element,
        text=body,
        method=method,
        status=meta.get("status", "draft"),
        criterion=criterion,
        parent=meta.get("parent"),
        allocated=(allocated,) if isinstance(allocated, str) else tuple(allocated),
        path=path,
    )


def read_study(folder: Path, element: str = "") -> Study:
    path = folder / "study.toml"
    try:
        d = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as e:
        raise ProjectError(f"{path}: {e}") from None
    m = STUDY_DIR.match(folder.name)
    study_id = d.get("id") or (m.group(1) if m else folder.name)
    if "task" not in d:
        raise ProjectError(f"{path}: missing 'task'")
    gate = d.get("gate", "analyse")
    if gate not in STUDY_GATES:
        raise ProjectError(f"{path}: gate must be one of {', '.join(STUDY_GATES)}")
    return Study(
        id=study_id,
        title=d.get("title", folder.name),
        task=d["task"],
        dir=folder,
        element=element,
        gate=gate,
        params=dict(d.get("params", {})),
        inputs={k: str(v) for k, v in d.get("inputs", {}).items()},
        verifies=list(d.get("verifies", [])),
        description=d.get("description", ""),
    )


def write_study(folder: Path, study: dict[str, Any]) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "study.toml"
    path.write_text(tomli_w.dumps(study))
    return path


# --- templates -----------------------------------------------------------------------------


def templates() -> dict[str, str]:
    """Template id → one-line description."""
    found = {}
    for t in files("factory.templates").iterdir():
        manifest = t / MANIFEST
        if t.is_dir() and manifest.is_file():
            found[t.name] = tomllib.loads(manifest.read_text())["project"].get("description", "")
    return dict(sorted(found.items()))


KICKOFF = ".factory/kickoff"


def create(target: Path, template: str = "hardware", name: str | None = None,
           project_id: str | None = None, git: bool = True, idea: str = "") -> Project:
    """Make a new project from a template: a name under ``factory/templates``, or the path
    of any folder laid out like one. With an ``idea``, it becomes the system's description
    and the project is marked for a kickoff: the first time Factory opens it, the agent
    interviews the user and drafts the rest from it."""
    target = target.expanduser().resolve()
    if target.exists() and any(target.iterdir()):
        raise ProjectError(f"{target} exists and is not empty")
    path = Path(template).expanduser()
    source = path if (path / MANIFEST).is_file() else files("factory.templates") / template
    if not (source / MANIFEST).is_file():
        raise ProjectError(f"unknown template {template!r} (have: {', '.join(templates())})")
    shutil.copytree(str(source), target, dirs_exist_ok=True)

    manifest = target / MANIFEST
    data = tomllib.loads(manifest.read_text())
    data["project"]["id"] = project_id or slug(target.name)
    data["project"]["name"] = name or data["project"].get("name") or target.name
    data["project"]["template"] = Path(template).name
    manifest.write_text(tomli_w.dumps(data))
    (target / ".gitignore").write_text(".factory/\n__pycache__/\n")
    if idea.strip():
        root_md = target / "element.md"
        meta, _ = read_frontmatter(root_md)
        root_md.write_text("---\n" + yaml.safe_dump(meta, sort_keys=False, allow_unicode=True)
                           + "---\n" + idea.strip() + "\n")
        (target / KICKOFF).parent.mkdir(exist_ok=True)
        (target / KICKOFF).write_text(idea.strip() + "\n")
    from . import agent

    for rel, text in agent.GUARDRAILS:
        (target / rel).parent.mkdir(parents=True, exist_ok=True)
        (target / rel).write_text(text)

    if git and shutil.which("git"):
        subprocess.run(["git", "init", "-q", str(target)], check=True)
        # The template is the starting point, not work to review: commit it, so the Review
        # tab starts empty and the journal starts with the project. Without a git identity
        # the commit fails and the files stay uncommitted, as before.
        subprocess.run(["git", "-C", str(target), "add", "-A"], check=False)
        subprocess.run(["git", "-C", str(target), "commit", "-q", "-m",
                        f"Start {data['project']['name']} from the "
                        f"{data['project']['template']} template"],
                       check=False, capture_output=True)
    return Project.load(target)


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "project"
