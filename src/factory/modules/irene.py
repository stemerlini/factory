"""IRENE (AE9/AP9/SPM): the trapped radiation environment along an orbit.

One task, ``irene.environment``: Factory writes the ``CmdLineIrene`` input from the study's
parameters (orbit elements, models, statistic, energies, shielding depths), runs it once per
model and flux type, and turns the output into what the rest of a project uses:

- ``spectrum_e.csv`` / ``spectrum_p.csv``: orbit-average and peak integral flux and the
  average differential flux per energy (the columns the AE-8/AP-8 script studies wrote, so
  downstream studies take either);
- ``gps_e.mac`` / ``gps_p.mac``: the average differential spectrum as a Geant4 GPS
  histogram, for transport studies;
- ``dose_depth.csv`` and ``tid_<d>mm`` metrics (krad, over ``mission_years``) when
  ``dose_depths_mm`` is set (ShieldDose2, aluminium shielding);
- the raw IRENE files under ``irene/``.

The model runs in its own process (``python -m factory.modules.irene``) so the IRENE toolchain
environment (``IRENE_DATA``, ``PATH``) applies to it and nothing else.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import math
import os
import subprocess
import sys
from pathlib import Path

from .api import Check, Command, FileKind, Module, Param, RunContext, Task, Toolchain

TOOLCHAIN_NAME = "irene"
SPECIES = {"AE9": "e", "AE8": "e", "AP9": "p", "AP8": "p"}
LEGACY = {"AE8", "AP8"}
E_GRID = [0.04, 0.07, 0.1, 0.15, 0.25, 0.4, 0.6, 0.8, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0,
          7.0, 8.0, 10.0]
P_GRID = [0.1, 0.25, 0.5, 1.0, 2.0, 3.0, 5.0, 7.0, 10.0, 15.0, 20.0, 30.0, 50.0, 70.0, 100.0,
          150.0, 200.0, 300.0, 400.0, 700.0, 1000.0, 2000.0]
COLUMNS = ["E_MeV", "int_avg_omni_cm2s", "int_peak_omni_cm2s", "int_avg_dir_cm2ssr",
           "diff_avg_omni_cm2sMeV", "frac_time_above_1e3_dir"]
TOOLCHAIN = Toolchain(TOOLCHAIN_NAME, env_script="irene.sh", executable="bin/CmdLineIrene")


def command(ctx: RunContext) -> Command:
    return Command(
        [sys.executable, "-m", "factory.modules.irene"],
        env={
            "FACTORY_RUN_DIR": str(ctx.run_dir),
            "FACTORY_OUTPUTS": str(ctx.outputs_dir),
            "FACTORY_PARAMS": json.dumps(ctx.params),
        },
        cwd=ctx.outputs_dir,
    )


# --- the input file ----------------------------------------------------------------------------


def statistic(params: dict) -> tuple[str, int | None]:
    """("mean", None) or ("percentile", N); with scenarios > 0 the percentile is a Monte Carlo
    confidence level over that many scenarios."""
    stat = str(params["statistic"]).lower()
    if stat == "mean":
        if params["scenarios"]:
            raise ValueError("Monte Carlo scenarios need a percentile statistic, e.g. p95")
        return "mean", None
    if stat.startswith("p") and stat[1:].isdigit() and 1 <= int(stat[1:]) <= 99:
        return "percentile", int(stat[1:])
    raise ValueError(f"statistic must be 'mean' or p1..p99, not {params['statistic']!r}")


def input_text(model: str, flux_type: str, prefix: Path, params: dict, data: str) -> str:
    stat, level = statistic(params)
    if model in LEGACY and stat != "mean":
        raise ValueError(f"{model} gives only the mean; use AE9/AP9 for percentiles")
    energies = params["electron_E_MeV"] if SPECIES[model] == "e" else params["proton_E_MeV"]
    start = dt.datetime.fromisoformat(params["start"])
    end = start + dt.timedelta(days=float(params["days"]))
    perigee = params["perigee_km"]
    apogee = params.get("apogee_km") or perigee

    def when(t: dt.datetime) -> str:
        sod = t.hour * 3600 + t.minute * 60 + t.second
        return f"{t.year}, {t.timetuple().tm_yday}, {sod}"

    lines = [
        f"ModelType: {model}",
        f"ModelDBDir: {data}",
        f"MagfieldDB: {data}/igrfDB.h5",
        f"OutFile: {prefix}.txt",
        f"FluxType: {flux_type}",
        "Energies: " + ", ".join(f"{e:g}" for e in energies),
    ]
    if model in LEGACY:
        lines += [f"ModelDB: {data}/radiationBeltDB.h5",
                  f"LegActLevel: {params['ae8_activity' if model == 'AE8' else 'ap8_activity']}",
                  "LegFixEpoch: true", "LegShiftSAA: true", "FluxOut: true"]
    elif params["scenarios"]:
        lines += [f"FluxOut: montecarlo,1-{int(params['scenarios'])}",
                  f"Aggregate: percent,{level}"]
    elif stat == "percentile":
        lines.append(f"FluxOut: percentile,{level}")
    else:
        lines.append("FluxOut: mean")
    if flux_type == "1PtDiff" and params["dose_depths_mm"]:
        lines += [
            "DoseAccumOut: true",
            "AccumMode: Cumul",
            "DoseDepths: " + ", ".join(f"{d:g}" for d in params["dose_depths_mm"]),
            "DoseDepthUnits: millimeters",
            f"DoseGeometry: {params['dose_geometry']}",
            f"DoseDetector: {params['dose_detector']}",
            "DoseAttnMode: none",
        ]
    lines += [
        f"OrbPropType: {params['propagator']}",
        f"OrbElemTime: {when(start)}",
        f"OrbStart: {when(start)}",
        f"OrbEnd: {when(end)}",
        f"OrbStep: {float(params['step_s']):g}",
        f"OrbInclination: {params['inclination_deg']}",
        f"OrbRAAN: {params['raan_deg']}",
        f"OrbArgPerigee: {params['argp_deg']}",
        f"OrbAltPerigee: {perigee}",
        f"OrbAltApogee: {apogee}",
        f"OrbitFile: {prefix}_ephem.txt",
        "TimeSpec: MJD",
        "CoordSys: GEI",
        "CoordUnits: Re",
        "DataDelim: comma",
    ]
    return "\n".join(lines) + "\n"


def executable(env) -> str:
    """CmdLineIrene by absolute path: it starts its helper programs (FluxTask, ...) from its
    own folder, which it only knows when invoked with one."""
    return str(Path(env["IRENE_HOME"]) / "bin/CmdLineIrene") if env.get("IRENE_HOME") \
        else "CmdLineIrene"


# --- the output files --------------------------------------------------------------------------


def read_table(path: Path) -> list[list[float]]:
    """Rows of values after the time and position columns."""
    rows = []
    for line in path.read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            rows.append([float(x) for x in line.split(",")[4:]])
    return rows


def output_file(prefix: Path, data_type: str, params: dict) -> Path:
    """Find the file IRENE wrote for this data type and the study's statistic."""
    stat, level = statistic(params)
    found = sorted(prefix.parent.glob(f"{prefix.name}_*_{data_type}*.txt"))
    if stat == "mean":
        found = [f for f in found if f"_mean_{data_type}." in f.name]
    else:
        found = [f for f in found if f.stem.endswith(f"_{level}")
                 and ("_pctile_" in f.name or "conf" in f.name)]
    if not found:
        have = ", ".join(f.name for f in sorted(prefix.parent.glob(f"{prefix.name}_*"))) or "none"
        raise FileNotFoundError(f"IRENE wrote no {data_type} file for {params['statistic']} "
                                f"(files: {have})")
    return found[0]


