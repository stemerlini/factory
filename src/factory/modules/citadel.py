"""Citadel: the literature library. The project keeps citations, Citadel keeps the papers."""

from __future__ import annotations

import shutil

from .api import Check, FileKind, Module, Tool


def checks() -> list[Check]:
    path = shutil.which("citadel")
    return [Check("citadel", bool(path), path or "not on PATH")]


module = Module(
    id="citadel",
    title="Citadel",
    description="Literature: references, notes, citations.",
    tools=[Tool("open", "Citadel", lambda f: ["citadel"])],
    file_kinds=[FileKind("Bibliography", ("*.bib",), "❡")],
    checks=checks,
)
