"""Modal screens: project picker, new project, new study, help."""

from __future__ import annotations

import time
import tomllib
from pathlib import Path

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Markdown, OptionList, Select
from textual.widgets.option_list import Option

from .. import agent, config
from ..model import STUDY_GATES, Element
from ..modules import registry
from ..project import MANIFEST, Project, ProjectError, create, slug, templates, write_study

NEW = "__new__"


class ProjectPicker(ModalScreen[Path | None]):
    BINDINGS = [Binding("escape", "dismiss(None)", "Close")]

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label("Open a project", classes="dialog-title")
            options = []
            for root in config.known_projects():
                try:
                    data = tomllib.loads((root / MANIFEST).read_text())["project"]
                except (OSError, KeyError, tomllib.TOMLDecodeError):
                    continue
                options.append(Option(Text.assemble(
                    (data.get("name", root.name), "bold"), f"  {data.get('template', '')}\n",
                    (str(root), "dim")), id=str(root)))
            options.append(Option(Text("＋ New project from a template…", "green"), id=NEW))
            yield OptionList(*options, id="project-list")

    @on(OptionList.OptionSelected)
    def chosen(self, event: OptionList.OptionSelected) -> None:
        if event.option.id == NEW:
            # Textual awaits what a callback returns, and awaiting dismiss() from a
            # screen's own handler raises, so the result must not be returned.
            def created(root: Path | None) -> None:
                self.dismiss(root)

            self.app.push_screen(NewProjectScreen(), created)
        else:
            self.dismiss(Path(event.option.id))


class NewProjectScreen(ModalScreen[Path | None]):
    BINDINGS = [Binding("escape", "dismiss(None)", "Close")]

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label("New project", classes="dialog-title")
            yield Label("Template")
            yield Select([(f"{k} — {v}", k) for k, v in templates().items()],
                         value="hardware", allow_blank=False, id="template")
            yield Label("Name")
            yield Input(placeholder="Particle telescope", id="name")
            yield Label(f"Folder (under {config.projects_root()} unless absolute)")
            yield Input(placeholder="ptel", id="folder")
            yield Button("Create", variant="primary", id="create")

    @on(Input.Changed, "#name")
    def suggest_folder(self, event: Input.Changed) -> None:
        self.query_one("#folder", Input).placeholder = slug(event.value) or "ptel"

    @on(Button.Pressed, "#create")
    @on(Input.Submitted)
    def create(self) -> None:
        name = self.query_one("#name", Input).value.strip()
        folder = self.query_one("#folder", Input)
        target = Path(folder.value.strip() or folder.placeholder).expanduser()
        if not target.is_absolute():
            target = config.projects_root() / target
        try:
            p = create(target, self.query_one("#template", Select).value, name or None)
        except ProjectError as e:
            self.notify(str(e), severity="error")
            return
        self.dismiss(p.root)


