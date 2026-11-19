"""Side evaluation only: compare mean deposits for a normal-incidence pencil beam.

Run in the configured Geant4/Celeritas environment. This is not a Factory backend.
"""

import argparse
import csv
import json
import math
import os
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("geometry", type=Path)
    parser.add_argument("volumes", nargs="+", help="exact GDML logical-volume names")
    parser.add_argument("--z-mm", type=float, required=True)
    parser.add_argument("--energy-mev", type=float, required=True)
    parser.add_argument("--events", type=int, default=4000)
    parser.add_argument("--cpu-workers", type=int, default=4)
    parser.add_argument("--celer-g4", default="celer-g4")
    args = parser.parse_args()
    if min(args.events, args.cpu_workers, args.energy_mev) <= 0:
        parser.error("event count, worker count and energy must be positive")

    tree = ET.parse(args.geometry)
    names = {node.get("name"): node for node in tree.findall("structure/volume")}
    if missing := set(args.volumes) - names.keys():
        parser.error(f"unknown scoring volumes: {sorted(missing)}")
    for name in args.volumes:
        ET.SubElement(names[name], "auxiliary", auxtype="SensDet", auxvalue=name)

    with tempfile.TemporaryDirectory() as temp:
        work = Path(temp)
        tree.write(work / "probe.gdml", encoding="utf-8", xml_declaration=True)
        (work / "gpu.json").write_text(json.dumps({
            "geometry_file": "probe.gdml", "output_file": "gpu.out.json",
            "physics_list": "celer_ftfp_bert", "sd_type": "simple_calo",
            "num_track_slots": 1024, "initializer_capacity": 8192, "secondary_stack_factor": 2,
            "primary_options": {"seed": 12345, "pdg": 11, "num_events": args.events,
                                "primaries_per_event": 1, "energy": args.energy_mev,
                                "position": [0, 0, args.z_mm / 10], "direction": [0, 0, 1]},
        }))
        (work / "cpu.mac").write_text(
            f"/gps/particle e-\n/gps/pos/centre 0 0 {args.z_mm} mm\n/gps/direction 0 0 1\n"
            f"/gps/energy {args.energy_mev} MeV\n/run/beamOn {args.events}\n")
        for key in ("CELER_DISABLE", "CELER_DISABLE_DEVICE", "CELER_KILL_OFFLOAD"):
            if os.environ.get(key):
                parser.error(f"unset {key} for GPU evaluation")
        start = time.monotonic()
        proc = subprocess.run([args.celer_g4, "gpu.json"], cwd=work, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              env={**os.environ, "G4FORCENUMBEROFTHREADS": "1",
                                   "OMP_NUM_THREADS": "1"})
        gpu_seconds = time.monotonic() - start
        if proc.returncode or any(s in proc.stdout.lower() for s in (
                "replaced invalid solid", "replaced unknown solid", "conversion may have failed",
                "failed to convert", "equivalent-volume sphere",
                "killing track", "tracks lost", "error:")):
            raise SystemExit(f"Rejected GPU transport:\n{proc.stdout[-16000:]}")
        data = json.loads((work / "gpu.out.json").read_text())
        device = data["system"]["device"]["name"]
        if not device or sum(map(len, data["result"]["time"]["events"])) != args.events:
            raise ValueError("missing device or incomplete GPU event count")
        gpu = {}
        for name in args.volumes:
            score = data["result"][name]
            if score["volume_labels"] != [name] or score["_units"]["energy_deposition"] != "MeV":
                raise ValueError(f"unexpected GPU scoring metadata for {name}")
            gpu[name] = score["energy_deposition"][0] * 1000 / args.events
        start = time.monotonic()
        subprocess.run(["factory-g4", "--gdml", "probe.gdml", "--physics", "FTFP_BERT_EMZ",
                        "--sensitive", ",".join(args.volumes), "-t", str(args.cpu_workers),
                        "cpu.mac"], cwd=work, env={**os.environ, "FACTORY_OUTPUTS": temp,
                        "G4FORCENUMBEROFTHREADS": str(args.cpu_workers)}, check=True,
                       stdout=subprocess.DEVNULL)
        cpu_seconds = time.monotonic() - start
        cpu = dict.fromkeys(args.volumes, 0.)
        count = 0
        with (work / "events.csv").open() as stream:
            for row in csv.DictReader(stream):
                count += 1
                if row["species"] != "e-" or float(row["E_MeV"]) != args.energy_mev:
                    raise ValueError("CPU source mismatch")
                for name in cpu:
                    cpu[name] += float(row[name + "_keV"]) / args.events
        if count != args.events:
            raise ValueError("incomplete CPU event count")
        for means in (gpu, cpu):
            if not all(math.isfinite(v) and v >= 0 for v in means.values()):
                raise ValueError("invalid deposit")
            if sum(means.values()) > args.energy_mev * 1000 * 1.00001:
                raise ValueError("scored more than incident electron energy")
        print(json.dumps({"device": device, "events": count, "cpu_mean_keV": cpu,
                          "gpu_mean_keV": gpu, "cpu_wall_s": cpu_seconds,
                          "gpu_wall_s": gpu_seconds}, indent=2))


if __name__ == "__main__":
    main()
