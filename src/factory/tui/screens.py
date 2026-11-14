"""Modal screens: project picker, new project, new study, help."""

from __future__ import annotations

import textwrap
import time
import tomllib
from pathlib import Path

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    DataTable,
    Input,
    Label,
    Markdown,
    OptionList,
    Select,
    Static,
    TextArea,
)
from textual.widgets.option_list import Option

from .. import agent, config, portal, requests
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
    """Name, template and location; the project gets its own folder, named after it,
    inside the chosen location."""

    BINDINGS = [Binding("escape", "dismiss(None)", "Close"),
                Binding("ctrl+b", "browse", "Choose location")]

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="dialog wide"):
            yield Label("New project", classes="dialog-title")
            yield Label("Name")
            yield Input(placeholder="Particle telescope", id="name")
            yield Label(Text.assemble("The idea ", ("optional: in your words. The agent asks "
                                                    "what it needs, then drafts the rest", "dim")))
            yield TextArea(id="idea", classes="idea")
            yield Label("Template")
            yield Select([(f"{k} — {v}", k) for k, v in templates().items()],
                         value="hardware", allow_blank=False, id="template")
            yield Label("Location")
            with Horizontal(classes="field-row"):
                yield Input(str(config.projects_root()), id="location")
                yield Button("Browse…", id="browse")
            yield Label("Folder name")
            yield Input(placeholder="project", id="folder")
            yield Static(id="target", classes="hint")
            yield Button("Create", variant="primary", id="create")

    def on_mount(self) -> None:
        self.query_one("#name", Input).focus()
        self.show_target()

    def target(self) -> Path:
        location = Path(self.query_one("#location", Input).value.strip() or "~").expanduser()
        folder = self.query_one("#folder", Input)
        return location / (folder.value.strip() or folder.placeholder)

    @on(Input.Changed)
    def show_target(self, event: Input.Changed | None = None) -> None:
        if event and event.input.id == "name":
            self.query_one("#folder", Input).placeholder = slug(event.value) or "project"
        target = self.target()
        clash = target.exists() and any(target.iterdir()) if target.is_dir() else target.exists()
        self.query_one("#target", Static).update(Text.assemble(
            ("Creates ", "dim"), (str(target), "bold"),
            ("  — already exists and is not empty" if clash else "", "red")))

    @on(Button.Pressed, "#browse")
    def action_browse(self) -> None:
        start = Path(self.query_one("#location", Input).value.strip() or "~").expanduser()
        self.run_worker(lambda: self._browse(start), thread=True, exclusive=True)

    def _browse(self, start: Path) -> None:
        try:
            chosen = portal.choose_folder("Where to create the project", start)
        except portal.PortalError as e:
            self.app.call_from_thread(
                self.notify, f"Can't open the folder chooser: {e}. Type the location instead.",
                severity="error")
            return
        if chosen:
            self.app.call_from_thread(self._set_location, chosen)

    def _set_location(self, path: Path) -> None:
        self.query_one("#location", Input).value = str(path)
        self.query_one("#folder", Input).focus()

    @on(Button.Pressed, "#create")
    @on(Input.Submitted)
    def create(self) -> None:
        name = self.query_one("#name", Input).value.strip()
        target = self.target()
        if not target.is_absolute():
            target = config.projects_root() / target
        try:
            p = create(target, self.query_one("#template", Select).value, name or None,
                       idea=self.query_one("#idea", TextArea).text)
        except (ProjectError, OSError) as e:
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


class CommentScreen(ModalScreen[str | None]):
    """A comment on one file of the agent's proposal: what to change, for the agent."""

    BINDINGS = [Binding("escape", "dismiss(None)", "Close"),
                Binding("ctrl+s", "save", "Save", priority=True)]

    def __init__(self, path: str, text: str = "") -> None:
        super().__init__()
        self.path, self.text = path, text

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog wide"):
            yield Label("Comment", classes="dialog-title")
            yield Static(Text.assemble(("on ", "dim"), self.path), classes="hint")
            yield TextArea(self.text, id="comment", classes="comment")
            yield Static("What should change? The agent gets it when you submit the review. "
                         "ctrl+s saves; empty removes the comment.", classes="hint")
            yield Button("Save comment", variant="primary", id="save")

    def on_mount(self) -> None:
        self.query_one(TextArea).focus()

    @on(Button.Pressed, "#save")
    def action_save(self) -> None:
        self.dismiss(self.query_one(TextArea).text.strip())