class NewStudyScreen(ModalScreen[Path | None]):
    BINDINGS = [Binding("escape", "dismiss(None)", "Close")]

    def __init__(self, project: Project, element: Element) -> None:
        super().__init__()
        self.project = project
        self.element = element

    def compose(self) -> ComposeResult:
        kind = registry.kind(self.element.kind)
        tasks = []
        for m in registry.all_modules().values():
            for t in m.tasks:
                full = m.id if t.id == "main" else f"{m.id}.{t.id}"
                note = "" if m.id in kind.modules else "  ·"
                tasks.append((f"{full} — {t.title}{note}", full))
        tasks.sort(key=lambda x: x[0].endswith("·"))  # the element kind's tools first
        with Vertical(classes="dialog"):
            yield Label(f"New study in {self.element.title}", classes="dialog-title")
            yield Label("Title")
            yield Input(placeholder="Shielding trade-off", id="title")
            yield Label("Task (· = not usual for this kind of element)")
            yield Select(tasks, value=tasks[0][1], allow_blank=False, id="task")
            yield Label("Gate it serves")
            yield Select([(g.title(), g) for g in STUDY_GATES], value="analyse",
                         allow_blank=False, id="gate")
            yield Button("Create", variant="primary", id="create")

    @on(Button.Pressed, "#create")
    @on(Input.Submitted)
    def create(self) -> None:
        title = self.query_one("#title", Input).value.strip()
        if not title:
            self.notify("Give the study a title", severity="warning")
            return
        task = self.query_one("#task", Select).value
        numbers = [int(s.id[1:]) for s in self.project.studies(self.element)
                   if s.id[1:].isdigit()]
        study_id = f"S{max(numbers, default=0) + 1:03d}"
        folder = self.element.dir / "studies" / f"{study_id}-{slug(title)}"
        _, t = registry.find_task(task)
        params = {p.name: p.default for p in t.params if p.default is not None}
        write_study(folder, {
            "title": title,
            "task": task,
            "gate": self.query_one("#gate", Select).value,
            "verifies": [],
            "params": params,
            "inputs": {},
        })
        if task == "script":
            (folder / params.get("file", "run.py")).write_text(
                '"""' + title + '"""\n\nfrom factory import metrics\n\n'
                '# Inputs: json.loads(os.environ["FACTORY_INPUTS"]); '
                'parameters: FACTORY_PARAMS; outputs: FACTORY_OUTPUTS\n'
                'metrics.write("example", 1.0, "")\n')
        self.dismiss(folder)


class ConfirmScreen(ModalScreen[bool]):
    BINDINGS = [Binding("escape,n", "dismiss(False)", "No"), Binding("y", "dismiss(True)", "Yes")]

    def __init__(self, question: str) -> None:
        super().__init__()
        self.question = question

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self.question)
            yield Button("Yes (y)", variant="primary", id="yes")

    @on(Button.Pressed, "#yes")
    def yes(self) -> None:
        self.dismiss(True)


class ApproveScreen(ModalScreen[tuple[str, str] | None]):
    """Commit pending work: a message (from the proposal) and the scope."""

    BINDINGS = [Binding("escape", "dismiss(None)", "Close")]

    def __init__(self, title: str, n_all: int, n_here: int, element: str) -> None:
        super().__init__()
        self.title_text, self.n_all, self.n_here, self.element = title, n_all, n_here, element

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label("Approve changes", classes="dialog-title")
            yield Label("Commit message")
            yield Input(value=self.title_text, placeholder="What this change does", id="message")
            yield Button(f"Approve all ({self.n_all} files)", variant="primary", id="all")
            if self.n_here and self.n_here != self.n_all:
                yield Button(f"Approve {self.element} only ({self.n_here} files)", id="element")

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed) -> None:
        self.finish(event.button.id)

    @on(Input.Submitted)
    def submitted(self) -> None:
        self.finish("all")

    def finish(self, scope: str) -> None:
        message = self.query_one("#message", Input).value.strip()
        if not message:
            self.notify("Write a commit message", severity="warning")
            return
        self.dismiss((scope, message))


class DecideScreen(ModalScreen[tuple[str, str | None, str] | None]):
    """The human's answer to a proposed decision."""

    BINDINGS = [Binding("escape", "dismiss(None)", "Close")]

    def __init__(self, decision) -> None:
        super().__init__()
        self.d = decision

    def compose(self) -> ComposeResult:
        d = self.d
        with Vertical(classes="dialog wide"):
            yield Label(f"{d.key} — {d.title}", classes="dialog-title")
            yield Markdown(d.context or "_No context given._", id="decision-context")
            options = []
            for o in d.options:
                rec = "  (recommended)" if o.id == d.recommended else ""
                options.append(Option(Text.assemble((f"{o.id}  {o.title}", "bold"),
                                                    (rec, "green"), f"\n   {o.summary}"
                                                    if o.summary else ""), id=o.id))
            yield OptionList(*options, id="decision-options")
            yield Label("Note (optional, kept in the record)")
            yield Input(id="note")
            with Horizontal(classes="buttons"):
                yield Button("Accept selected", variant="primary", id="accept")
                yield Button("Reject", variant="error", id="reject")

    def on_mount(self) -> None:
        ol = self.query_one("#decision-options", OptionList)
        ids = [o.id for o in self.d.options]
        if self.d.recommended in ids:
            ol.highlighted = ids.index(self.d.recommended)

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed) -> None:
        note = self.query_one("#note", Input).value
        if event.button.id == "reject":
            self.dismiss(("reject", None, note))
            return
        ol = self.query_one("#decision-options", OptionList)
        option = ol.get_option_at_index(ol.highlighted).id if ol.highlighted is not None else None
        self.dismiss(("accept", option, note))


