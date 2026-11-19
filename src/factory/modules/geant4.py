"""Geant4: Monte Carlo transport of particles through matter.

``geant4.run`` runs any Geant4 application with a macro. With the default ``factory-g4``
(``src/factory/modules/g4app``, built by ``tools/build-factory-g4``) a study needs no C++: the
geometry is a GDML file or a layer stack (``stack.yaml``, see ``stack.py``) given as the
``stack`` input, the source is a GPS macro, and ``events.csv`` holds one row per event with the
energy deposited in each named volume plus ``back`` and ``escape`` flags of the primary.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from . import scene
from . import stack as stacks
from .api import Check, Command, FileKind, Module, Param, RunContext, Task, Tool, Toolchain

TOOLCHAIN = Toolchain("geant4", env_script="bin/geant4.sh", executable="bin/geant4-config")


def threads_value(v) -> int | str:
    """A thread count, or ``auto``."""
    if str(v).strip().lower() == "auto":
        return "auto"
    n = int(v)
    if n < 1:
        raise ValueError("threads must be a positive number or 'auto'")
    return n


def _in_flight(ctx: RunContext) -> int:
    from .. import runs

    return max(1, sum(1 for s in ctx.project.studies()
                      for r in runs.history(ctx.project, s) if r.status == "running"))


def resolve_threads(ctx: RunContext) -> int | None:
    """Worker threads: the study's ``threads``, else ``[geant4] threads`` of factory.toml, else
    None (the application's default). ``auto`` = the cores this process may use, divided by the
    runs in flight."""
    value = ctx.params.get("threads")
    if value is None and "threads" in ctx.project.geant4:
        value = threads_value(ctx.project.geant4["threads"])
    if value == "auto":
        cores = len(os.sched_getaffinity(0))
        value = max(1, cores // _in_flight(ctx))
    return value


def run_command(ctx: RunContext) -> Command:
    app = ctx.params["app"]
    macro = ctx.study.dir / ctx.params["macro"]
    argv = [app]
    geometry = ctx.inputs.get("stack") or ctx.inputs.get("geometry")
    if geometry is not None:
        gdml, names = geometry, []
        if geometry.suffix.lower() != ".gdml":
            spec = stacks.load(geometry)
            ctx.outputs_dir.mkdir(parents=True, exist_ok=True)
            cad = stacks.fetch(spec, ctx.run_dir / "inputs")
            gdml, names = ctx.outputs_dir / "geometry.gdml", stacks.sensitive(spec, cad)
            gdml.write_text(stacks.to_gdml(spec, cad))
            if cad:
                stacks.save_cad(cad, ctx.outputs_dir / "cad.json")
        names = [n.strip() for n in str(ctx.params["sensitive"]).split(",") if n.strip()] or names
        argv += ["--gdml", str(gdml), "--physics", ctx.params["physics"]]
        if ctx.params["tracks"]:
            argv += ["--tracks", str(ctx.params["tracks"])]
        if names:
            argv += ["--sensitive", ",".join(names)]
    argv.append(str(macro))
    threads = resolve_threads(ctx)
    if threads is not None:
        ctx.params["threads"] = threads  # the run record keeps the count actually used
        argv += ["-t", str(threads)]
    return Command(argv, env={"FACTORY_OUTPUTS": str(ctx.outputs_dir)}, cwd=ctx.outputs_dir)


def _stack_input(ctx: RunContext) -> dict | None:
    geometry = ctx.inputs.get("stack") or ctx.inputs.get("geometry")
    if geometry is None or geometry.suffix.lower() == ".gdml":
        return None
    return stacks.load(geometry)


def collect(ctx: RunContext) -> dict:
    """No metrics; builds ``scene.vtk`` (outlines and the recorded tracks) for the 3D viewer."""
    tracks = ctx.outputs_dir / "tracks.csv"
    if tracks.is_file():
        scene.build(_stack_input(ctx), tracks, ctx.outputs_dir / "scene.vtk",
                    stacks.read_cad(ctx.outputs_dir / "cad.json"))
    return {}


def preview(stack_file: Path) -> list[str]:
    """Open a stack in f3d: its volume outlines, front face at z = 0."""
    out = Path(tempfile.gettempdir()) / f"factory-{stack_file.stem}.vtk"
    scene.build(stacks.load(stack_file), None, out)
    return scene.f3d_argv(out)


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
                Param("threads", threads_value, None,
                      help="worker threads: N or auto (cores / runs in flight); default "
                      "[geant4] threads in factory.toml, else the app's (factory-g4: all cores)"),
                Param("physics", str, "FTFP_BERT_EMZ", help="reference physics list (factory-g4)"),
                Param("tracks", int, 20, help="events whose tracks are drawn in scene.vtk "
                      "(0 = none; factory-g4 with a stack or GDML)"),
                Param("sensitive", str, "", help="volumes to record, comma-separated "
                      "(default: the stack's layers, or every volume in the world of a GDML)"),
            ),
            outputs=("events.csv", "tracks.csv", "scene.vtk"),
            collect=collect,
        )
    ],
    tools=[
        Tool("stack-view", "3D view of the stack", preview,
             globs=("stack.yaml", "*-stack.yaml", "stack-*.yaml"))
    ],
    file_kinds=[
        FileKind("Geant4 macro", ("*.mac",), "▸"),
        FileKind("GDML geometry", ("*.gdml",), "◇"),
    ],
    toolchains=[TOOLCHAIN],
    checks=checks,
)
