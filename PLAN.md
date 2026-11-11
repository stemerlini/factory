# Factory — plan (working name)

> A universal workbench for developing anything — a particle telescope, a plasma
> thruster, a circuit board, a piece of software. One loop, applied at every level:
> describe, research, architect, conceive, analyse until proven, lock, then break the
> product into subsystems and run the same loop on each, down to a design, a report and a BOM.

Status: Phase 0b done (2026-10-12) plus the human-in-the-loop agent workflow (2026-10-18),
integrated agent sessions (2026-10-19) and the IRENE environment module (2026-10-28, built
from AFRL source, verified against AFRL's expected output); 56 tests; on GitHub. Geant4
not built yet (`xerces-c` is now installed).

## Vision

Developing a product is the same process whatever the product is:

1. **Describe** the system and write its **requirements**.
2. **Research**: literature review (Citadel), notes and analysis (Obsidian).
3. **Architect**: a **block diagram** — blocks, interfaces, which block meets which requirement.
4. **Conceive**: a concept design (anatomy figures, sketches, first numbers in Pascaline).
5. **Analyse and iterate**: simulations and calculations, compared against the requirements,
   with plots and 3D views of the results, until the architecture is **proven**.
6. **Lock**: an Obsidian report of the evidence, and a baseline that freezes the architecture.
7. **Decompose**: each block becomes a **subsystem** (electronics, mechanics, software…) with
   requirements allocated from the parent. **Each subsystem runs the same loop** with its own
   tools (SPICE for circuits, FEA for structures, unit tests for software).
8. **Design and deliver**: KiCad schematic and layout, 3D CAD, code — ending in a report,
   a BOM and manufacturing outputs.

Factory is the one interface over that loop. It does not replace the specialist tools; it
launches them, versions what they produce, runs the analyses, traces every result to a
requirement and writes the reports.

```
                 ┌──────────── System (particle telescope) ─────────────┐
                 │ describe → research → architect → concept → analyse ⟲ │
                 │                     → lock (report + baseline)         │
                 └───────────┬────────────────┬───────────────┬──────────┘
                    allocate │ requirements   │               │
             ┌───────────────▼───┐ ┌──────────▼────────┐ ┌────▼──────────────┐
             │ Front-end elec.   │ │ Sensor head (mech)│ │ Flight software   │
             │ same loop:        │ │ same loop:        │ │ same loop:        │
             │ SPICE ⟲ → KiCad   │ │ FEA/thermal ⟲ →   │ │ tests ⟲ → code    │
             │ → report + BOM    │ │ CAD → drawings,BOM│ │ → release         │
             └───────────────────┘ └───────────────────┘ └───────────────────┘
                                 roll-up: system BOM, verification matrix, reports
```

## Core model

The scaffold had *project → stages → studies*. Vision v2 makes it recursive:

| Noun | What it is | Lives in |
|---|---|---|
| **Project** | A git repo; settings, modules, toolchains, Obsidian link | `factory.toml` |
| **Element** | A node of the product breakdown: system, subsystem, component. Has a **kind** (system, electronics, mechanical, software, generic) that decides its default tools | `element.md` in its folder; children under `elements/` |
| **Gate** | The fixed loop every element goes through: Describe · Research · Architect · Concept · Analyse · Lock · Design · Deliver. Status comes from content, not ticking boxes; an element may **skip** gates (`skip: [research]`) | computed |
| **Requirement** | Text + verification method + optional metric criterion; `parent` links flow down from the parent element; `allocated` names the block that must meet it | `req/*.md` per element |
| **Architecture** | Blocks, ports, interfaces, requirement allocation. A block can become a child element | `architecture.yaml` per element |
| **Study** | An analysis or simulation: a module task + params + inputs; sweeps for trade studies | `studies/S001-*/study.toml` |
| **Run** | One execution with provenance and metrics | record in git, data in the run store |
| **Decision** | A recorded choice (trade-off outcome, architecture option chosen) with the evidence linked | `decisions/D001-*.md` |
| **Report** | Markdown generated from live data (description, diagram, trace table, plots, citations) + your text | Obsidian vault |
| **Note** | Anything you write while working: research, analysis, meetings | Obsidian vault |
| **Baseline** | An element locked at a gate: git tag `<element>/<gate>-vN` + the frozen report | git tags |
| **Deliverable** | What design produces: BOM, Gerbers, STEP, drawings, PDF, release | `out/` (generated, reproducible) |
| **Module** | Plugin: tasks, tools, viewers, file kinds, toolchains, element kinds, report blocks, checks | Python package |

### Gate status is computed, not declared

| Gate | Done when |
|---|---|
| Describe | `element.md` has a description and at least one requirement |
| Research | Citadel references listed in `element.md`, or notes in the element's vault folder |
| Architect | `architecture.yaml` exists; every requirement is allocated to a block (or to the element itself) |
| Concept | a concept note or figure exists |
| Analyse | every requirement with method *analysis* passes on up-to-date runs |
| Lock | a baseline tag exists and nothing it covers changed since (else: **unlocked by change**) |
| Design | design files exist and their checks pass (ERC/DRC, CAD checks, tests) |
| Deliver | deliverables are generated from the current design (BOM, outputs, final report) |

So "the architecture is proven against the requirements" is a visible state, not an opinion.
Changing a locked element's inputs shows it as unlocked; children whose allocated
requirements changed show as affected.

### Element kinds = module bundles (universal, not hardware-only)

| Kind | Analyse with | Design with | Deliver |
|---|---|---|---|
| system | geant4, pic/mhd, omere, script, pascaline | — (architecture) | system report, verification matrix, BOM roll-up |
| electronics | ngspice, script | KiCad (schematic, layout, ERC/DRC) | BOM, Gerbers, schematic PDF, report |
| mechanical | FEA (CalculiX/Elmer), thermal, script | FreeCAD / build123d | STEP, drawings, mechanical BOM |
| software | tests, benchmarks (pytest metrics) | code repo | release, test report |
| generic | script, pascaline | files | report |

New kinds come from modules (e.g. *optics*, *thruster*), not from the core.

## Architecture: block diagram as data

```yaml
# architecture.yaml (system)
blocks:
  - {id: head, title: Sensor head, kind: mechanical, element: sensor-head}
  - {id: fee,  title: Front-end electronics, kind: electronics, element: fee}
  - {id: fsw,  title: Flight software, kind: software}
interfaces:
  - {from: head.D1, to: fee.ch1, type: signal, label: charge pulse}
  - {from: fee.adc, to: fsw.spi, type: data, label: SPI 10 MHz}
allocate:
  SYS-004: [fee]        # energy threshold → front-end
  SYS-007: [head]       # mass
```

- Text in git is the source of truth (diffable, agent-editable).
- **TUI view**: rendered with box drawing (layout from Graphviz `dot -Tplain`); select a block
  to see its requirements, interfaces and child element.
- **Exports**: SVG (D2 or Graphviz) for reports; an **Obsidian Canvas** (`.canvas`) so the
  diagram is browsable next to the notes. Editing back from Canvas is a later option.
- `factory decompose fee` creates the child element with its allocated requirements stubbed
  as children (`EL-001 parent: SYS-004`).

## Seeing results: viewers

A **viewer** is a module capability, like a tool: file kinds → how to show them.

| Content | Viewer | How it feels in Omarchy |
|---|---|---|
| Plots (PNG/SVG from runs) | inline in the TUI (textual-image), like Citadel previews | in place |
| Interactive plots | matplotlib/plotly window | tiled window next to the TUI |
| 3D fields, meshes, ntuples → VTK (`.vtu`, `.vtk`, `.pvd`, HDF5) | **ParaView** standalone, opened by Factory with a prepared state (`pvpython` script per study) | its own window, Hyprland rule, theme colours |
| Quick 3D look: STEP, STL, GLTF, VTK | **f3d** (fast, minimal) | floating window, instant |
| Geometry for Geant4 (GDML) | Geant4 Qt viewer | window |
| Schematic / PCB | KiCad, or SVG export inline | window / in place |
| Block diagram | TUI render, Obsidian Canvas | in place / Obsidian |
| Reports | Obsidian, PDF via pandoc | Obsidian |

All available from the Arch repos: paraview 6.1, f3d 3.5, kicad 10, ngspice 47, freecad 1.1,
d2, graphviz; Obsidian is installed.

## Where things live (decided 2026-10-12)

| What | Where | Why |
|---|---|---|
| Sourced knowledge: papers, books, standards | **Citadel** | one library, cited by key |
| Produced knowledge: notes, analyses, reports | **Obsidian vault** (`<vault>/Projects/<project>/<element>/`) | where you read and write |
| Design data: requirements, architecture, studies, CAD, KiCad, code | **project git repo** | versioned, reproducible, diffable |
| Bulky run outputs | **run store** (`~/Data/factory`) | too big for git |

- The vault is not set up yet; its path is a Factory setting (`vault` in
  `~/.config/factory/config.toml`). Without it, note and report features say so and wait.
- **Reports** are generated into the vault from a template per gate, with data blocks filled
  from the repo (description, block diagram SVG, trace table, plots, decisions, BOM) around
  sections you write; regenerating refreshes only the marked data blocks.
- **Locking** an element tags the repo and records the hash of each report and note it
  relies on, so a baseline still proves which evidence it was locked with even though the
  vault is outside git.
- Citations use Citadel keys; report generation resolves them through Citadel's JSON CLI.
- Requirements stay in the repo (design data, and the future requirements app edits them).
  An Obsidian Base can still show them if the vault links to the repo's `req/` folders.

## Iteration and trade studies

- A study can **sweep** parameters (`[sweep] gap_mm = [10, 20, 50]`); the run produces a
  table of metrics per point and a plot. The TUI shows which points pass the requirements.
- Choosing an option writes a **decision** (`D003: gap 20 mm — passes SYS-002 with margin
  and keeps mass under SYS-007`) that links the runs as evidence.
- An element can hold **architecture variants** (`architecture.yaml`, `architecture.B.yaml`)
  analysed side by side until one is locked.

## Project layout

```
ptel/                                   ← git repo
├── factory.toml                        ← project settings, modules, toolchains
├── element.md                          ← root element: system description (kind: system)
├── req/SYS-001.md …
├── architecture.yaml
├── concept/  decisions/                 ← notes & reports go to the vault, not here
├── studies/S001-geometric-factor/
├── elements/
│   ├── fee/                            ← kind: electronics
│   │   ├── element.md  req/EL-001.md …  architecture.yaml (optional)
│   │   ├── studies/S001-csa-noise/     ← ngspice
│   │   ├── design/fee.kicad_pro …      ← KiCad
│   │   ├── out/bom.csv  out/gerbers/
│   └── sensor-head/                    ← kind: mechanical
│       ├── design/head.FCStd  studies/S001-modal/  out/head.step
└── .factory/                           ← cache (not committed)
```

Requirement ids carry the element prefix (`SYS-`, `EL-`, `ME-`, `SW-`), set in `element.md`.
Study ids are local to their element; across elements they are written `fee/S001`.

## TUI

```
┌ Factory · Particle telescope · main @ 3f2a… ─────────────── ⟳ fee/S002 62% ┐
│ Elements          │ System — gates                                         │
│ ▾ ◆ System    ●   │ Describe ✓  Research ✓  Architect ✓  Concept ✓          │
│   ├ ⏚ FEE     ○   │ Analyse ● 2 stale  Lock ○  Design —  Deliver —          │
│   ├ ▦ Head    ○   ├────────────────────────────────────────────────────────┤
│   └ λ FSW     ○   │ Overview Requirements Architecture Studies Results     │
│                   │ Design Reports                                          │
│ Trace (system)    │  ┌─────────┐ charge  ┌─────────┐ SPI  ┌─────────┐       │
│ ✓ 9 ✗ 1 ● 2 ○ 3   │  │  Head   │────────▶│   FEE   │─────▶│   FSW   │       │
│                   │  └─────────┘ pulse   └─────────┘      └─────────┘       │
└ r run · v view · o open · g report · L lock · d decompose · ? help ───────┘
```

- **Left**: the element tree with each element's gate progress, and the trace summary.
- **Right**: the gate bar for the selected element, then tabs: Overview · Requirements
  (with parent/children flow-down) · Architecture (block diagram) · Studies · Results
  (runs, metrics, plots inline, `v` opens ParaView/f3d) · Design (files, tools, checks) ·
  Reports (generate, open in Obsidian).
- Everything stays a CLI verb with `--json` for agents.

## Agents and the human in the loop (decided 2026-10-18)

Factory is agent-first, and the human stays the engineer of record.

- **Start an agent** with `a` in the TUI (or `factory agent [element] [-t task] [-w]`):
  Claude Code opens in its own Omarchy window in the project folder, briefed with the
  element, its next gate, open decisions and unreviewed work, and asked to summarise and
  propose before it builds.
- **The `factory` skill** carries the recipe and the protocol: orient on decisions and
  pending review; propose before cross-cutting work; `factory ask` on real trade-offs;
  leave changes uncommitted with a **proposal** (`.factory/proposal.md`, line one = commit
  title); hand over.
- **Guardrails, enforced:** every project has `.claude/settings.json` denying `git commit`,
  `push`, `reset`, `checkout`, `restore`, `tag`, `rebase`, `factory lock`, `decide`,
  `approve`, `reject` (verified: a live session's commit was blocked). Prefix rules can be
  sidestepped (`git -C . commit`), so the real gate is review: nothing reaches history
  unless the human approves it.
- **Review tab** (`8`, label shows the count): the proposal, pending changes per element
  with diffs, and decisions. `y` approves (commit; all, or the selected element's files),
  `x` discards a file, `enter` answers a decision. CLI: `factory review | approve | reject`.
- **Decisions** (`decisions/D001-*.md` per element): proposed with options and a
  recommendation (`factory ask`); accepted or rejected only by the human (`factory decide`
  or the Review tab), with a note kept in the record. Accepted decisions bind the agent.
- The TUI refreshes by itself when files change, so it follows the agent live.
- **The agent belongs to the project** (2026-10-19). The top bar shows `◆ agent working`
  while a Claude Code session runs in the project folder. `a` opens one dialog: go to the
  running session (focuses its window), continue the last saved session (after a usage
  limit resets), continue a copy, or start a new one on the selected element with a task.
  CLI: `factory agent --sessions | --continue | --fork`. Sessions are Claude Code's own
  transcripts (`~/.claude/projects/<folder>`); Factory only finds them.
- **Handover:** a session can stop at any moment, so the proposal doubles as the handover:
  written at the first change, with a `## Where I stopped` section kept current. A new
  session's brief tells it to read the proposal first.

## What changes in the scaffold (Phase 0b)

Kept as is: module API, registry, runs, run store, provenance, fingerprints, staleness,
trace logic, CLI pattern, TUI shell and theme.

Changes:
1. `Element` (folder with `element.md`) replaces the flat project; `Project` becomes the
   root element + settings. Requirements, studies, decisions, reports are per element.
2. Fixed **gates** with computed status replace configurable stages.
3. `architecture.yaml` model + validation + allocation; TUI render; Canvas/SVG export.
4. Module API gains **viewers**, **element kinds**, **report blocks**.
5. Requirement flow-down (`parent`, `allocated`) in the trace.
6. Templates become element trees (telescope: system + fee + sensor-head + fsw; thruster:
   system + PPU + thruster head + feed system; software: system + services).

## Templates (decided 2026-10-18)

Three, no more for now: **hardware** (system budgets, electronics, mechanics, firmware),
**software** (components, code verified by tests) and **publication** (claims, outline as
the architecture, analyses behind every number, PDF build with page and word metrics).
The particle-telescope and plasma-thruster trees live on as test fixtures.

## Phases

0. ✓ Scaffold (flat). ✓ **0b** — elements, gates, architecture model, viewers API, flow-down.
1. **Architecture view** — TUI block diagram, SVG/Canvas export, `decompose`.
2. **Reports & Obsidian** — report templates, data blocks, Citadel citations, vault link, Bases.
3. **Viewers** — inline plots, f3d, ParaView launcher with per-study state, Hyprland rules.
3b. ✓ **Environment** — IRENE (AE9/AP9) `irene.environment`: orbit → spectra (CSV), Geant4
   GPS sources, TID vs Al depth; `irene.run` for any hand-written input; `tools/build-irene`.
4. **Geant4** — build toolchain; `factory-g4` generic app; stack → GDML; response; bowtie.
5. **Baselines** — lock/unlock, tags, frozen reports, change impact on children.
6. **Electronics** — ngspice studies (metrics from `.meas`), KiCad ERC/DRC, BOM + Gerbers.
7. **Mechanics** — FreeCAD/build123d, STEP export, FEA (CalculiX/Elmer), drawings.
8. **Software kind** — test/benchmark metrics; release deliverable.
9. **Environment** — OMERE (Wine) / AE9AP9; trade-study sweeps; install into Omarchy; MCP.

## Open questions

- Vault location (not set up yet).
- Name (working name *Factory*); projects root (default `~/Hardware`); run store
  (default `~/Data/factory`, off OneDrive).
- Element ids/prefixes: free per project, or a fixed scheme (SYS / EL / ME / SW)?