class AgentScreen(ModalScreen[tuple[str, str] | None]):
    """Start, continue or return to the project's agent session. Returns (action, task)."""

    BINDINGS = [Binding("escape", "dismiss(None)", "Close")]

    def __init__(self, element: str, live: bool, last: agent.Session | None) -> None:
        super().__init__()
        self.element, self.live, self.last = element, live, last

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog wide"):
            yield Label("Agent", classes="dialog-title")
            if self.live:
                yield Label("An agent session is working in this project.")
                yield Button("Go to it", variant="primary", id="focus")
            elif self.last:
                ago = humanize(time.time() - self.last.modified)
                yield Label(Text.assemble(("Last session ", "dim"), ago, ("  ·  ", "dim"),
                                          self.last.prompt[:110] or "(no prompt)"))
                yield Button("Continue it", variant="primary", id="continue")
                yield Button("Continue a copy (keeps the original)", id="fork")
            yield Label(f"New session on {self.element}")
            yield Input(placeholder="What to work on (empty: summarise and propose)", id="task")
            yield Button("Start new session", id="new",
                         variant="default" if self.live or self.last else "primary")

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed) -> None:
        self.dismiss((event.button.id, self.query_one("#task", Input).value))

    @on(Input.Submitted)
    def submitted(self) -> None:
        self.dismiss(("new", self.query_one("#task", Input).value))


def humanize(seconds: float) -> str:
    for size, unit in ((86400, "d"), (3600, "h"), (60, "min")):
        if seconds >= size:
            return f"{int(seconds // size)} {unit} ago"
    return "just now"


HELP = """\
## Factory

Every **element** — the system, each subsystem, each component — goes through the same
loop: **Describe · Research · Architect · Concept · Analyse · Lock · Design · Deliver**.
Gate status is computed from what exists; `skip: [gate]` in `element.md` skips one.

Requirements (`req/`) carry criteria on metrics; **studies** run module tasks and record
**runs** whose metrics pass or fail them; derived requirements (`parent:`) roll up. A
change marks studies, and what they verify, **stale**. Lock baselines an element (git tag).

| Key | |
|---|---|
| `1`…`7` | Overview · Requirements · Architecture · Studies · Results · Files · Modules |
| `r` | run the selected study (in the background) |
| `v` | view the selected file or output (ParaView, f3d, image…) |
| `n` | new study in this element |
| `e` | edit: element.md, a requirement, study.toml, architecture.yaml, a file |
| `o` | open with the module's tool (KiCad, FreeCAD, Geant4 viewer…) |
| `d` | decompose the selected block into a subsystem (Architecture tab) |
| `L` | lock the element (needs a commit and proven analysis) |
| `c` | cancel the selected run |
| `a` | agent: go to the running session, continue the last one, or start one here |
| `8` | Review: the agent's proposal, its changes (diff) and open decisions |
| `y` / `x` | approve (commit) / discard pending changes (Review tab) |
| `enter` | answer the selected decision (Review tab) |
| `ctrl+o` / `ctrl+r` | switch project / reload |

Gates: ✓ done · ◐ in progress · ○ to do · – skipped · ● unlocked by a change.
Studies: ✓ up to date · ● stale · ○ never ran · ⟳ running · ✗ failed.
Requirements: ✓ pass · ✗ fail · ● stale · ◌ pending · ○ open.
"""


class HelpScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape,q,question_mark", "dismiss(None)", "Close")]

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog wide"):
            yield Markdown(HELP)
