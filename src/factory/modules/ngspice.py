"""ngspice: circuit simulation. ``.meas`` results become metrics, so a requirement like
``noise_enc <= 300 e-`` is verified straight from the netlist."""

from __future__ import annotations

import re
import shutil

from ..model import Metric
from .api import Check, Command, FileKind, Module, Param, RunContext, Task

MEAS = re.compile(r"^\s*([A-Za-z_]\w*)\s*=\s*([-+]?\d[\d.]*(?:[eE][-+]?\d+)?)", re.M)


def run_command(ctx: RunContext) -> Command:
    deck = ctx.study.dir / ctx.params["deck"]
    return Command(["ngspice", "-b", "-o", str(ctx.outputs_dir / "ngspice.log"), str(deck)],
                   cwd=ctx.study.dir)


def collect(ctx: RunContext) -> dict[str, Metric]:
    """``name = value`` lines that ``.meas`` prints."""
    text = (ctx.outputs_dir / "ngspice.log").read_text(errors="replace")
    return {m.group(1).lower(): Metric(float(m.group(2))) for m in MEAS.finditer(text)}


def checks() -> list[Check]:
    path = shutil.which("ngspice")
    return [Check("ngspice", bool(path), path or "not installed (pacman -S ngspice)")]


module = Module(
    id="ngspice",
    title="ngspice",
    description="SPICE circuit simulation; .meas results as metrics.",
    tasks=[Task(id="run", title="SPICE simulation", command=run_command, collect=collect,
                params=(Param("deck", str, "circuit.cir", help="netlist in the study folder"),),
                outputs=("ngspice.log",))],
    file_kinds=[FileKind("SPICE netlist", ("*.cir", "*.sp", "*.spice"), "∿")],
    checks=checks,
)
