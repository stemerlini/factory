"""KiCad: schematics and PCBs. ERC runs as a study, so "no ERC violations" can be a
verified requirement."""

from __future__ import annotations

import json
import shutil

from ..model import Metric
from .api import Check, Command, ElementKind, FileKind, Module, Param, RunContext, Task, Tool


def erc_command(ctx: RunContext) -> Command:
    sch = ctx.element_dir / ctx.params["schematic"]
    report = ctx.outputs_dir / "erc.json"
    return Command(
        ["kicad-cli", "sch", "erc", "--format", "json", "-o", str(report), str(sch)],
        cwd=ctx.element_dir,
    )


def erc_collect(ctx: RunContext) -> dict[str, Metric]:
    data = json.loads((ctx.outputs_dir / "erc.json").read_text())
    errors = sum(
        1
        for sheet in data.get("sheets", [])
        for v in sheet.get("violations", [])
        if v.get("severity") == "error"
    )
    return {"erc_errors": Metric(errors)}


def checks() -> list[Check]:
    path = shutil.which("kicad-cli")
    return [Check("kicad-cli", bool(path), path or "not installed (pacman -S kicad)")]


module = Module(
    id="kicad",
    title="KiCad",
    description="Schematics, PCB layout, ERC/DRC.",
    kinds=[ElementKind("electronics", "Electronics", "⏚", ("kicad", "ngspice", "script"),
                       "EL", "Circuits and boards.")],
    tasks=[
        Task(
            id="erc",
            title="Electrical rules check",
            command=erc_command,
            collect=erc_collect,
            params=(Param("schematic", str, required=True, help="*.kicad_sch path"),),
            outputs=("erc.json",),
        )
    ],
    tools=[Tool("open", "KiCad", lambda f: ["kicad", str(f)], globs=("*.kicad_pro",))],
    file_kinds=[
        FileKind("KiCad project", ("*.kicad_pro",), "⏚"),
        FileKind("Schematic", ("*.kicad_sch",), "⏚"),
        FileKind("PCB", ("*.kicad_pcb",), "▦"),
    ],
    checks=checks,
)
