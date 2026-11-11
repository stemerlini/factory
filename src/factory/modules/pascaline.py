"""Pascaline: the notebook calculator, for the quick sums every stage needs."""

from __future__ import annotations

import shutil

from .api import Check, FileKind, Module, Tool


def checks() -> list[Check]:
    path = shutil.which("pascaline")
    return [Check("pascaline", bool(path), path or "not on PATH")]


module = Module(
    id="pascaline",
    title="Pascaline",
    description="Calculations notebook.",
    tools=[Tool("open", "Pascaline", lambda f: ["pascaline"])],
    file_kinds=[FileKind("Calculation", ("calc/*.json", "calc/*.md"), "∑")],
    checks=checks,
)
