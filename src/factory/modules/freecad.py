"""Mechanical design: FreeCAD models, STEP exchange. FEA tasks come later (CalculiX/Elmer)."""

from __future__ import annotations

import shutil

from .api import Check, ElementKind, FileKind, Module, Tool


def checks() -> list[Check]:
    path = shutil.which("freecad")
    return [Check("freecad", bool(path), path or "not installed (pacman -S freecad)")]


module = Module(
    id="freecad",
    title="FreeCAD",
    description="3D mechanical design, STEP export, drawings.",
    kinds=[ElementKind("mechanical", "Mechanical", "▦", ("freecad", "script", "pascaline"),
                       "ME", "Structures, housings, mechanisms.")],
    tools=[Tool("open", "FreeCAD", lambda f: ["freecad", str(f)], globs=("*.FCStd",))],
    file_kinds=[
        FileKind("FreeCAD model", ("*.FCStd",), "▦"),
        FileKind("STEP model", ("*.step", "*.stp"), "▦"),
    ],
    checks=checks,
)
