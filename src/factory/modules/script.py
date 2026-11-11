"""The escape hatch: run any Python script as a study.

The script gets its context through environment variables and writes metrics to
``$FACTORY_OUTPUTS/../metrics.json`` (or ``factory.metrics.write``). Any analysis starts
here and becomes a module only when it is reused across projects.
"""

from __future__ import annotations

import json
import sys

from .api import Command, FileKind, Module, Param, RunContext, Task


def command(ctx: RunContext) -> Command:
    script = ctx.study.dir / ctx.params["file"]
    interpreter = ctx.params.get("python") or sys.executable
    return Command(
        [interpreter, str(script)],
        env={
            "FACTORY_PROJECT": str(ctx.project.root),
            "FACTORY_ELEMENT": str(ctx.element_dir),
            "FACTORY_STUDY": ctx.study.key,
            "FACTORY_RUN_ID": ctx.run_id,
            "FACTORY_RUN_DIR": str(ctx.run_dir),
            "FACTORY_OUTPUTS": str(ctx.outputs_dir),
            "FACTORY_PARAMS": json.dumps(ctx.params),
            "FACTORY_INPUTS": json.dumps({k: str(v) for k, v in ctx.inputs.items()}),
        },
        cwd=ctx.study.dir,
    )


module = Module(
    id="script",
    title="Script",
    description="Run a Python script with the study's inputs, parameters and outputs.",
    tasks=[
        Task(
            id="main",
            title="Python script",
            command=command,
            params=(
                Param("file", str, "run.py", help="script, relative to the study folder"),
                Param("python", str, None, help="interpreter (default: Factory's)"),
            ),
        )
    ],
    file_kinds=[FileKind("Python", ("*.py",), "λ"), FileKind("Notebook", ("*.ipynb",), "λ")],
)
