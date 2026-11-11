"""Geant4: Monte Carlo transport of particles through matter.

Phase 0 runs any Geant4 application with a macro. Phase 2 adds ``factory-g4``, one generic
application (GDML geometry, GPS source, per-volume energy deposits to HDF5), so studies
need no C++.
"""

from __future__ import annotations

import os

from .api import Check, Command, FileKind, Module, Param, RunContext, Task, Tool, Toolchain

TOOLCHAIN = Toolchain("geant4", env_script="bin/geant4.sh", executable="bin/geant4-config")


def run_command(ctx: RunContext) -> Command:
    app = ctx.params["app"]
    macro = ctx.study.dir / ctx.params["macro"]
    argv = [app, str(macro)]
    if ctx.params.get("threads"):
        argv += ["-t", str(ctx.params["threads"])]
    return Command(argv, env={"FACTORY_OUTPUTS": str(ctx.outputs_dir)}, cwd=ctx.outputs_dir)


def checks() -> list[Check]:
    prefix = TOOLCHAIN.prefix()
    if prefix is None:
        return []
    try:
        env = TOOLCHAIN.environment()
    except Exception as e:  # noqa: BLE001 - surface any failure as a health problem
        return [Check("geant4 environment", False, str(e))]
    data = [k for k in env if k.startswith("G4") and k.endswith("DATA")]
    missing = [k for k in data if not os.path.isdir(env[k])]
    return [
        Check(
            "geant4 datasets",
            bool(data) and not missing,
            f"{len(data)} found" + (f", missing: {', '.join(missing)}" if missing else ""),
        )
    ]


module = Module(
    id="geant4",
    title="Geant4",
    description="Monte Carlo particle transport (detectors, shielding, dose).",
    tasks=[
        Task(
            id="run",
            title="Geant4 application",
            command=run_command,
            toolchain="geant4",
            params=(
                Param("app", str, "factory-g4", help="Geant4 executable"),
                Param("macro", str, "run.mac", help="macro, relative to the study folder"),
                Param("threads", int, None, help="worker threads"),
            ),
        )
    ],
    tools=[
        Tool(
            "viewer",
            "Geant4 Qt viewer",
            lambda f: ["factory-g4", "--gui", str(f)],
            globs=("*.gdml",),
            toolchain="geant4",
        )
    ],
    file_kinds=[
        FileKind("Geant4 macro", ("*.mac",), "▸"),
        FileKind("GDML geometry", ("*.gdml",), "◇"),
    ],
    toolchains=[TOOLCHAIN],
    checks=checks,
)