class SubmitScreen(ModalScreen[tuple[str, str] | None]):
    """Submit the review: what gets committed, discarded and sent back to the agent."""

    BINDINGS = [Binding("escape", "dismiss(None)", "Close"),
                Binding("ctrl+s", "submit", "Submit", priority=True)]

    def __init__(self, title: str, accepted: int, rejected: int, comments: int,
                 pending: int) -> None:
        super().__init__()
        self.title_text = title
        self.counts = accepted, rejected, comments, pending

    def compose(self) -> ComposeResult:
        accepted, rejected, comments, pending = self.counts
        with Vertical(classes="dialog wide"):
            yield Label("Submit review", classes="dialog-title")
            yield Static(Text.assemble(
                (f"✓ {accepted} accepted", "green"), " will be committed   ",
                (f"✗ {rejected} rejected", "red"), " will be discarded\n",
                (f"✎ {comments} comment(s)", "yellow"), " go to the agent   ",
                (f"{pending} file(s)", "bold"), " stay pending"))
            if accepted:
                yield Label("Commit message")
                yield Input(value=self.title_text, placeholder="What this change does",
                            id="message")
            yield Label("Overall feedback for the agent (optional)")
            yield TextArea(id="overall", classes="comment")
            yield Button("Submit review", variant="primary", id="submit")
            yield Static("ctrl+s submits · esc goes back to the files", classes="hint")

    @on(Button.Pressed, "#submit")
    @on(Input.Submitted)
    def action_submit(self) -> None:
        found = self.query("#message")
        message = found.first(Input).value.strip() if found else ""
        if self.counts[0] and not message:
            self.notify("Accepted files are committed: write a commit message",
                        severity="warning")
            return
        self.dismiss((message, self.query_one("#overall", TextArea).text.strip()))


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
| `1`…`9` | Home, Gates, Requirements, Architecture, Studies, Files, Agent, Review, Journal |
| `enter` | on Home: go to what waits on you, or to an element's next step |
| `space` | on a Gates item: do it (open the file, dialog or tab it needs) |
| `a` | on a Gates item: hand that gate to an agent, with its playbook |
| `r` | run the selected study (in the background) |
| `v` | view the selected file or output (ParaView, f3d, image…) |
| `n` | new study in this element |
| `e` | edit: element.md, a requirement, study.toml, architecture.yaml, a file |
| `o` | open with the module's tool (KiCad, FreeCAD, Geant4 viewer…) |
| `d` | decompose the selected block into a subsystem (Architecture tab) |
| `L` | lock the element (needs a commit and proven analysis) |
| `c` | cancel the selected run |
| `m` | tools: what the project uses, what is installed; `e` enables a module |
| `R` | requests the agents filed to your tools; `enter` sends one to a dev session |
| `ctrl+p` | command palette: every action by name |
| `a` | agent: start (or show) this element's agent in the pane, briefed on it |
| `ctrl+a` | show, focus or hide the agent pane; `esc` in it stops the agent |
| `y` `n` `s` | agent asks: allow, deny, always allow that command (`↑↓` scroll it) |
| `A` | Claude Code in its own terminal window instead (continue, fork, new) |
| `7` | Agent: this element's conversation, full size |
| `8` | Review: the agent's proposal, its changes (diff) and open decisions |
| `9` | Journal: approvals, locks, decisions, runs and gates passed, newest first |
| `y` / `x` / `c` | Review: accept / reject / comment on the file (`Y` accepts the rest) |
| `S` | Review: submit — commit accepted, discard rejected, send comments to the agent |
| `enter` | answer the selected decision (Review tab) |
| `ctrl+o` / `ctrl+r` | switch project / reload |

