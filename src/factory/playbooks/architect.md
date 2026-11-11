# Architect

Goal: a block diagram that covers every requirement.

1. Write `architecture.yaml` (FORMATS.md): blocks with a kind and a one-line description,
   interfaces between ports with a type and label.
2. Allocate every requirement to the blocks that satisfy it (`allocated:` in the
   requirement or `allocate:` in the architecture).
3. `factory arch <element>` to see it, `factory check` until clean.
4. A block that needs its own design becomes a subsystem: propose `factory decompose`;
   the user runs it.
5. Real alternatives (topologies, partitioning) are decisions: `factory ask` with options
   and your recommendation.
