# Factory file formats

All paths are relative to an element's folder. The system element is the project root;
a subsystem `fee` lives in `elements/fee/`, its own children in `elements/fee/elements/`.

## element.md

```markdown
---
title: Front-end electronics
kind: electronics        # system | electronics | mechanical | software | generic (factory tasks)
prefix: EL               # requirement id prefix
references: [2004_StatisticalTheoryMHDTurbulence_Verma]   # Citadel citekeys
skip: [concept]          # gates this element does not need
---
What the element is and does, in a paragraph.
```

## req/<ID>.md

```markdown
---
id: EL-001
title: Conversion gain
parent: SYS-004          # the requirement this one is derived from (flow-down)
method: analysis         # analysis | test | inspection | demonstration
status: draft
allocated: [csa]         # blocks of THIS element's architecture
criterion: {metric: gain, op: ">=", value: 0.3, unit: mV/fC}   # ops: >= > <= < ==
---
The CSA and shaper shall convert at least 0.3 mV per fC of input charge.
```

The metric name in `criterion` must equal the name a verifying study reports; when both
give a unit, they must match exactly.

## architecture.yaml

```yaml
blocks:
  - {id: fee, title: Front-end electronics, kind: electronics, element: fee,
     description: "CSA + shaper + discriminators"}      # element: the subsystem folder name
interfaces:
  - {from: head.D1, to: fee.ch1, type: signal, label: charge pulse}   # block.port
allocate:                     # alternative to `allocated:` in the requirement
  SYS-004: [fee]
```

A requirement with derived requirements counts as allocated.

## studies/S00N-name/study.toml

```toml
title = "Sensor head mass budget"
task = "script"               # factory tasks lists all tasks and their params
gate = "analyse"              # analyse | design | deliver
verifies = ["ME-001"]
description = "..."

[params]                      # task params, plus any extras your script reads
file = "mass.py"
housing_wall_mm = 2.0

[inputs]                      # name = path or another study's output
stack = "/geometry/stack.yaml"        # leading / = project root; else this element's folder
spectra = "@S001:spectra.csv"         # output of S001 in this element
response = "@/S002:response.h5"       # output of the system's S002
gain = "@fee/S001:ngspice.log"        # output of another element's study
```

The task's module must be in `[modules] enabled` of `factory.toml`.

## decisions/D00N-slug.md

Created with `factory ask` (prefer it to writing the file). After the user decides:

```markdown
---
id: D001
title: Detector stack baseline
status: accepted          # proposed | accepted | rejected
raised_by: agent
date: 2026-10-18
options:
  - {id: A, title: Si only, summary: "2 × Si, simplest, protons 1–100 MeV"}
  - {id: B, title: Si + CsI, summary: "adds a calorimeter, reaches 300 MeV"}
recommended: A
chosen: A
decided: 2026-10-19
---
Context: why it matters, evidence, numbers, citekeys.

## Decision

Accepted option A: Si only.
```

## .factory/proposal.md

Not committed. Line one is the commit title the user approves with.

```markdown
Sensor-head mass budget and collimator trade

- Added ME-002 (collimator half-angle) derived from SYS-002.
- New study sensor-head/S002 sweeps collimator length; ran it: 20 mm passes both.
- Assumed Al 6061 housing, 2 mm walls.
- Check: the tungsten density in mass.py (19.3 g/cm³).
- Asked D002: tungsten vs brass collimator.

## Where I stopped

- Done: ME-002, study S002 and its run.
- Half-done: sensor-head/architecture.yaml has the collimator block but no interface yet.
- Next: add the collimator–housing interface, then rerun `factory check`.
```

## A script study

The script runs from the study folder with Factory's Python (PyYAML available):

```python
import json, os
from factory import metrics

params = json.loads(os.environ["FACTORY_PARAMS"])
inputs = json.loads(os.environ["FACTORY_INPUTS"])     # name -> absolute path
out = os.environ["FACTORY_OUTPUTS"]                   # write files here (plots, tables)
metrics.write("mass", 107.0, "g")                     # what criteria check
```

Print the working (inputs, intermediate values, result) so the run log explains the number.
