"""Where modules come from: the built-ins below plus the ``factory.modules`` entry points."""

from __future__ import annotations

from functools import cache
from importlib import import_module
from importlib.metadata import entry_points

from .api import ElementKind, Module, Task, Viewer

BUILTIN = (
    "core", "script", "geant4", "irene", "pic", "ngspice", "kicad", "freecad", "software",
    "publication",
    "pascaline",
    "citadel",
)


@cache
def all_modules() -> dict[str, Module]:
    found: dict[str, Module] = {}
    for name in BUILTIN:
        mod = import_module(f"factory.modules.{name}").module
        found[mod.id] = mod
    for ep in entry_points(group="factory.modules"):
        mod = ep.load()
        mod = mod() if callable(mod) else mod
        found[mod.id] = mod
    return found


def get(module_id: str) -> Module:
    try:
        return all_modules()[module_id]
    except KeyError:
        raise LookupError(f"unknown module {module_id!r}") from None


def find_task(task_id: str) -> tuple[Module, Task]:
    """``"geant4.run"`` → (geant4 module, run task); ``"script"`` → its ``main`` task."""
    module_id, _, local = task_id.partition(".")
    mod = get(module_id)
    task = mod.task(local or "main")
    if task is None:
        raise LookupError(f"module {module_id!r} has no task {local or 'main'!r}")
    return mod, task


def kinds() -> dict[str, ElementKind]:
    return {k.id: k for m in all_modules().values() for k in m.kinds}


def kind(kind_id: str) -> ElementKind:
    return kinds().get(kind_id) or kinds()["generic"]


def viewer_for(path) -> Viewer | None:
    """The first available viewer whose globs match the file."""
    for m in all_modules().values():
        for v in m.viewers:
            if any(path.match(g) for g in v.globs) and v.available():
                return v
    return None
