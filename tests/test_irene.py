"""IRENE environment task: input generation, output parsing, spectra, GPS source and dose.

A stand-in CmdLineIrene writes files in IRENE's output format, so the chain runs without the
AFRL build.
"""

import csv
import json
import sys

import pytest

from factory import runs
from factory.modules import irene, registry
from factory.project import write_study

FAKE = '''#!{python}
"""Stand-in for CmdLineIrene: flux falls as 1/E, integral as 1/E, dose falls with depth."""
import sys
from pathlib import Path

lines = Path(sys.argv[2]).read_text().splitlines()
args = dict(line.split(":", 1) for line in lines if ":" in line)
args = {{k.strip(): v.strip() for k, v in args.items()}}
prefix = args["OutFile"].removesuffix(".txt")
energies = [float(e) for e in args["Energies"].split(",")]
flux_out = args["FluxOut"].split(",")
scale = 2.0 if args["FluxType"] == "Integral" else 1.0
if flux_out[0] == "mean" or args["ModelType"] in ("AE8", "AP8"):
    tags = ["_mean_{{}}"]
elif flux_out[0] == "percentile":
    tags = ["_pctile_{{}}_" + flux_out[1]]
else:
    tags = ["_mc_{{}}_conf_level_" + args["Aggregate"].split(",")[1]]
rows = [(55197.0 + i / 288, 1.5, 0.0, 0.5 * (i % 3)) for i in range(4)]
for tag in tags:
    with open(prefix + tag.format("flux") + ".txt", "w") as f:
        f.write("# Irene Model output file\\n# datetime(mjd),posx(Re),posy(Re),posz(Re),flux\\n")
        for t, x, y, z in rows:
            vals = [scale * 1e4 * (1 + z) / e for e in energies]
            f.write(",".join(f"{{v:.6e}}" for v in (t, x, y, z, *vals)) + "\\n")
    if "DoseDepths" in args:
        depths = [float(d) for d in args["DoseDepths"].split(",")]
        with open(prefix + tag.format("doseaccum") + ".txt", "w") as f:
            f.write("# datetime(mjd),posx(Re),posy(Re),posz(Re),dose(rads)\\n")
            for k, (t, x, y, z) in enumerate(rows):
                vals = [(k + 1) * 100.0 / d for d in depths]
                f.write(",".join(f"{{v:.6e}}" for v in (t, x, y, z, *vals)) + "\\n")
'''


@pytest.fixture
def toolchain(tmp_path, monkeypatch):
    prefix = tmp_path / "opt/irene/1.58.001"
    (prefix / "bin").mkdir(parents=True)
    (prefix / "modelData").mkdir()
    exe = prefix / "bin/CmdLineIrene"
    exe.write_text(FAKE.format(python=sys.executable))
    exe.chmod(0o755)
    (prefix / "irene.sh").write_text(
        f'export IRENE_HOME="{prefix}"\nexport IRENE_DATA="{prefix}/modelData"\n')
    monkeypatch.setattr(irene.TOOLCHAIN, "root", tmp_path / "opt")
    return prefix


def environment_study(p, **params):
    base = {"perigee_km": 8000, "inclination_deg": 55, "days": 1,
            "electron_E_MeV": [0.5, 1.0, 2.0], "proton_E_MeV": [10.0, 50.0]}
    write_study(p.root / "studies/S010-env", {"task": "irene.environment", "gate": "analyse",
                                              "params": {**base, **params}})
    return p.study("S010")


