---
name: factory
description: Factory, the user's development workbench (git repo with factory.toml). Use when the user describes a product, system or subsystem to develop and wants it set up; when working inside a Factory project (requirements, architecture, studies, runs, trace, gates, decisions, review); or when adding a subsystem, requirement, study or analysis to one.
---

# Factory

A Factory project is a git repo holding one product as a tree of **elements** (system →
subsystems → components). Every element runs the same loop, its **gates**: Describe ·
Research · Architect · Concept · Analyse · Lock · Design · Deliver. Gate status is
computed from files and runs, never set by hand. Requirements carry criteria on
**metrics**; **studies** run module tasks whose runs report metrics; the **trace** says
pass / fail / stale / pending / open per requirement, and derived requirements roll up
to their parent.

The CLI is the interface (`factory <verb> -C <project> --json`). File formats are in
[FORMATS.md](FORMATS.md): read it before writing or editing any project file.

## Human in the loop

The user is the engineer of record; you work to their review. Every session:

1. **Orient.** `factory decisions --json` and `factory review --json` first. Accepted
   decisions are binding; rejected options stay closed; unreviewed changes are someone's
   work in flight, so build on them only when the user says so.
2. **Propose before you build.** Before work that touches requirements, the architecture
   or more than one element, state the plan in a few lines and wait for the go-ahead.
   Small, local work (one study, one fix) you may just do.
3. **Ask, don't assume, on real trade-offs.** A choice with consequences the user would
   want to weigh (detector material, orbit, a margin, a scope cut) becomes
   `factory ask "<question>" -o "A title: summary" -o "B title: summary" -r A -c "<context,
   numbers, citekeys>" -e <element>`. Carry on with work the answer does not block; stop
   and say so when it does.
4. **Leave a proposal.** Your changes stay uncommitted. Write `.factory/proposal.md`: line
   one is the commit title; then what changed and why, the assumptions you made, the
   numbers to check, and open questions. Done when the proposal accounts for every file
   `factory review` lists.
   It is also the handover: a session can stop at any moment (a usage or context limit),
   and the next one starts by reading it. So write it as soon as you change the first file,
   and after every step that matters update its `## Where I stopped` section: what is done,
   what is half-done (which file, what remains), and the next step. Never leave a result
   only in the chat.
5. **Hand over.** End with: what to review (Review tab, key `8`), decisions waiting, and
   what you would do next.

Committing, tagging, locking, approving and deciding belong to the user (the project's
`.claude/settings.json` blocks them); so do `status: approved` on requirements.

## Set up a project from a description

1. **Frame.** Restate the product in three lines: what it does, for whom, in what
   environment, and the few numbers that define success. Ask the user only for what you
   cannot sensibly assume (the mission, a hard limit); everything else becomes an
   assumption you list at the end. Done when you can name the system's top 3–8 needs.
2. **Create.** `factory new <name> -t hardware|software|publication`, which creates
   `~/Hardware/<name>`. Replace the template's placeholder content with this product's;
   delete placeholder studies that do not apply.
3. **Describe.** Write the system `element.md` and its requirements `req/SYS-*.md`.
   Give a requirement a `criterion` whenever its need has a number; each one is one
   verifiable statement. Done when every need from step 1 is a requirement.
4. **Research.** Search Citadel (the `citadel` skill) for the product's key physics or
   prior instruments and list the citekeys under `references:` in `element.md`. Done
   when the main design questions each have a reference or are listed as gaps.
5. **Architect.** Write `architecture.yaml`: blocks for the subsystems, interfaces
   between them, and every requirement allocated (by `allocated:` in the requirement or
   `allocate:` in the file). Done when `factory arch` shows no problems.
6. **Decompose.** `factory decompose <block>` for each block that is a real subsystem,
   then write its `element.md` and turn the stub requirements into real derived ones
   with numbers. Leaf components skip gates they do not need (`skip:` in element.md).
7. **Analyse.** For each requirement with a criterion, add a study that computes its
   metric: a `script` study for a first-order estimate now; the engine task
   (`factory tasks`) when the model needs it. Run them with `factory run <key> -w`.
   Done when every criterion has a verifying study and each runnable study has run.
8. **Check.** `factory check` until it prints no problems, then `factory status` and
   `factory req`. Done when check is clean.
9. **Hand over** per *Human in the loop*: the proposal names the tree, the trace counts,
   which requirements fail or are pending and why, every assumption, and the next gate per
   element; trade-offs you met are `factory ask` decisions.

Everything you create is a **draft** (`status: draft`).

## Work in an existing project

- **Orient** with `factory home --json` (what waits on the user, each element's next
  step, recent events) and `factory gates <element>`; the first gate that is not done is
  the work in front of you; its checklist says what is left. `factory journal --since 7`
  shows what happened lately.
- **In Factory's agent pane** Factory answers your permission requests: reads and edits
  inside the project pass, commands are asked, and committing, locking and deciding are
  refused. Don't retry a refused command another way; say what the user should do.
- **Kickoff.** A brief that starts "We are starting a new Factory project" carries the
  kickoff playbook: interview first, write nothing until the user has answered.
- **Playbooks.** When your brief names a gate, follow its playbook. A project can
  override one in `playbooks/<gate>.md`.
- **Change** files per FORMATS.md, then `factory check`. A changed input makes its
  studies and their requirements stale: rerun with `factory run <key> -w`.
- **Long runs** (Geant4, PIC) start in the background with `factory run <key>`; follow
  them with `factory runs <key>` and `factory log <run-id>`.

## Where knowledge lives

- Papers and books: Citadel, cited by citekey.
- Notes and reports: the user's Obsidian vault (`factory status` shows whether it is configured).
- Design data — requirements, architecture, studies, CAD, KiCad, code: the project repo.
- Run outputs: the run store (`~/Data/factory`), referenced by run records in git.

## CLI

```sh
factory templates | tasks | modules             # what exists: templates, tasks+params, kinds, health
factory new NAME -t TEMPLATE
factory home | next [-e EL] | journal [--since DAYS] [-e EL] [--md]
factory status | check | req [-e EL] | studies [-e EL] | gates [EL] | arch [EL]
factory show KEY ; factory run KEY [-w] ; factory runs [KEY] ; factory log RUN ; factory cancel RUN
factory decompose BLOCK [-e PARENT]
factory review ; factory decisions [--open] ; factory ask TITLE -o A -o B [-r A] [-c TEXT] [-e EL]
```

Element paths are `""` (the system), `fee`, `fee/adc`; study keys are `S001`, `fee/S001`.