def run_irene(model: str, flux_type: str, work: Path, params: dict, data: str) -> Path:
    prefix = work / f"{model}_{'int' if flux_type == 'Integral' else 'diff'}"
    inp = prefix.with_name(prefix.name + "_input.txt")
    inp.write_text(input_text(model, flux_type, prefix, params, data))
    print(f"\n$ CmdLineIrene -i {inp.name}", flush=True)
    done = subprocess.run([executable(os.environ), "-i", str(inp)], cwd=work)
    if done.returncode != 0:
        raise RuntimeError(f"CmdLineIrene failed for {model} ({flux_type}), "
                           f"exit {done.returncode}; input: {inp}")
    return prefix


def spectrum(model: str, energies: list[float], fint: list[list[float]],
             fdiff: list[list[float]], out: Path) -> dict[str, float]:
    n = len(fint)
    rows = []
    for k, e in enumerate(energies):
        col = [max(r[k], 0.0) for r in fint]
        avg, peak = sum(col) / n, max(col)
        diff = sum(max(r[k], 0.0) for r in fdiff) / len(fdiff)
        above = sum(1 for v in col if v / (4 * math.pi) > 1e3) / n
        rows.append([e, avg, peak, avg / (4 * math.pi), diff, above])
    with out.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(COLUMNS)
        w.writerows(rows)
    print(f"\n{model}: orbit-average and peak integral flux (omnidirectional, /cm2/s)")
    print(f"{'E MeV':>8} {'avg >E':>10} {'peak >E':>10} {'avg dir /cm2/s/sr':>18} "
          f"{'diff avg /MeV':>14}")
    for e, avg, peak, dirn, diff, _ in rows:
        print(f"{e:8.3g} {avg:10.3e} {peak:10.3e} {dirn:18.3e} {diff:14.3e}")
    return {"avg": rows[0][1], "peak": rows[0][2]}