Gates: ✓ done · ◐ in progress · ○ to do · – skipped · ● unlocked by a change.
Studies: ✓ up to date · ● stale · ○ never ran · ⟳ running · ✗ failed.
Requirements: ✓ pass · ✗ fail · ● stale · ◌ pending · ○ open.
"""


class ToolsScreen(ModalScreen[bool]):
    """The project's software suite: the modules it uses, whether their tools are installed
    on this machine and how to install what is missing; then the rest, to enable."""

    BINDINGS = [Binding("escape,q,m", "close", "Close"),
                Binding("e", "toggle", "Enable / disable for this project")]

    def __init__(self, project: Project) -> None:
        super().__init__()
        self.project = project
        self.changed = False

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog modules"):
            yield Label(f"Tools · {self.project.name}", classes="dialog-title")
            yield Static("● this project uses it · ○ available · red: not installed here"
                         "  ·  e enables or disables the module for this project",
                         classes="hint")
            yield DataTable(id="module-table", cursor_type="row")

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("", "Module", "Used by", "On this machine")
        self.fill()
        table.focus()

    def fill(self) -> None:
        table = self.query_one(DataTable)
        row = table.cursor_row
        table.clear()
        for m, used, studies in registry.suite(self.project):
            health = m.health()
            ok = all(c.ok for c in health)
            enabled = m.id in self.project.modules
            usedby = ([("enabled", "dim")] if enabled else
                      [("not enabled", "yellow")] if used else [])
            usedby += [f"\n{k}" for k in studies]
            missing = "red" if used else "dim yellow"
            checks = Text("\n".join(f"{'✓' if c.ok else '✗'} {c.name}: {c.detail}"
                                    for c in health) or "✓ nothing to install",
                          "green" if ok else missing)
            table.add_row(
                Text("●" if used else "○", ("green" if ok else "red") if used else "dim"),
                Text.assemble((m.title, "bold" if used else ""), "\n",
                              ("\n".join(textwrap.wrap(m.description, 44)), "dim")),
                Text.assemble(*usedby) if usedby else "",
                checks, height=max(1 + len(textwrap.wrap(m.description, 44)), len(health),
                                   len(studies) + 1), key=m.id)
        table.move_cursor(row=min(row, table.row_count - 1))

    def action_toggle(self) -> None:
        table = self.query_one(DataTable)
        if not table.row_count:
            return
        key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
        on = key not in self.project.modules
        self.project.enable(key, on)
        self.changed = True
        self.notify(f"{registry.get(key).title} {'enabled' if on else 'disabled'} "
                    "for this project (factory.toml)")
        self.fill()

    def action_close(self) -> None:
        self.dismiss(self.changed)


class HelpScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape,q,question_mark", "dismiss(None)", "Close")]

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog wide"):
            yield Markdown(HELP)


class RequestsScreen(ModalScreen[bool]):
    """What the project agents asked of your tools. Enter sends a request to a development
    session in the tool's repository; that session closes it when the change is in."""

    BINDINGS = [Binding("escape,q,R", "close", "Close"),
                Binding("x", "dismiss_request", "Dismiss"),
                Binding("a", "toggle_all", "Show done")]

    def __init__(self, project: Project | None) -> None:
        super().__init__()
        self.project = project
        self.everything = False
        self.changed = False

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog modules"):
            yield Label("Requests to your tools", classes="dialog-title")
            yield Static("Features, fixes and materials your project agents found missing.  "
                         "enter opens a dev session in the tool's repo with the request as "
                         "its brief · x dismisses · a shows done ones", classes="hint")
            yield DataTable(id="request-table", cursor_type="row")
            yield Static(id="request-body", classes="detail")

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("", "Id", "Request", "To", "From", "Filed")
        self.fill()
        table.focus()

    def fill(self) -> None:
        table = self.query_one(DataTable)
        row = table.cursor_row
        table.clear()
        self.found = {r.id: r for r in requests.all_requests(everything=self.everything)}
        marks = {requests.OPEN: ("⇢", "cyan"), requests.SENT: ("…", "yellow"),
                 requests.DONE: ("✓", "green"), requests.DISMISSED: ("✗", "dim")}
        for r in self.found.values():
            mine = self.project and r.root == str(self.project.root)
            table.add_row(Text(*marks.get(r.status, ("·", ""))),
                          Text(r.id, "bold"), Text.assemble(r.title, "  ", (r.kind, "dim")),
                          r.to, Text(r.project or "", "" if mine else "dim"),
                          requests.age(r), key=r.id)
        if not self.found:
            self.query_one("#request-body", Static).update(
                "Nothing waiting. Project agents file requests with `factory request`.")
        table.move_cursor(row=min(row, max(table.row_count - 1, 0)))

    @on(DataTable.RowHighlighted)
    def show(self, event: DataTable.RowHighlighted) -> None:
        r = self.found.get(event.row_key.value) if event.row_key else None
        if r:
            self.query_one("#request-body", Static).update(Text.assemble(
                (f"{r.status}", "bold"), f" · {r.kind} for {r.to}",
                (f" · {r.project}{'/' + r.element if r.element else ''}" if r.project else ""),
                "\n\n", r.body or "(no detail)",
                ("\n\nDone: " + r.note if r.note else "", "green")))

    def current(self):
        table = self.query_one(DataTable)
        if not table.row_count:
            return None
        return self.found[table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value]

    @on(DataTable.RowSelected)
    def send(self, _event=None) -> None:
        r = self.current()
        if not r:
            return
        try:
            argv = requests.window(r)
        except LookupError as e:
            self.notify(str(e), severity="error")
            return
        self.app.launch(argv)
        if r.status == requests.OPEN:
            requests.close(r.id, requests.SENT)
        self.changed = True
        self.notify(f"Opened a {r.to} dev session for {r.id}")
        self.fill()

    def action_dismiss_request(self) -> None:
        r = self.current()
        if r and r.status in (requests.OPEN, requests.SENT):
            requests.close(r.id, requests.DISMISSED, "dismissed by the user")
            self.changed = True
            self.fill()

    def action_toggle_all(self) -> None:
        self.everything = not self.everything
        self.fill()

    def action_close(self) -> None:
        self.dismiss(self.changed)
