"""Running studies and keeping their records.

A run's *record* (``studies/<study>/runs/<run-id>.json``) is small and lives in git: it is
the provenance. Its *data* (``log.txt``, ``outputs/``, ``metrics.json``) lives in the run
store outside git. Runs are separate processes started in their own session, so they
survive the TUI closing; the TUI only reads records.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from .model import Metric, Run, Study

if TYPE_CHECKING:
    from .project import Project


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def new_id() -> str:
    """``20261012-222559-1f0a``: sorts by time; the suffix is 3 hex digits of milliseconds
    plus one random digit, so runs started in the same second still sort in order."""
    t = datetime.now()
    return t.strftime("%Y%m%d-%H%M%S-") + f"{t.microsecond // 1000:03x}{secrets.token_hex(1)[0]}"


def records_dir(study: Study) -> Path:
    return study.dir / "runs"


def run_dir(project: Project, run: Run) -> Path:
    return project.store / run.study / run.id


def save(study: Study, run: Run) -> None:
    d = records_dir(study)
    d.mkdir(exist_ok=True)
    tmp = d / f".{run.id}.json.tmp"
    tmp.write_text(json.dumps(run.to_dict(), indent=2, default=str) + "\n")
    tmp.replace(d / f"{run.id}.json")


def history(project: Project, study: Study) -> list[Run]:
    """All runs of a study, oldest first. Runs whose process died are reported as failed."""
    d = records_dir(study)
    if not d.is_dir():
        return []
    found = []
    for f in sorted(d.glob("*.json")):
        try:
            run = Run.from_dict(json.loads(f.read_text()))
        except (ValueError, TypeError):
            continue
        if run.status in ("queued", "running") and not _alive(run.pid):
            run.status, run.error = "failed", "interrupted (process is gone)"
        found.append(run)
    return found


def latest(project: Project, study: Study, status: str | None = None) -> Run | None:
    for run in reversed(history(project, study)):
        if status is None or run.status == status:
            return run
    return None


def get(project: Project, run_id: str) -> tuple[Study, Run]:
    for study in project.studies():
        f = records_dir(study) / f"{run_id}.json"
        if f.exists():
            return study, Run.from_dict(json.loads(f.read_text()))
    raise LookupError(f"no run {run_id!r}")


def _alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


# --- execution -----------------------------------------------------------------------------


def start(project: Project, study: Study) -> str:
    """Start a run in the background (own session) and return its id."""
    run_id = new_id()
    log = project.store / study.key / run_id / "log.txt"
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "ab") as out:
        subprocess.Popen(
            [sys.executable, "-m", "factory.runs", str(project.root), study.key, run_id],
            stdout=out,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    return run_id


def execute(project: Project, study: Study, run_id: str | None = None) -> Run:
    """Run a study in this process and return its finished record."""
    from . import pipeline
    from .modules import registry
    from .modules.api import RunContext

    run_id = run_id or new_id()
    commit, dirty = project.commit()
    run = Run(
        id=run_id,
        study=study.key,
        status="running",
        fingerprint="",
        started=now(),
        commit=commit,
        dirty=dirty,
        host=socket.gethostname(),
        pid=os.getpid(),
    )
    rdir = run_dir(project, run)
    (rdir / "outputs").mkdir(parents=True, exist_ok=True)
    log = rdir / "log.txt"
    save(study, run)

    try:
        _, task = registry.find_task(study.task)
        params = task.resolve_params(study.params)
        run.params = params
        run.fingerprint = pipeline.fingerprint(project, study)
        tc_name = task.toolchain_for(params)
        env = dict(os.environ)
        if tc_name:
            version = pipeline.toolchain_version(project, tc_name)
            tc = next(t for m in registry.all_modules().values() for t in m.toolchains
                      if t.name == tc_name)
            env.update(tc.environment(version))
            run.toolchain = {tc_name: str(version)}
        ctx = RunContext(project, study, run_id, rdir, params,
                         pipeline.resolve_inputs(project, study), env)
        cmd = task.command(ctx)
        env.update(cmd.env)
        save(study, run)

        with open(log, "ab") as out:
            out.write(f"$ {' '.join(cmd.argv)}\n".encode())
            out.flush()
            try:
                proc = subprocess.Popen(cmd.argv, cwd=cmd.cwd or study.dir, env=env,
                                        stdout=out, stderr=subprocess.STDOUT,
                                        stdin=subprocess.DEVNULL, start_new_session=True)
            except FileNotFoundError:
                raise RuntimeError(f"{cmd.argv[0]} is not installed or not on PATH "
                                   "(see the Modules tab)") from None
            run.pid = proc.pid
            save(study, run)
            run.returncode = proc.wait()

        if run.returncode == 0:
            run.metrics = _read_metrics(rdir / "metrics.json")
            if task.collect:
                run.metrics.update(task.collect(ctx))
            run.outputs = _hash_outputs(rdir / "outputs")
            run.status = "done"
        elif run.returncode < 0:
            run.status, run.error = "cancelled", f"signal {-run.returncode}"
        else:
            run.status, run.error = "failed", f"exit code {run.returncode}"
    except Exception as e:  # noqa: BLE001 - every failure must end up in the record
        run.status, run.error = "failed", f"{type(e).__name__}: {e}"
        with open(log, "a") as out:
            out.write(f"factory: {run.error}\n")
    run.pid = None
    run.finished = now()
    save(study, run)
    return run


def cancel(project: Project, run_id: str) -> bool:
    study, run = get(project, run_id)
    if run.status != "running" or not _alive(run.pid):
        return False
    os.killpg(os.getpgid(run.pid), signal.SIGTERM)
    return True


def _read_metrics(path: Path) -> dict[str, Metric]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    return {k: Metric(float(v["value"]), v.get("unit", "")) if isinstance(v, dict)
            else Metric(float(v)) for k, v in data.items()}


def _hash_outputs(outputs: Path) -> dict[str, str]:
    found = {}
    for f in sorted(outputs.rglob("*")):
        if f.is_file():
            h = hashlib.sha256()
            with open(f, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
            found[str(f.relative_to(outputs))] = h.hexdigest()
    return found


if __name__ == "__main__":  # the background worker started by start()
    from .project import Project as _Project

    _p = _Project.load(sys.argv[1])
    _r = execute(_p, _p.study(sys.argv[2]), sys.argv[3])
    sys.exit(0 if _r.status == "done" else 1)