def gps_macro(species: str, model: str, energies: list[float], fdiff: list[list[float]],
              out: Path) -> None:
    """Geant4 GPS energy histogram from the orbit-average differential spectrum."""
    avg = [sum(max(r[k], 0.0) for r in fdiff) / len(fdiff) for k in range(len(energies))]
    points = [(e, f) for e, f in zip(energies, avg, strict=True) if f > 0]
    lines = [f"# {model} orbit-average differential spectrum (omnidirectional, /cm2/s/MeV),",
             "# written by Factory's irene.environment task. Energies in MeV.",
             f"/gps/particle {'e-' if species == 'e' else 'proton'}",
             "/gps/ene/type Arb", "/gps/hist/type arb"]
    lines += [f"/gps/hist/point {e:g} {f:.6e}" for e, f in points]
    lines.append("/gps/hist/inter Log")
    out.write_text("\n".join(lines) + "\n")


def main() -> None:
    params = json.loads(os.environ["FACTORY_PARAMS"])
    out = Path(os.environ["FACTORY_OUTPUTS"])
    data = os.environ.get("IRENE_DATA")
    if not data:
        raise SystemExit("IRENE_DATA is not set: is the irene toolchain installed?")
    work = out / "irene"
    work.mkdir(parents=True, exist_ok=True)
    metrics: dict[str, dict] = {}
    perigee = params["perigee_km"]
    apogee = params.get("apogee_km") or perigee
    print(f"Orbit: {perigee:g} x {apogee:g} km, i = {params['inclination_deg']} deg, "
          f"{params['days']} d from {params['start']}, step {params['step_s']} s; "
          f"models {', '.join(params['models'])}, statistic {params['statistic']}"
          + (f" over {params['scenarios']} Monte Carlo scenarios" if params["scenarios"] else ""))
    doses: dict[str, list[float]] = {}
    for model in params["models"]:
        model = model.upper()
        if model not in SPECIES:
            raise SystemExit(f"unknown model {model!r}; use one of {', '.join(SPECIES)}")
        sp = SPECIES[model]
        energies = params["electron_E_MeV"] if sp == "e" else params["proton_E_MeV"]
        p_diff = run_irene(model, "1PtDiff", work, params, data)
        p_int = run_irene(model, "Integral", work, params, data)
        fdiff = read_table(output_file(p_diff, "flux", params))
        fint = read_table(output_file(p_int, "flux", params))
        top = spectrum(model, energies, fint, fdiff, out / f"spectrum_{sp}.csv")
        gps_macro(sp, model, energies, fdiff, out / f"gps_{sp}.mac")
        tag = "electron" if sp == "e" else "proton"
        metrics[f"{tag}_flux_avg_above_{energies[0]:g}MeV"] = {"value": top["avg"],
                                                               "unit": "1/(cm2 s)"}
        metrics[f"{tag}_flux_peak_above_{energies[0]:g}MeV"] = {"value": top["peak"],
                                                                "unit": "1/(cm2 s)"}
        if params["dose_depths_mm"]:
            accum = read_table(output_file(p_diff, "doseaccum", params))
            doses[model] = accum[-1]
    if doses:
        depths = params["dose_depths_mm"]
        scale = 365.25 * float(params["mission_years"]) / float(params["days"]) / 1000.0
        total = [sum(d[k] for d in doses.values()) * scale for k in range(len(depths))]
        with (out / "dose_depth.csv").open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["depth_mm_Al", *[f"{m}_krad" for m in doses], "total_krad"])
            for k, d in enumerate(depths):
                w.writerow([d, *[v[k] * scale for v in doses.values()], total[k]])
        print(f"\nTotal ionising dose over {params['mission_years']} year(s), "
              f"{params['dose_detector']} behind {params['dose_geometry']} Al:")
        for d, t in zip(depths, total, strict=True):
            print(f"{d:8.3g} mm  {t:10.3e} krad")
            metrics[f"tid_{d:g}mm"] = {"value": t, "unit": "krad"}
    path = Path(os.environ["FACTORY_RUN_DIR"]) / "metrics.json"
    existing = json.loads(path.read_text()) if path.exists() else {}
    path.write_text(json.dumps({**existing, **metrics}, indent=2))


