# GPU electron transport: adoption study (factory-6, 2026-11-19)

**Keep CPU MT as the reference; do not expose `backend = "gpu"`.** CUDA transport runs on
the laptop. That alone does not establish that it transports through the right solids,
implements the low-energy reference physics, or records the quantities used for selection.
This study changes neither `geant4.run` nor the project trace.

## What ran

Geant4 11.4.3, CUDA 13.4.92, RTX PRO 2000 Blackwell laptop (compute capability 12.0,
8151 MiB reported). Built [Celeritas v0.7.0](https://github.com/celeritas-project/celeritas/tree/v0.7.0)
in Release with double-precision transport, `CMAKE_CUDA_ARCHITECTURES=120`,
`CELERITAS_USE_CUDA=ON`, `CELERITAS_USE_Geant4=ON`; target `celer-g4`.
`factory-g4` used `FTFP_BERT_EMZ`; the GPU driver used `celer_ftfp_bert`, not EMZ.

| Si control: 4000 electrons, one primary/event | CPU MT mean D1 (keV) | GPU ORANGE mean D1 (keV) | CPU / GPU elapsed (s) |
|---|---:|---:|---:|
| 0.2 MeV, normal incidence | 183.19 | 183.91 | 0.92 / 15.92 |
| 2 MeV, normal incidence | 107.00 | 108.80 | 0.99 / 4.90 |

The control was a 300 µm thick, 5 mm radius Si disc in vacuum, source at z = −1 mm
and front face at zero. Times are single wall-clock observations including startup and
scoring, **not** predictions for 2–3 million events. CPU used four workers; GPU used one
host worker and 1024 slots. ORANGE also warned about simple safety for curved surfaces.
Similar mean deposits (+0.4%, +1.7%) do not validate spectra, thresholds or event classes.
Few secondaries and a flush at event boundaries leave this configuration little work per
launch. We did not isolate FP64 arithmetic throughput as the limiting factor.

## Geometry

| Archived CPU input | SHA-256 of geometry.gdml | Tessellated solids / facets | ORANGE replacements |
|---|---|---:|---:|
| S049 | `95267b0f774a0a785df8364c375e1d8f0eb0c31417d899618507fbba5b673fe7` | 1 / 4208 | 1 |
| S050 | `55c432806a13b2e125911b172762c1f5c791b0731a837994501d3d8a5b8f528b` | 14 / 5812 | 14 |

Both ORANGE smoke runs **exited successfully** after replacing these solids with
equivalent-volume spheres. That changes apertures and scattering paths. Discard these
runs; preserving volume is not preserving geometry. The [converter](https://github.com/celeritas-project/celeritas/blob/v0.7.0/src/orange/g4org/LogicalVolumeConverter.cc)
explains the log messages. The Geant4 navigator is not a GPU fallback in this release.

**VecGeom is a viable conversion path for a probe.** Built VecCore v0.8.2, VecGeom
v2.2.1 (CUDA, scalar backend, GDML, tuple navigation) and G4VG v1.0.8 in a private prefix.
Point `CMAKE_PREFIX_PATH` at that prefix and Geant4, and rebuild Celeritas with
`CELERITAS_USE_VecGeom=ON` and `CELERITAS_CORE_GEO=VecGeom`. G4VG v1.0.3 failed to
configure against this VecGeom version; v1.0.8 needed no local patches.
The [converter](https://github.com/celeritas-project/g4vg/blob/v1.0.8/src/g4vg_impl/SolidConverter.cc)
copies the facets; both actual heads ran without a reported solid substitution. That
is evidence for transport, not a precision validation of foil or aperture boundaries.

| Head / electron energy | Mean D1 keV, CPU / GPU | Mean D2 keV, CPU / GPU | Mean GAGG keV, CPU / GPU | CPU / GPU wall (s) |
|---|---:|---:|---:|---:|
| S049 / 0.2 MeV | 0.020 / 0.025 | 0.091 / 0.084 | 0.021 / 0.072 | 1.57 / 20.52 |
| S050 / 0.2 MeV | 0.030 / 0.022 | 0.085 / 0.038 | 0.036 / 0.110 | 2.30 / 24.66 |
| S049 / 2 MeV | 95.51 / 100.97 | 158.37 / 159.31 | 1066.30 / 1065.06 | 3.69 / 28.77 |
| S050 / 2 MeV | 103.41 / 106.16 | 180.66 / 178.33 | 1274.36 / 1291.20 | 2.51 / 30.47 |

Same counts and worker settings as the Si control; beam at x = y = 0, z = −30.06 mm
(S049) or −39.01 mm (S050), directed along +z. D1/D2 for S050 are aperture A; the probe
also scored B. At 2 MeV the D1 means differ by +5.7% and +2.7%; the low-energy samples
contain too little deposited energy to infer a relative bias from one batch. There is
neither an equivalence result nor a speed advantage here. These are end-to-end comparisons
of two different drivers and scoring paths, not isolated kernel benchmarks.

## Scoring and physics

Geant4 option4 uses Goudsmit–Saunderson electron multiple scattering at low energy
and Penelope electron ionisation below 0.1 MeV. The low end matters: the study sources
start at 0.04 MeV. Importing Geant4 tables or retaining its hadron list does not make
the GPU EM model option4. [AdePT v0.4.3](https://github.com/apt-sim/AdePT/tree/v0.4.3)
is an alternative VecGeom/G4HepEm integration, not independent evidence of agreement:
its [G4HepEm electron initialisation](https://github.com/mnovak42/g4hepem/blob/20261106/G4HepEm/G4HepEmInit/src/G4HepEmElectronInit.cc)
uses Urban scattering. AdePT was assessed from source, not benchmarked.

`simple_calo` verified a sensitive-volume deposit can return from GPU to host. It sums
over events; it is **not** Factory's `events.csv`. Factory also needs the *primary's*
last exit direction, reset on re-entry, including boundaries with no deposit. Celeritas'
[SD callback contract](https://github.com/celeritas-project/celeritas/blob/v0.7.0/src/accel/SetupOptions.hh)
skips zero-deposit steps by default and does not supply valid Geant4 track and parent IDs.
An adapter that just calls the existing stepping action could silently mislabel `back`
and `escape`. A future adapter needs event/primary identity and boundary scoring, and
must flush all offloaded secondaries before writing the event. Keep protons/hadrons
on Geant4 and route only e−, e+ and photons; avoid double counting their deposits.

## Reproduction and sanity checks

1. Source the Geant4 environment. Use the archived GDML, not a reconstruction; verify
   its hash above. Build `celer-g4` with `-DCELERITAS_CORE_GEO=ORANGE` for the negative
   control. Use separate build directories for different navigators.
2. With both executables on PATH, run e.g.
   `python tools/studies/gpu-em-probe.py /path/to/S049/geometry.gdml D1 D2 GAGG --z-mm=-30.06 --energy-mev=2`.
   The probe adds only SD metadata to a temporary copy. It rejects geometry substitution
   even on exit 0, checks the reported device, event counts, scoring volume labels and
   units, then converts summed MeV to keV per incident electron. Zero-deposit events stay
   in the denominator. It is not a production runner.
3. Before any adoption, repeat the **actual** GPS aperture and energy scan, matching cuts,
   materials, geometry and scoring. Compare per-energy D1 trigger fraction and edge,
   `back` and `escape` fractions, per-volume spectra and joint Si/GAGG selection, including
   the rare 1Si electron class. Include proton events with EM secondaries. Predeclare
   engineering equivalence tolerances with the project; use independent batches and
   confidence intervals narrow enough to resolve them. Failure to detect a difference
   is not equivalence.
4. Only after these pass, time repeated representative runs against the merged CPU MT
   implementation at the same event count and scoring settings. Include transfers,
   startup and output; record peak device memory and worker count. On a host without CUDA,
   a future opt-in should explicitly record the CPU MT fallback. Never silently substitute
   geometry or physics or label a CPU result as GPU.

**Assumptions:** the archived CPU geometry and EMZ response are the comparison baseline,
not experimental ground truth. Pencil beams are diagnostics, not substitutes for the
60° cosine-law aperture source. The CPU and GPU random histories are not paired.
