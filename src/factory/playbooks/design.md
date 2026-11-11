# Design

Goal: the detailed design in `design/`, checked by studies.

1. Work from the locked baseline: requirements and architecture are inputs, not drafts.
2. Put design files in `design/` (KiCad, FreeCAD/build123d, code); keep generated files
   out of git unless they are deliverables.
3. Add a check study per gate `design` (ERC/DRC, FEA, tests) and make it pass.
4. A change that breaks the baseline is a proposal for the user, not a quiet edit.