# --- any IRENE run -----------------------------------------------------------------------------


def run_command(ctx: RunContext) -> Command:
    """A hand-written CmdLineIrene input, for what ``environment`` does not cover (SPM plasma,
    CAMMICE, adiabatic coordinates, kernels). ``${FACTORY_OUTPUTS}`` and ``${STUDY_DIR}`` are
    replaced before the run; ``$IRENE_DATA`` is expanded by IRENE itself. The rendered input
    is kept with the outputs."""
    source = ctx.study.dir / ctx.params["input"]
    rendered = ctx.outputs_dir / source.name
    rendered.write_text(source.read_text()
                        .replace("${FACTORY_OUTPUTS}", str(ctx.outputs_dir))
                        .replace("${STUDY_DIR}", str(ctx.study.dir)))
    return Command([executable(ctx.toolchain_env), "-i", str(rendered)], cwd=ctx.outputs_dir)


# --- the module --------------------------------------------------------------------------------


def checks() -> list[Check]:
    prefix = TOOLCHAIN.prefix()
    if prefix is None:
        return [Check("irene", False, "not installed (tools/build-irene)")]
    need = ["AE9V15_runtime_tables.mat", "AP9V15_runtime_tables.mat", "igrfDB.h5",
            "radiationBeltDB.h5", "sd2DB.h5"]
    missing = [f for f in need if not (prefix / "modelData" / f).is_file()]
    return [Check("irene model data", not missing,
                  f"{prefix.name}" + (f", missing: {', '.join(missing)}" if missing else ""))]


module = Module(
    id="irene",
    title="IRENE (AE9/AP9)",
    description="Trapped radiation environment along an orbit: spectra, Geant4 source, dose.",
    tasks=[
        Task(
            id="environment",
            title="Orbit radiation environment (AE9/AP9, IRENE)",
            command=command,
            toolchain=TOOLCHAIN_NAME,
            outputs=("spectrum_e.csv", "spectrum_p.csv", "gps_e.mac", "gps_p.mac",
                     "dose_depth.csv"),
            params=(
                Param("models", list, ["AE9", "AP9"], help="AE9, AP9 (or legacy AE8, AP8)"),
                Param("statistic", str, "mean", help="mean, or a percentile such as p95"),
                Param("scenarios", int, 0,
                      help="Monte Carlo scenarios (with a percentile: confidence level)"),
                Param("perigee_km", float, None, "km", required=True),
                Param("apogee_km", float, None, "km", help="default: circular"),
                Param("inclination_deg", float, None, "deg", required=True),
                Param("raan_deg", float, 0.0, "deg"),
                Param("argp_deg", float, 0.0, "deg"),
                Param("propagator", str, "Kepler", help="Kepler (J2), SGP4 or SatEph"),
                Param("start", str, "2026-01-01T00:00:00", help="UTC, ISO 8601"),
                Param("days", float, 3.0, "d"),
                Param("step_s", float, 300.0, "s"),
                Param("electron_E_MeV", list, E_GRID, "MeV", help="0.04-10 MeV"),
                Param("proton_E_MeV", list, P_GRID, "MeV", help="0.1-2000 MeV"),
                Param("ae8_activity", str, "max", help="AE8 solar activity: min or max"),
                Param("ap8_activity", str, "min", help="AP8 solar activity: min or max"),
                Param("dose_depths_mm", list, [], "mm", help="Al depths (3 or more) for dose"),
                Param("dose_geometry", str, "spherical4pi",
                      help="spherical4pi, spherical2pi, finiteslab, semiinfiniteslab"),
                Param("dose_detector", str, "silicon"),
                Param("mission_years", float, 1.0, "yr", help="dose scaling"),
            ),
            version="1",
            description="Writes spectrum_[ep].csv, gps_[ep].mac and dose_depth.csv.",
        ),
        Task(
            id="run",
            title="IRENE command-line run (own input file)",
            command=run_command,
            toolchain=TOOLCHAIN_NAME,
            params=(Param("input", str, "irene_input.txt",
                          help="CmdLineIrene input, in the study folder"),),
        ),
    ],
    file_kinds=[FileKind("Geant4 GPS source", ("gps_*.mac",), "≈"),
                FileKind("IRENE input", ("irene_*.txt", "*.irene.txt"), "⊕")],
    toolchains=[TOOLCHAIN],
    checks=checks,
)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, FileNotFoundError) as e:
        raise SystemExit(f"irene: {e}") from None
