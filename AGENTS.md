# Maintaining Factory

Factory is maintained by its owner from Omarchy machines, each with its own agents. GitHub
(`stemerlini/factory`) is where they meet.

- [README.md](README.md): what Factory does, for the user.
- [PLAN.md](PLAN.md): vision, model, decisions taken (dated) and phases. Read the
  *Status* line and the decisions before changing behaviour.

## Setting up a machine

```sh
gh repo clone stemerlini/factory ~/Projects/factory && cd ~/Projects/factory
tools/install-omarchy                  # venv, ~/.local/bin/factory, launcher, skill
.venv/bin/pip install -q -e '.[dev]'   # pytest, pytest-asyncio, ruff
```

The install is editable: `~/.local/bin/factory` runs this checkout, so what is on the
checked-out branch is what the user runs.

## The change loop

1. **Sync.** `git switch main && git pull --ff-only`, then a branch `feat/<topic>` or
   `fix/<topic>`.
2. **Red.** A test in `tests/` that fails for the reason the user (or a project's request)
   gave. Keep the suite lean: only tests that would fail without the change; none for
   constants, defaults, re-exports or plumbing. Tests run in temporary folders
   (`tests/conftest.py` isolates config, data and the run store).
3. **Green.** Both pass: `.venv/bin/pytest -q` and `.venv/bin/ruff check src tests`.
   A change to the TUI is looked at before it is called done: an offscreen screenshot from
   `app.run_test(size=(140, 40))` and `app.save_screenshot(...)`, converted with
   `rsvg-convert`.
4. **Record.** User-visible behaviour in README.md and the TUI's help (`HELP` in
   `tui/screens.py`); a new CLI verb or file format also in the agent skill
   (`omarchy/skill/factory/SKILL.md`, `FORMATS.md`); a design decision in PLAN.md, dated.
5. **Pull request.** Commit with short imperative messages, push the branch and
   `gh pr create`: what was asked, why, how it was verified. The owner merges; never merge
   yourself.

A session started from a project's request (`factory requests`) hands its branch and pull
request back with `factory requests --close <id> --branch <b> --pr <url> -n "<what changed
and how the project uses it>"`; the owner accepts or rejects it in Factory.

## Guardrails

- The user's projects (`~/Hardware/…`) are their data: never change one from a test or a
  trial. Try things on a project made in a temporary folder (`factory new /tmp/x/demo`).
- Agents never commit, lock, decide or approve inside a project: `acp.FORBIDDEN` and the
  project's `.claude/settings.json` (`agent.SETTINGS`) enforce it; keep them in step with
  the rules in `agent.AGENTS_MD`.
- Requests from every project live in `~/.local/share/factory/requests`; tests use a
  temporary one.
- `assets/` is drawn by `tools/make_art.py`: change it there and redraw rather than editing
  the PNGs.
