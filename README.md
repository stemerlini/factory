<p align="center"><img src="assets/banner.png" alt="Factory: describe, prove, build" width="100%"></p>

# Factory

A universal development workbench for Omarchy. A product is a tree of **elements**
(system → subsystems → components); every element goes through the same loop —
Describe · Research · Architect · Concept · Analyse · Lock · Design · Deliver — with each
gate's status computed from what exists. Requirements carry criteria on metrics; studies
run module tasks (Geant4, IRENE AE9/AP9, PIC, ngspice, KiCad, pytest, scripts…) and record runs whose
metrics pass or fail them; derived requirements roll up to their parents; any change marks
what depends on it stale. Notes and reports live in the Obsidian vault, papers in Citadel,
design data in the project's git repo.

See [PLAN.md](PLAN.md) for the vision, the model and the phases.

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/factory templates
.venv/bin/factory new ptel -t hardware   # or software, publication; under ~/Hardware
.venv/bin/factory status -C ~/Hardware/ptel
.venv/bin/factory run S001 --wait -C ~/Hardware/ptel
.venv/bin/factory arch -C ~/Hardware/ptel
.venv/bin/factory -C ~/Hardware/ptel               # TUI
.venv/bin/pytest
```

## Home and journal

Factory opens on **Home**, which answers "what now?" for the whole project:

- **Waiting on you:** changes to review, open decisions, failed or stale studies,
  and elements changed since their lock. Enter takes you there.
- **Next steps:** each element's current gate and what it needs. Enter opens that element.
- **Journey:** every element's eight gates as a row of cells, plus the project's progress.
- **Recently:** the latest entries in the **Journal**.

The Journal tab (`9`) is the project's timeline: approvals, locks, decisions, runs,
discarded changes and gates passed. A toast marks each gate as it passes.

```bash
factory home          # the same summary; --json for agents
factory next          # each element's next step
factory journal --since 7 --md > handover.md
```

## Guided gates

The **Gates** tab (`2`) shows the selected element's gates, and under each one not yet
done, a short checklist of what is left. Everything is computed from the files:
"Write its requirements", "Allocate every requirement to a block", "Run the studies and
keep them up to date", and so on. On an item:

- `space` or `enter` **does it yourself**: the editor on the right file (created from a
  stub if needed), a new requirement, the New Study dialog, the right tab, or lock.
- `a` **hands the gate to an agent**: the brief carries the gate's open items and its
  **playbook**, a short recipe for running that gate. Factory ships one per gate. Put
  your own in `playbooks/<gate>.md` in the project to change how your team works.

```bash
factory gates fee            # the gates with their checklists
factory agent fee -g analyse # an agent session on that gate (--print shows the brief)
```

## New projects

`Ctrl+O` → *New project from a template…* asks for a name, a template and a **location**:
type it, or press `Browse…` (`Ctrl+B`) for the desktop's folder chooser. The project gets
its own folder, named after it, inside that location; the dialog shows the full path
before you create it.

## Working with an agent

Press `a` in the TUI: start a Claude Code session on the selected element, go to the one
already running (the top bar shows `◆ agent working`), or continue the last one after a
usage limit resets. The agent never commits: its changes wait in the Review tab (`8`) with
`.factory/proposal.md`, which is also its handover to the next session.

```bash
factory agent fee -t "size the ADC"   # new session (-w: own window)
factory agent --continue              # resume the last session; --fork resumes a copy
factory agent --sessions              # saved and running sessions
```

## Radiation environment (IRENE)

`tools/build-irene` builds AFRL's IRENE (AE9/AP9/SPM) from its source distribution into
`~/.local/opt/irene/<version>` and adds an `irene` command. A study with
`task = "irene.environment"` takes orbit elements, models (AE9, AP9, AE8, AP8) and a statistic
(mean, a percentile, or a Monte Carlo confidence level) and writes orbit-average/peak spectra,
Geant4 GPS source macros and total dose behind aluminium (`tid_<d>mm` metrics).

## Writing a module

A module is a `factory.modules.api.Module` with tasks, tools, viewers, element kinds, file
kinds, toolchains and checks. Built-ins live in `src/factory/modules/`; external packages
register through the `factory.modules` entry-point group. A task builds a `Command` from a
`RunContext`; Factory runs it, logs it, and reads `metrics.json` plus the task's `collect`.