def test_input_file_from_params():
    _, task = registry.find_task("irene.environment")
    params = task.resolve_params({"perigee_km": 8000, "apogee_km": 20000,
                                  "inclination_deg": 55, "statistic": "p95", "scenarios": 40,
                                  "dose_depths_mm": [1, 2, 4], "start": "2026-03-01T06:00:00"})
    text = irene.input_text("AP9", "1PtDiff", irene.Path("/o/AP9_diff"), params, "/data")
    assert "ModelType: AP9" in text and "ModelDBDir: /data" in text
    assert "FluxOut: montecarlo,1-40" in text and "Aggregate: percent,95" in text
    assert "OrbStart: 2026, 60, 21600" in text and "OrbEnd: 2026, 63, 21600" in text
    assert "OrbAltPerigee: 8000" in text and "OrbAltApogee: 20000" in text
    assert "DoseDepths: 1, 2, 4" in text and "Energies: 0.1, 0.25" in text
    with pytest.raises(ValueError, match="only the mean"):
        irene.input_text("AE8", "1PtDiff", irene.Path("/o/x"), params, "/data")
    with pytest.raises(ValueError, match="statistic"):
        irene.statistic({"statistic": "worst", "scenarios": 0})


def test_environment_run_writes_spectra_source_and_dose(make, toolchain):
    p = make()
    study = environment_study(p, dose_depths_mm=[1.0, 2.0, 4.0], mission_years=2)
    run = runs.execute(p, study)
    assert run.status == "done", (runs.run_dir(p, run) / "log.txt").read_text()
    out = runs.run_dir(p, run) / "outputs"
    rows = list(csv.DictReader((out / "spectrum_e.csv").open()))
    assert [float(r["E_MeV"]) for r in rows] == [0.5, 1.0, 2.0]
    # integral = 2e4 (1 + z) / E with z = 0, 0.5, 1, 0 → mean factor 1.375, peak 2
    assert float(rows[0]["int_avg_omni_cm2s"]) == pytest.approx(4e4 * 1.375)
    assert float(rows[0]["int_peak_omni_cm2s"]) == pytest.approx(8e4)
    assert {"E_MeV", "int_avg_dir_cm2ssr", "int_peak_omni_cm2s"} <= set(rows[0])
    gps = (out / "gps_p.mac").read_text()
    assert "/gps/particle proton" in gps and "/gps/hist/point 10 " in gps
    # dose: last cumulative row is 400/d rad per model over 1 day; two models; 2 years
    dose = list(csv.DictReader((out / "dose_depth.csv").open()))
    expected = 2 * 400.0 * 365.25 * 2 / 1000
    assert float(dose[0]["total_krad"]) == pytest.approx(expected)
    assert run.metrics["tid_1mm"].value == pytest.approx(expected)
    assert run.metrics["electron_flux_avg_above_0.5MeV"].value == pytest.approx(5.5e4)
    assert (out / "irene/AE9_diff_input.txt").is_file()


def test_percentile_statistic_reads_the_percentile_file(make, toolchain):
    p = make()
    run = runs.execute(p, environment_study(p, statistic="p95", models=["AP9"]))
    assert run.status == "done"
    assert (runs.run_dir(p, run) / "outputs/spectrum_p.csv").is_file()


def test_missing_toolchain_fails_clearly(make, tmp_path, monkeypatch):
    p = make()
    monkeypatch.setattr(irene.TOOLCHAIN, "root", tmp_path / "nothing")
    run = runs.execute(p, environment_study(p))
    assert run.status == "failed" and "irene is not installed" in (run.error or "")


def test_raw_run_renders_placeholders(make, toolchain):
    p = make()
    folder = p.root / "studies/S011-raw"
    write_study(folder, {"task": "irene.run"})
    (folder / "irene_input.txt").write_text(
        "ModelType: AP9\nOutFile: ${FACTORY_OUTPUTS}/ap9.txt\nFluxType: 1PtDiff\n"
        "Energies: 10\nFluxOut: mean\n")
    run = runs.execute(p, p.study("S011"))
    out = runs.run_dir(p, run) / "outputs"
    assert run.status == "done"
    assert f"OutFile: {out}/ap9.txt" in (out / "irene_input.txt").read_text()
    assert (out / "ap9_mean_flux.txt").is_file()
    assert json.loads(json.dumps(run.to_dict()))["status"] == "done"
