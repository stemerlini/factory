"""Particle-in-cell plasma codes (WarpX, Smilei). A second engine that keeps the core
honest: nothing in Factory's core changes to support it."""

from __future__ import annotations

from .api import Command, FileKind, Module, Param, RunContext, Task, Toolchain

CODES = {"warpx": "warpx.3d", "smilei": "smilei"}  # executables in <prefix>/bin


def run_command(ctx: RunContext) -> Command:
    code = ctx.params["code"]
    if code not in CODES:
        raise ValueError(f"unknown PIC code {code!r} (known: {', '.join(CODES)})")
    deck = ctx.study.dir / ctx.params["input"]
    argv = [CODES[code], str(deck)]
    if ctx.params.get("ranks", 1) > 1:
        argv = ["mpirun", "-n", str(ctx.params["ranks"]), *argv]
    return Command(argv, cwd=ctx.outputs_dir)


module = Module(
    id="pic",
    title="Particle-in-cell",
    description="Kinetic plasma simulation (thrusters, plumes, sheaths).",
    tasks=[
        Task(
            id="run",
            title="PIC simulation",
            command=run_command,
            toolchain=lambda p: p["code"],
            params=(
                Param("code", str, "warpx", help="warpx | smilei"),
                Param("input", str, "input.deck", help="input deck in the study folder"),
                Param("ranks", int, 1, help="MPI ranks"),
            ),
        )
    ],
    file_kinds=[FileKind("PIC input", ("*.deck", "inputs_*", "*.smilei.py"), "≈")],
    toolchains=[Toolchain("warpx"), Toolchain("smilei")],
)
