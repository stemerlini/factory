"""The Factory app: the product breakdown on the left, the selected element's gates on top,
and its requirements, architecture, studies, results and files in tabs. The app only reads
the project and starts runs as separate processes, so closing it never stops a simulation."""

from __future__ import annotations

import asyncio
import contextlib
import os
import shlex
import shutil
import subprocess
from collections.abc import Iterable
from functools import partial
from pathlib import Path

from rich.markup import escape
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult, SystemCommand
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import (
    DataTable,
    DirectoryTree,
    Footer,
    Input,
    Label,
    Log,
    Markdown,
    Static,
    TabbedContent,
    TabPane,
    Tree,
)

from .. import (
    __version__,
    agent,
    architecture,
    config,
    decisions,
    gates,
    guide,
    home,
    journal,
    live,
    pipeline,
    review,
    runs,
    trace,
)
from ..model import GATES as GATES_ORDER
from ..model import Element
from ..modules import registry
from ..project import KICKOFF, Project, ProjectError
from . import logo
from .agentpane import AgentPane, Question
from .screens import (
    AgentScreen,
    CommentScreen,
    ConfirmScreen,
    DecideScreen,
    HelpScreen,
    NewStudyScreen,
    ProjectPicker,
    SubmitScreen,
    ToolsScreen,
)
from .theme import omarchy_theme

STUDY_STYLE = {
    pipeline.OK: "green",
    pipeline.STALE: "yellow",
    pipeline.PENDING: "dim",
    pipeline.RUNNING: "cyan",
    pipeline.FAILED: "red",
}
TRACE_STYLE = {
    trace.PASS: "green",
    trace.FAIL: "red",
    trace.STALE: "yellow",
    trace.PENDING: "dim",
    trace.OPEN: "dim",
}
GATE_STYLE = {
    gates.DONE: "green",
    gates.PARTIAL: "yellow",
    gates.TODO: "dim",
    gates.SKIPPED: "dim",
    gates.UNLOCKED: "red",
}
RUN_STYLE = {"done": "green", "failed": "red", "running": "cyan", "queued": "cyan",
             "cancelled": "yellow"}
TABS = ("home", "gates", "requirements", "architecture", "studies", "files", "agent", "review",
        "journal")
WAITING_MARK = {"review": ("●", "magenta"), "decision": ("?", "yellow"), "failed": ("✗", "red"),
                "stale": ("●", "yellow"), "unlocked": ("◆", "red"), "problem": ("!", "red"),
                "agent": ("?", "bold magenta")}
JOURNAL_STYLE = {"approve": "green", "lock": "magenta", "decision": "yellow", "decided": "green",
                 "run": "cyan", "failed": "red", "agent": "blue", "reject": "yellow",
                 "gate": "bold yellow"}
DIFF_STYLE = {"+": "green", "-": "red", "@": "cyan"}


def editor_command(path: Path) -> list[str]:
    """$VISUAL / $EDITOR split like a shell would (Omarchy's is a command with flags)."""
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    argv = shlex.split(editor) if editor else []
    return [*(argv or [shutil.which("nvim") or "vi"]), str(path)]


def mark(symbol: str, style: str) -> Text:
    return Text(symbol, style=style)


class FactoryApp(App):
    CSS_PATH = "app.tcss"
    TITLE = "Factory"
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "run_study", "Run"),
        Binding("v", "view", "View"),
        Binding("n", "new_study", "New study"),
        Binding("e", "edit", "Edit"),
        Binding("o", "open", "Open"),
        Binding("d", "decompose", "Decompose", show=False),
        Binding("L", "lock", "Lock", show=False),
        Binding("y", "mark('accept')", "Accept"),
        Binding("x", "mark('reject')", "Reject"),
        Binding("c", "comment", "Comment"),
        Binding("Y", "accept_rest", "Accept the rest"),
        Binding("S", "submit_review", "Submit review"),
        Binding("c", "cancel_run", "Cancel run", show=False),
        Binding("m", "modules", "Tools"),
        Binding("ctrl+o", "pick_project", "Projects"),
        Binding("ctrl+r", "refresh", "Reload", show=False),
        *(Binding(str(i % 10), f"tab('{t}')", t.title(), show=False)
          for i, t in enumerate(TABS, 1)),
        Binding("a", "agent", "Agent"),
        Binding("A", "agent_window", "Agent window", show=False),
        Binding("ctrl+a", "drawer", "Agent pane", priority=True),
        Binding("space", "do_item", "Do it", show=False),
        Binding("question_mark", "help", "Help"),
    ]

    def __init__(self, root: Path | None = None) -> None:
        super().__init__()
        self.root = root
        self.project: Project | None = None
        self.element_path = ""
        self.elements: dict[str, Element] = {}
        self.states: dict[str, pipeline.StudyState] = {}
        self.traces: list[trace.Trace] = []
        self.gate_states: dict[str, list[gates.GateState]] = {}
        self.nodes: dict[str, object] = {}
        self.selected_run: str | None = None
        self._log_pos = 0
        self._git_seen: tuple = ()
        self._agent_live = False
        self.pending: list[review.Change] = []
        self.marks: dict[str, dict] = {}  # the review in progress, per file
        self.home = home.Home()
        self.checks: dict[str, dict[str, list[guide.Check]]] = {}
        self._checks_shown: str | None = None
        self.lives: dict[str, live.Live] = {}
        self._agent_dirty = False
        self._agent_states: dict[str, str] = {}

    # --- layout ----------------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Horizontal(id="topbar"):
            yield Static(logo.render(), id="brand-logo")
            with Vertical(id="brand"):
                yield Static(Text("FACTORY", style="bold"), id="brand-name")
                yield Static(Text("describe · prove · build", style="dim"), id="brand-tagline")
            yield Static(id="project-line")
            yield Static(id="activity")
        with Horizontal(id="body"):
            with Vertical(id="sidebar"):
                yield Label("ELEMENTS", classes="pane-title")
                yield Tree("", id="elements")
                yield Label("TRACE", classes="pane-title")
                yield Static(id="trace-summary")
            with Vertical(id="main"):
                yield Static(id="gate-bar")
                with TabbedContent(id="tabs", initial="home"):
                    with TabPane("Home", id="home"):
                        with VerticalScroll(id="home-scroll"):
                            yield Static(id="home-hero")
                            yield Label("WAITING ON YOU", classes="pane-title")
                            yield DataTable(id="waiting-table", cursor_type="row",
                                            classes="home-table")
                            yield Label("NEXT STEPS", classes="pane-title")
                            yield DataTable(id="next-table", cursor_type="row",
                                            classes="home-table")
                            yield Label("JOURNEY", classes="pane-title")
                            yield Static(id="journey")
                            yield Label("RECENTLY", classes="pane-title")
                            yield Static(id="recent")
                    with TabPane("Gates", id="gates"):
                        with VerticalScroll():
                            yield Markdown(id="description")
                            yield DataTable(id="check-table", cursor_type="row")
                            yield Static(id="children")
                    with TabPane("Requirements", id="requirements"):
                        yield DataTable(id="req-table", cursor_type="row", zebra_stripes=True)
                        yield Markdown(id="req-detail", classes="detail")
                    with TabPane("Architecture", id="architecture"):
                        with VerticalScroll(id="arch-scroll"):
                            yield Static(id="arch-diagram")
                        yield DataTable(id="block-table", cursor_type="row", classes="detail")
                    with TabPane("Studies", id="studies"):
                        yield DataTable(id="study-table", cursor_type="row", zebra_stripes=True)
                        with Horizontal(id="study-body", classes="detail"):
                            yield Static(id="study-detail")
                            yield DataTable(id="run-table", cursor_type="row")
                        with Horizontal(id="run-body", classes="detail"):
                            yield Tree("outputs", id="output-tree")
                            yield Log(id="run-log", highlight=True)
                    with TabPane("Files", id="files"):
                        with Horizontal():
                            yield DirectoryTree(".", id="file-tree")
                            yield VerticalScroll(Static(id="file-preview"), id="file-scroll")
                    with TabPane("Agent", id="agent"):
                        yield AgentPane(id="agent-tab")
                    with TabPane("Review", id="review"):
                        yield Markdown(id="proposal")
                        yield Static(id="review-bar")
                        with Horizontal(id="review-body"):
                            yield DataTable(id="change-table", cursor_type="row")
                            yield VerticalScroll(Static(id="diff"), id="diff-scroll")
                        yield DataTable(id="decision-table", cursor_type="row",
                                        classes="detail")
                    with TabPane("Journal", id="journal"):
                        yield DataTable(id="journal-table", cursor_type="row",
                                        zebra_stripes=True)
            yield AgentPane(id="drawer")
        yield Footer()

    def on_mount(self) -> None:
        theme = omarchy_theme()
        if theme:
            self.register_theme(theme)
            self.theme = theme.name
        self.query_one("#elements", Tree).show_root = False
        self.query_one("#check-table", DataTable).add_columns("", "Gate", "Status")
        self.query_one("#req-table", DataTable).add_columns(
            "", "Id", "Title", "Criterion", "Value", "Parent", "Derived", "Allocated", "Why")
        self.query_one("#block-table", DataTable).add_columns(
            "", "Block", "Kind", "Element", "Requirements", "Gates")
        self.query_one("#study-table", DataTable).add_columns(
            "", "Id", "Title", "Task", "Gate", "Last run", "Why")
        self.query_one("#run-table", DataTable).add_columns(
            "", "Run", "Status", "Metrics", "Commit")
        self.query_one("#change-table", DataTable).add_columns(
            "", "Change", "Element", "File", "")
        self.query_one("#decision-table", DataTable).add_columns(
            "", "Decision", "Title", "By", "Recommended", "Chosen")
        self.query_one("#waiting-table", DataTable).add_columns("", "What", "Element")
        self.query_one("#next-table", DataTable).add_columns("", "Element", "Gate", "Next step")
        self.query_one("#journal-table", DataTable).add_columns(
            "When", "", "What", "Element", "Detail")
        self.query_one("#output-tree", Tree).show_root = False
        if self.root:
            self.open_project(self.root)
        else:
            self.action_pick_project()
        self.set_interval(2.0, self.tick)
        self.set_interval(0.15, self.agent_tick)

    # --- project and element ----------------------------------------------------------------

    def open_project(self, root: Path) -> None:
        try:
            self.project = Project.load(root)
        except ProjectError as e:
            self.notify(str(e), severity="error")
            return
        config.remember(self.project.root)
        if any(lv.project.root != self.project.root for lv in self.lives.values()):
            for lv in self.lives.values():
                self.run_worker(lv.close(), group="agent")
            self.lives.clear()
        self.title = f"Factory · {self.project.name}"
        self.element_path = ""
        self.rebuild_tree()
        self.refresh_all()
        self.kickoff()

    def kickoff(self) -> None:
        """A project created with an idea opens with the agent interviewing the user."""
        marker = self.project.root / KICKOFF
        try:
            idea = marker.read_text()
            marker.unlink()
        except OSError:
            return
        p = self.project
        session = self.lives[""] = live.Live(p, "", live.default_name(p), self.agent_changed)
        journal.record(p, "agent", f"{session.title} kicks off {p.name}")
        self.action_tab("agent")

        async def run() -> None:
            await session.start()
            if session.state == live.READY:
                await session.send(agent.kickoff_brief(p, idea))

        self.run_worker(run(), group="agent")

    def rebuild_tree(self) -> None:
        try:
            root = self.project.tree()
        except ProjectError as e:
            self.notify(str(e), severity="error", timeout=8)
            return
        self.elements = {e.path: e for e in root.walk()}
        tree = self.query_one("#elements", Tree)
        tree.clear()
        self.nodes = {}

        def add(parent, element: Element) -> None:
            node = parent.add(element.title, data=element.path, expand=True,
                              allow_expand=bool(element.children))
            self.nodes[element.path] = node
            for c in element.children:
                add(node, c)

        add(tree.root, root)
        if self.element_path not in self.elements:
            self.element_path = ""

    @property
    def element(self) -> Element | None:
        return self.elements.get(self.element_path)

    def refresh_all(self) -> None:
        if not self.project:
            return
        try:
            self.states = pipeline.states(self.project)
            self.traces = trace.trace(self.project, self.states)
            self.gate_states = {p: gates.gates(self.project, e, self.states, self.traces)
                                for p, e in self.elements.items()}
            self.checks = {p: guide.checklist(self.project, e, self.states, self.traces)
                           for p, e in self.elements.items()}
        except ProjectError as e:
            self.notify(str(e), severity="error", timeout=8)
            return
        for event in journal.passed_gates(self.project, self.gate_states,
                                          {p: e.title for p, e in self.elements.items()}):
            self.notify(event.title, title="★ Gate passed", timeout=6)
            self.celebrate()
        self.refresh_topbar()
        self.refresh_tree_labels()
        self.refresh_element()
        self.refresh_review()
        self.refresh_home()

    def refresh_element(self) -> None:
        if not self.element:
            return
        self.refresh_agent()
        self.refresh_trace_summary()
        self.refresh_gate_bar()
        self.refresh_gates()
        self.refresh_requirements()
        self.refresh_architecture()
        self.refresh_studies()
        self.refresh_runs()
        files = self.query_one("#file-tree", DirectoryTree)
        if files.path != self.element.dir:
            files.path = self.element.dir

    def tick(self) -> None:
        """Refresh when runs are active or files changed (an agent at work); follow the log."""
        if not self.project:
            return
        seen = tuple((c.code, c.path) for c in review.changes(self.project))
        try:
            mtimes = tuple(f.stat().st_mtime_ns for f in
                           (self.project.root / c.path for c in self.pending) if f.exists())
        except OSError:
            mtimes = ()
        changed = (seen, mtimes) != self._git_seen
        live = bool(agent.running(self.project))
        if live != self._agent_live:
            self._agent_live = live
            self.refresh_topbar()
        self._git_seen = (seen, mtimes)
        if changed or any(s.status == pipeline.RUNNING for s in self.states.values()):
            if changed and {p for p in self.elements} != {e.path for e in
                                                          self.project.elements()}:
                self.rebuild_tree()
            self.refresh_all()
        self.refresh_log()

    # --- top, tree, gates --------------------------------------------------------------------

    def celebrate(self) -> None:
        """A moment of life in the top-bar factory: smoke puffs and the windows light up."""
        frames = iter(range(12))

        def step() -> None:
            i = next(frames, None)
            art = logo.cheer(i) if i is not None else logo.FACTORY
            self.query_one("#brand-logo", Static).update(logo.render(art))

        self.set_interval(0.2, step, repeat=13)

    def refresh_topbar(self) -> None:
        p = self.project
        commit, dirty = p.commit()
        self.query_one("#project-line", Static).update(Text.assemble(
            (p.name, "bold"), f"  {p.id}  ·  ",
            (p.branch() or "no branch", "magenta"), " @ ", (commit or "no commits", "dim"),
            ("  ·  uncommitted changes", "yellow") if dirty else ""))
        active = [s for s in self.states.values() if s.status == pipeline.RUNNING]
        text = (Text(f"⟳ {len(active)} running: " + ", ".join(s.study.key for s in active), "cyan")
                if active else Text(f"v{__version__}", "dim"))
        states = {lv.state for lv in self.lives.values()}
        if live.ASKING in states:
            text = Text.assemble(("? agent asks you", "bold magenta"), "  ", text)
        elif self._agent_live or live.WORKING in states:
            text = Text.assemble(("◆ agent working", "bold magenta"), "  ", text)
        self.query_one("#activity", Static).update(text)

    def refresh_tree_labels(self) -> None:
        for path, node in self.nodes.items():
            e = self.elements[path]
            g = self.gate_states.get(path, [])
            label = Text.assemble((registry.kind(e.kind).icon + " ", "bold"), e.title, "  ")
            for x in g:
                label.append(gates.MARKS[x.status], GATE_STYLE[x.status])
            node.set_label(label)

    def refresh_trace_summary(self) -> None:
        mine = [t for t in self.traces if t.requirement.element == self.element_path]
        summary = Text(f"{self.element.title}\n", "bold")
        for k in trace.MARKS:
            n = sum(t.status == k for t in mine)
            summary.append(f"{trace.MARKS[k]} {n:>3} {k}\n", TRACE_STYLE[k])
        problems = trace.problems(self.project)
        if problems:
            summary.append(f"\n! {len(problems)} problem(s)\n", "red")
            for p in problems[:4]:
                summary.append(f"  {p}\n", "red")
        self.query_one("#trace-summary", Static).update(summary)

    def refresh_gate_bar(self) -> None:
        g = self.gate_states.get(self.element_path, [])
        cur = gates.current(g)
        bar = Text()
        for x in g:
            style = GATE_STYLE[x.status] + (" bold reverse" if cur and x.gate == cur.gate else "")
            bar.append(f" {gates.MARKS[x.status]} {x.title} ", style)
            bar.append(" ")
        bar.append("\n")
        if cur:
            bar.append(f" Next: {cur.title} — {cur.detail}", "italic")
        else:
            bar.append(" Every gate is done.", "green")
        self.query_one("#gate-bar", Static).update(bar)

    @on(Tree.NodeHighlighted, "#elements")
    def element_selected(self, event: Tree.NodeHighlighted) -> None:
        path = event.node.data
        if path is None or path == self.element_path:
            return
        self.element_path = path
        self.selected_run = None
        self.refresh_element()

    def select_element(self, path: str) -> None:
        """Make an element current, as if picked in the tree."""
        if path not in self.elements:
            return
        node = self.nodes.get(path)
        if node is not None:
            self.query_one("#elements", Tree).move_cursor(node)
        if path != self.element_path:
            self.element_path = path
            self.selected_run = None
            self.refresh_element()

    # --- home and journal ------------------------------------------------------------------

    def refresh_home(self) -> None:
        h = home.build(self.project, list(self.elements.values()), self.states,
                       self.gate_states, checks=self.checks)
        h.waiting[:0] = [home.Item("agent", f"{lv.title} asks to "
                                   f"{lv.asking.command or lv.asking.entry.text}", path)
                         for path, lv in self.lives.items() if lv.asking]
        self.home = h
        width = 30
        filled = round(h.progress * width)
        hero = Text.assemble(
            (self.project.name, "bold"), "   ",
            ("█" * filled, "green"), ("░" * (width - filled), "dim"),
            (f" {h.progress:.0%}", "bold"), " of the way")
        if h.streak > 1:
            hero.append(f"   ▲ {h.streak} days in a row", "yellow")
        if not h.waiting:
            hero.append("\nNothing is waiting on you.", "dim")
        self.query_one("#home-hero", Static).update(hero)

        table = self.query_one("#waiting-table", DataTable)
        keep = table.cursor_row
        table.clear()
        for n, item in enumerate(h.waiting):
            title = self.elements[item.element].title if item.element in self.elements else ""
            table.add_row(mark(*WAITING_MARK.get(item.kind, ("·", ""))), item.text,
                          Text(title, "dim"), key=str(n))
        if not h.waiting:
            table.add_row(mark("✓", "green"), Text("All clear: pick a next step below", "dim"),
                          "", key="none")
        table.move_cursor(row=min(keep, table.row_count - 1))

        table = self.query_one("#next-table", DataTable)
        keep = table.cursor_row
        table.clear()
        for step in h.steps:
            e = self.elements[step.element]
            table.add_row(mark(registry.kind(e.kind).icon, "bold"), e.title,
                          Text(step.gate_title, "yellow"), step.detail, key=step.element)
        if h.steps:
            table.move_cursor(row=min(keep, table.row_count - 1))

        journey = Text()
        pad = max((len(r.title) + 2 * r.depth for r in h.journey), default=10) + 2
        journey.append(" " * pad + " ".join(t[0] for _, t in GATES_ORDER) + "\n", "dim")
        for r in h.journey:
            journey.append(f"{'  ' * r.depth}{r.title}".ljust(pad))
            for status in r.statuses:
                journey.append("■ " if status == gates.DONE else
                               {gates.PARTIAL: "◐ ", gates.SKIPPED: "· ",
                                gates.UNLOCKED: "◆ "}.get(status, "□ "),
                               GATE_STYLE[status])
            journey.append("\n")
        self.query_one("#journey", Static).update(journey)

        recent = Text()
        for e in h.recent:
            recent.append(f"{e.ago:>11}  ", "dim")
            recent.append(journal.MARKS.get(e.kind, "·") + " ", JOURNAL_STYLE.get(e.kind, ""))
            recent.append(e.title + "\n")
        self.query_one("#recent", Static).update(recent or Text("Nothing yet.", "dim"))
        self.refresh_journal()

    def refresh_journal(self) -> None:
        table = self.query_one("#journal-table", DataTable)
        keep = table.cursor_row
        table.clear()
        for n, e in enumerate(journal.events(self.project, limit=500)):
            title = self.elements[e.element].title if e.element in self.elements else ""
            table.add_row(Text(f"{e.when:%Y-%m-%d %H:%M}" if e.when.year > 1 else "", "dim"),
                          mark(journal.MARKS.get(e.kind, "·"), JOURNAL_STYLE.get(e.kind, "")),
                          e.title, Text(title, "dim"), Text(e.detail, "dim"), key=str(n))
        if table.row_count:
            table.move_cursor(row=min(keep, table.row_count - 1))

    @on(DataTable.RowSelected, "#waiting-table")
    def open_waiting(self, event: DataTable.RowSelected) -> None:
        if event.row_key.value == "none":
            return
        item = self.home.waiting[int(event.row_key.value)]
        if item.element in self.elements:
            self.select_element(item.element)
        if item.kind == "agent":
            self.show_agent()
        elif item.kind == "review":
            self.action_tab("review")
        elif item.kind == "decision":
            self.decide(item.target)
        elif item.kind in ("failed", "stale"):
            self.action_tab("studies")
            table = self.query_one("#study-table", DataTable)
            try:
                table.move_cursor(row=table.get_row_index(item.target))
            except Exception:  # noqa: BLE001 - the row may have gone since the refresh
                pass
        elif item.kind == "problem":
            self.action_tab("requirements")
        else:
            self.action_tab("gates")

    @on(DataTable.RowSelected, "#next-table")
    def open_step(self, event: DataTable.RowSelected) -> None:
        self.select_element(event.row_key.value)
        self.action_tab("gates")
        self.query_one("#check-table", DataTable).focus()

    # --- tabs ------------------------------------------------------------------------------

    def refresh_gates(self) -> None:
        e = self.element
        kind = registry.kind(e.kind)
        refs = (f"\n\n**References:** {', '.join(e.references)}" if e.references else "")
        self.query_one("#description", Markdown).update(
            f"## {kind.icon} {e.title}\n\n*{kind.title}"
            + (f" · {e.path}" if e.path else " · system") + f" · prefix {e.prefix}*\n\n"
            + (e.description or "_No description yet._") + refs)
        table = self.query_one("#check-table", DataTable)
        keep = table.cursor_row
        table.clear()
        g = self.gate_states.get(self.element_path, [])
        cur = gates.current(g)
        checks = self.checks.get(self.element_path, {})
        start = None
        for x in g:
            here = cur is not None and x.gate == cur.gate
            table.add_row(mark(gates.MARKS[x.status], GATE_STYLE[x.status]),
                          Text(x.title, "bold" + (" reverse" if here else "")),
                          Text(x.detail, GATE_STYLE[x.status] if x.status != gates.TODO else ""),
                          key=f"gate:{x.gate}")
            if x.status in (gates.DONE, gates.SKIPPED):
                continue
            for n, c in enumerate(checks.get(x.gate, [])):
                if here and start is None and not c.done:
                    start = table.row_count
                table.add_row("", Text.assemble(("  ✓ ", "green") if c.done else "  □ ",
                                                (c.label, "dim" if c.done else "")),
                              Text(c.detail, "dim"), key=f"check:{x.gate}:{n}")
        if self._checks_shown != self.element_path:
            self._checks_shown = self.element_path
            keep = start or 0
        if table.row_count:
            table.move_cursor(row=min(keep, table.row_count - 1))
        text = Text()
        if e.children:
            text.append("\nSubsystems\n", "bold")
            for c in e.children:
                g = self.gate_states.get(c.path, [])
                cur = gates.current(g)
                text.append(f"  {registry.kind(c.kind).icon} {c.title:30} ")
                for x in g:
                    text.append(gates.MARKS[x.status], GATE_STYLE[x.status])
                text.append(f"  next: {cur.title if cur else 'done'}\n", "dim")
        vault = self.project.vault_dir(e)
        text.append("\nNotes & reports  ", "bold")
        text.append(str(vault) if vault else "Obsidian vault not configured "
                    "(vault = \"…\" in ~/.config/factory/config.toml)", "dim")
        self.query_one("#children", Static).update(text)

    def refresh_requirements(self) -> None:
        table = self.query_one("#req-table", DataTable)
        keep = table.cursor_row
        table.clear()
        arch = self._arch()
        for t in self.traces:
            r = t.requirement
            if r.element != self.element_path:
                continue
            allocated = list(r.allocated) + (arch.allocate.get(r.id, []) if arch else [])
            table.add_row(
                mark(trace.MARKS[t.status], TRACE_STYLE[t.status]), r.id, r.title,
                str(r.criterion or ""),
                f"{t.value.value:.4g} {t.value.unit}" if t.value else "",
                r.parent or "", ", ".join(t.children), ", ".join(dict.fromkeys(allocated)),
                Text(t.reason, "dim"), key=r.id)
        if table.row_count:
            table.move_cursor(row=min(keep, table.row_count - 1))

    def _arch(self) -> architecture.Architecture | None:
        try:
            return architecture.load(self.element.dir)
        except Exception:  # noqa: BLE001 - shown as a gate problem
            return None

    def refresh_architecture(self) -> None:
        arch = self._arch()
        diagram = self.query_one("#arch-diagram", Static)
        table = self.query_one("#block-table", DataTable)
        table.clear()
        if arch is None:
            diagram.update(Text(
                f"No {architecture.FILE} for {self.element.title}.\n\n"
                "Write one with blocks, interfaces and allocations (e edits it), "
                "or skip the Architect gate in element.md for a leaf component.", "dim"))
            return
        text, _ = architecture.render(arch)
        body = Text(text + "\n\n", "bold")
        for i in arch.interfaces:
            body.append(f"  {i.src} → {i.dst}", "")
            body.append(f"  {i.type}  {i.label}\n", "dim")
        for p in arch.problems({r.id for r in self.project.requirements(self.element)}):
            body.append(f"  ! {p}\n", "red")
        diagram.update(body)
        reqs = self.project.requirements(self.element)
        for b in arch.blocks:
            allocated = [r.id for r in reqs
                         if b.id in r.allocated or b.id in arch.allocate.get(r.id, [])]
            child = f"{self.element_path}/{b.element}".strip("/") if b.element else None
            exists = child in self.elements
            g = Text("".join(gates.MARKS[x.status] for x in self.gate_states.get(child, [])))
            table.add_row(
                mark("●" if exists else "○", "green" if exists else "dim"),
                b.title, b.kind,
                (b.element or "") + ("" if exists or not b.element else "  (d: decompose)"),
                ", ".join(allocated), g, key=b.id)

    def refresh_studies(self) -> None:
        table = self.query_one("#study-table", DataTable)
        keep = table.cursor_row
        table.clear()
        for s in self.states.values():
            if s.study.element != self.element_path:
                continue
            last = s.last
            table.add_row(
                mark(pipeline.MARKS[s.status], STUDY_STYLE[s.status]), s.study.id, s.study.title,
                s.study.task, s.study.gate,
                Text(f"{last.id} {last.status}", RUN_STYLE.get(last.status, "")) if last else "",
                Text(s.reason, "dim"), key=s.study.key)
        if table.row_count:
            table.move_cursor(row=min(keep, table.row_count - 1))

    def refresh_runs(self) -> None:
        """The runs of the highlighted study, newest first, beside its details."""
        table = self.query_one("#run-table", DataTable)
        keep = table.cursor_row
        table.clear()
        st = self.states.get(self._row_key("#study-table") or "")
        found = sorted(runs.history(self.project, st.study) if st else [],
                       key=lambda r: r.id, reverse=True)
        for r in found:
            table.add_row(
                mark("●", RUN_STYLE.get(r.status, "")), r.id,
                r.status + (f" ({r.error})" if r.error else ""),
                " ".join(f"{k}={m.value:.4g}" for k, m in r.metrics.items()),
                Text((r.commit or "-")[:8] + ("+" if r.dirty else ""), "dim"), key=r.id)
        if table.row_count:
            table.move_cursor(row=min(keep, table.row_count - 1))
        else:
            self.query_one("#run-log", Log).clear()
            self.query_one("#output-tree", Tree).clear()

    # --- details ----------------------------------------------------------------------------

    @on(DataTable.RowHighlighted, "#req-table")
    def show_requirement(self, event: DataTable.RowHighlighted) -> None:
        t = next((t for t in self.traces if t.requirement.id == event.row_key.value), None)
        if t:
            r = t.requirement
            meta = f"*{r.method} · {r.status}" + (f" · from {r.parent}" if r.parent else "") + "*"
            self.query_one("#req-detail", Markdown).update(
                f"### {r.id} — {r.title}\n\n{meta}\n\n{r.text}\n\n**Trace:** {t.status} — "
                f"{t.reason}")

    @on(DataTable.RowHighlighted, "#study-table")
    def show_study(self, event: DataTable.RowHighlighted) -> None:
        st = self.states.get(event.row_key.value)
        if not st:
            return
        s = st.study
        text = Text()
        text.append(f"{s.key} — {s.title}\n", "bold")
        if s.description:
            text.append(s.description + "\n", "dim")
        text.append(f"\ntask      {s.task}  (gate: {s.gate})\nstatus    ")
        text.append(f"{st.status} {st.reason}\n", STUDY_STYLE[st.status])
        text.append(f"params    {escape(str(s.params))}\n")
        text.append(f"inputs    {escape(str(s.inputs))}\n")
        text.append(f"verifies  {', '.join(s.verifies) or '-'}\n")
        down = pipeline.downstream([x.study for x in self.states.values()], s.key)
        text.append(f"feeds     {', '.join(down) or '-'}\n")
        if st.good and st.good.metrics:
            text.append("\nmetrics   ", "bold")
            text.append("  ".join(f"{k} = {m.value:.4g} {m.unit}"
                                  for k, m in st.good.metrics.items()))
        if st.last and st.last.toolchain:
            text.append(f"\ntoolchain {' '.join(f'{k} {v}' for k, v in st.last.toolchain.items())}",
                        "dim")
        self.query_one("#study-detail", Static).update(text)
        self.refresh_runs()

    @on(DataTable.RowHighlighted, "#run-table")
    def show_run(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key.value == self.selected_run and self._log_pos:
            return
        self.selected_run = event.row_key.value
        self._log_pos = 0
        self.query_one("#run-log", Log).clear()
        self.refresh_log()
        try:
            _, run = runs.get(self.project, self.selected_run)
        except LookupError:
            return
        tree = self.query_one("#output-tree", Tree)
        tree.clear()
        rdir = runs.run_dir(self.project, run)
        for f in sorted(rdir.rglob("*")):
            if f.is_file():
                viewer = registry.viewer_for(f)
                label = Text.assemble(str(f.relative_to(rdir)),
                                      (f"  {viewer.title}" if viewer else "", "dim"))
                tree.root.add_leaf(label, data=f)

    def refresh_log(self) -> None:
        if not (self.project and self.selected_run):
            return
        try:
            _, run = runs.get(self.project, self.selected_run)
        except LookupError:
            return
        try:
            with open(runs.run_dir(self.project, run) / "log.txt", "rb") as f:
                f.seek(self._log_pos)
                chunk = f.read()
        except OSError:
            return
        if chunk:
            self._log_pos += len(chunk)
            self.query_one("#run-log", Log).write(chunk.decode(errors="replace"))

    @on(DirectoryTree.FileSelected, "#file-tree")
    def preview_file(self, event: DirectoryTree.FileSelected) -> None:
        path = event.path
        kind = next((k for m in registry.all_modules().values() for k in m.file_kinds
                     if any(path.match(g) for g in k.globs)), None)
        header = Text(f"{kind.icon} {kind.label}\n\n" if kind else "", "bold")
        try:
            if path.stat().st_size > 512_000:
                raise ValueError("large file")
            body = Text(path.read_text())
        except (OSError, UnicodeDecodeError, ValueError):
            body = Text("(binary or large file — press o to open, v to view)", "dim")
        self.query_one("#file-preview", Static).update(header + body)

    # --- actions ---------------------------------------------------------------------------

    def _row_key(self, table_id: str) -> str | None:
        table = self.query_one(table_id, DataTable)
        if not table.row_count:
            return None
        return table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value

    def _tab(self) -> str:
        return self.query_one("#tabs", TabbedContent).active

    def copy_to_clipboard(self, text: str) -> None:
        # Textual copies with OSC 52, which not every terminal honours; wl-copy always works
        # on Wayland.
        super().copy_to_clipboard(text)
        if self.is_headless:
            return
        if shutil.which("wl-copy"):
            with contextlib.suppress(OSError, subprocess.TimeoutExpired):
                subprocess.run(["wl-copy"], input=text.encode(), timeout=2, check=False)
        lines = len(text.splitlines())
        self.notify(f"Copied {lines} line{'s' * (lines != 1)}", timeout=2)

    def action_tab(self, tab: str) -> None:
        self.query_one("#tabs", TabbedContent).active = tab

    def action_modules(self) -> None:
        if self.project:
            self.push_screen(ToolsScreen(self.project),
                             lambda changed: changed and self.action_refresh())

    def get_system_commands(self, screen: Screen) -> Iterable[SystemCommand]:
        """Every action by name in the command palette (ctrl+p), beside Textual's own."""
        yield from super().get_system_commands(screen)
        for tab in TABS:
            yield SystemCommand(f"Go to {tab.title()}", f"Show the {tab.title()} tab",
                                partial(self.action_tab, tab))
        for name, help_, action in (
            ("Agent: work on this element", "Start or show the element's agent in the pane",
             self.action_agent),
            ("Agent: toggle the pane", "Show, focus or hide the agent drawer", self.action_drawer),
            ("Agent in its own window", "Claude Code in a terminal window",
             self.action_agent_window),
            ("Run the selected study", "Runs in the background", self.action_run_study),
            ("New study", "Add a study to this element", self.action_new_study),
            ("Lock this element", "Baseline it with a git tag", self.action_lock),
            ("Switch project", "Open, or create from a template", self.action_pick_project),
            ("Tools", "The project's software suite: what it uses and what is installed",
             self.action_modules),
            ("Reload", "Re-read the project from disk", self.action_refresh),
        ):
            yield SystemCommand(name, help_, action)

    def action_refresh(self) -> None:
        if self.project:
            path = self.element_path
            self.open_project(self.project.root)
            self.element_path = path if path in self.elements else ""
            self.refresh_element()

    def action_run_study(self) -> None:
        if not self.project:
            return
        if self._tab() != "studies":
            self.action_tab("studies")
            return
        key = self._row_key("#study-table")
        if not key:
            return
        run_id = runs.start(self.project, self.project.study(key))
        self.notify(f"{key}: started run {run_id}")
        self.selected_run = run_id
        self.set_timer(0.5, self.refresh_all)

    def action_cancel_run(self) -> None:
        if self.project and self.selected_run and runs.cancel(self.project, self.selected_run):
            self.notify(f"Cancelling {self.selected_run}")

    def action_new_study(self) -> None:
        if not self.project or not self.element:
            return

        def created(path: Path | None) -> None:
            if path:
                self.notify(f"Created {path.relative_to(self.project.root)}")
                self.refresh_all()

        self.push_screen(NewStudyScreen(self.project, self.element), created)

    def action_decompose(self) -> None:
        if self._tab() != "architecture" or not self.project:
            return
        block = self._row_key("#block-table")
        if not block:
            return

        def go(ok: bool | None) -> None:
            if not ok:
                return
            try:
                folder = architecture.decompose(self.project, self.element, block)
            except ProjectError as e:
                self.notify(str(e), severity="error")
                return
            self.notify(f"Created {folder.relative_to(self.project.root)}")
            self.rebuild_tree()
            self.refresh_all()

        self.push_screen(ConfirmScreen(
            f"Create a subsystem for block '{block}', with requirements derived from the ones "
            "allocated to it?"), go)

    def action_lock(self) -> None:
        if not self.project or not self.element:
            return
        try:
            tag = gates.lock(self.project, self.element)
        except ProjectError as e:
            self.notify(str(e), severity="warning", timeout=8)
            return
        self.notify(f"{self.element.title} is baselined as {tag}", title="★ Locked", timeout=6)
        self.celebrate()
        self.refresh_all()

    def target_path(self) -> Path | None:
        tab = self._tab()
        if tab == "studies" and self.focused is self.query_one("#output-tree", Tree):
            node = self.query_one("#output-tree", Tree).cursor_node
            return node.data if node and node.data else None
        if tab == "studies" and (key := self._row_key("#study-table")):
            return self.project.study(key).dir / "study.toml"
        if tab == "requirements" and (rid := self._row_key("#req-table")):
            return self.project.requirement(rid).path
        if tab == "architecture":
            return self.element.dir / architecture.FILE
        if tab == "gates":
            return self.element.dir / "element.md"
        if tab == "files":
            node = self.query_one("#file-tree", DirectoryTree).cursor_node
            if node and node.data:
                return node.data.path
        return None

    def action_edit(self) -> None:
        path = self.target_path()
        if not path or path.is_dir():
            return
        self.edit_file(path)

    def edit_file(self, path: Path) -> None:
        argv = editor_command(path)
        try:
            with self.suspend():
                subprocess.run(argv)
        except OSError as e:
            self.notify(f"Could not start the editor ({argv[0]}): {e}", severity="error")
            return
        self.rebuild_tree()
        self.refresh_all()

    def selected_check(self) -> tuple[str, guide.Check | None] | None:
        """(gate, check) under the cursor in the Gates tab; check is None on a gate row."""
        if self._tab() != "gates" or not self.element:
            return None
        key = self._row_key("#check-table")
        if not key:
            return None
        _, gate, *n = key.split(":")
        items = self.checks.get(self.element_path, {}).get(gate, [])
        return gate, items[int(n[0])] if n and int(n[0]) < len(items) else None

    @on(DataTable.RowSelected, "#check-table")
    def check_selected(self) -> None:
        self.action_do_item()

    def action_do_item(self) -> None:
        """Space on a checklist item: do it yourself — open the right file, dialog or tab."""
        picked = self.selected_check()
        if not picked or picked[1] is None:
            return
        verb, _, arg = picked[1].action.partition(":")
        e = self.element
        if verb == "edit":
            path = e.dir / arg
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(guide.STUBS.get(arg, ""))
            self.edit_file(path)
        elif verb == "new" and arg == "req":
            self.edit_file(self.project.root / guide.new_requirement(self.project, e))
        elif verb == "new" and arg == "study":
            self.action_new_study()
        elif verb == "tab":
            self.action_tab(arg)
        elif verb == "lock":
            self.action_lock()
        elif verb == "open":
            (e.dir / arg).mkdir(parents=True, exist_ok=True)
            self.launch(["xdg-open", str(e.dir / arg)])
        elif verb == "vault":
            vault = self.project.vault_dir(e)
            if vault is None:
                self.notify('Set vault = "…" in ~/.config/factory/config.toml first',
                            severity="warning")
                return
            vault.mkdir(parents=True, exist_ok=True)
            self.launch(["xdg-open", str(vault)])

    def action_open(self) -> None:
        path = self.target_path()
        if path and path.name == "study.toml":
            path = path.parent
        if not path:
            return
        tool = next((t for m in registry.all_modules().values() for t in m.tools
                     if t.globs and any(path.match(g) for g in t.globs)), None)
        self.launch(tool.argv(path) if tool else ["xdg-open", str(path)])

    def action_view(self) -> None:
        path = self.target_path()
        if not path or path.is_dir():
            return
        viewer = registry.viewer_for(path)
        if viewer is None:
            self.notify(f"No viewer for {path.name}; o opens it with the default app")
        elif viewer.argv is None:
            self.launch(["xdg-open", str(path)])  # inline previews come in Phase 3
        else:
            self.launch(viewer.argv(path))

    @work(thread=True, exclusive=False)
    def launch(self, argv: list[str]) -> None:
        try:
            subprocess.Popen(argv, start_new_session=True, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        except OSError as e:
            self.call_from_thread(self.notify, f"{argv[0]}: {e}", severity="error")

    def action_pick_project(self) -> None:
        def chosen(root: Path | None) -> None:
            if root:
                self.open_project(root)
            elif not self.project:
                self.exit()

        self.push_screen(ProjectPicker(), chosen)

    # --- review: the human in the loop ------------------------------------------------------

    def refresh_review(self) -> None:
        p = self.project
        self.pending = review.changes(p)
        try:
            found = decisions.all_decisions(p)
        except ProjectError as e:
            self.notify(str(e), severity="error")
            found = []
        waiting = [d for d in found if d.status == "proposed"]
        text = review.proposal(p)
        if self.pending or waiting:
            self.query_one("#proposal", Markdown).update(
                text or "_No proposal note: the changes below came without an explanation._")
        else:
            self.query_one("#proposal", Markdown).update("_Nothing waiting for review._")
        self.marks = review.marks(p)
        table = self.query_one("#change-table", DataTable)
        keep = table.cursor_row
        table.clear()
        verdict = {review.ACCEPT: ("✓", "bold green"), review.REJECT: ("✗", "bold red")}
        for c in self.pending:
            m = self.marks.get(c.path, {})
            table.add_row(mark(*verdict.get(m.get("verdict"), ("·", "dim"))), c.kind,
                          c.element or "system", c.path,
                          mark("✎", "yellow") if m.get("comment") else "", key=c.path)
        if table.row_count:
            table.move_cursor(row=min(keep, table.row_count - 1))
        else:
            self.query_one("#diff", Static).update("")
        dtable = self.query_one("#decision-table", DataTable)
        dtable.clear()
        marks = {"proposed": ("?", "yellow"), "accepted": ("✓", "green"), "rejected": ("✗", "red")}
        for d in sorted(found, key=lambda d: d.status != "proposed"):
            rec = d.option(d.recommended)
            chosen = d.option(d.chosen)
            dtable.add_row(mark(*marks[d.status]), d.key, d.title, d.raised_by,
                           f"{d.recommended}: {rec.title}" if rec else "",
                           f"{d.chosen}: {chosen.title}" if chosen else "", key=d.key)
        self.refresh_review_bar()
        n = len(self.pending) + len(waiting)
        tab = self.query_one("#tabs", TabbedContent).get_tab("review")
        tab.label = f"Review ({n})" if n else "Review"

    @on(DataTable.RowHighlighted, "#change-table")
    def show_diff(self, event: DataTable.RowHighlighted) -> None:
        self.show_diff_of(event.row_key.value)

    def show_diff_of(self, path: str) -> None:
        change = next((c for c in self.pending if c.path == path), None)
        if change is None:
            return
        body = review.diff(self.project, change)
        text = Text()
        if note := self.marks.get(change.path, {}).get("comment"):
            text.append("✎ your comment\n", "bold yellow")
            text.append(note + "\n\n", "yellow")
        if change.code == "??":
            text.append(f"new file {change.path}\n\n", "bold green")
            text.append(body)
        else:
            for line in body.splitlines():
                text.append(line + "\n", DIFF_STYLE.get(line[:1], ""))
        self.query_one("#diff", Static).update(text)

    @on(DataTable.RowSelected, "#decision-table")
    def open_decision(self, event: DataTable.RowSelected) -> None:
        self.decide(event.row_key.value)

    def decide(self, key: str) -> None:
        d = decisions.get(self.project, key)
        if d.status != "proposed":
            self.notify(f"{d.key} is already {d.status}")
            return

        def done(result) -> None:
            if not result:
                return
            verdict, option, note = result
            try:
                decided = decisions.decide(self.project, d.key, verdict, option, note)
            except ProjectError as e:
                self.notify(str(e), severity="error")
                return
            self.notify(f"{decided.key}: {decided.status}"
                        + (f" ({decided.chosen})" if decided.chosen else ""))
            self.refresh_review()
            self.refresh_home()

        self.push_screen(DecideScreen(d), done)

    def refresh_review_bar(self) -> None:
        bar = self.query_one("#review-bar", Static)
        if not self.pending:
            bar.display = False
            return
        bar.display = True
        verdicts = [m.get("verdict") for m in self.marks.values()]
        comments = sum(1 for m in self.marks.values() if m.get("comment"))
        open_ = sum(1 for c in self.pending if not self.marks.get(c.path, {}).get("verdict"))
        bar.update(Text.assemble(
            (f"✓ {verdicts.count(review.ACCEPT)} accepted", "green"), "  ",
            (f"✗ {verdicts.count(review.REJECT)} rejected", "red"), "  ",
            (f"✎ {comments} comment(s)", "yellow"), "  ", (f"· {open_} to review", "bold"),
            ("   S submits the review", "dim")))

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        """The review keys only exist on the Review tab, where `c` comments instead of
        cancelling a run."""
        try:
            in_review = self._tab() == "review"
        except Exception:
            return True
        if action in ("mark", "comment", "accept_rest", "submit_review"):
            return in_review and bool(self.pending)
        if action == "cancel_run":
            return not in_review
        return True

    def change_mark(self, path: str, **values) -> None:
        m = self.marks.setdefault(path, {})
        m.update(values)
        review.save_marks(self.project, self.marks)

    def action_mark(self, verdict: str) -> None:
        """Accept or reject the file under the cursor (again to undo), then go to the next."""
        path = self._row_key("#change-table")
        if not path:
            return
        same = self.marks.get(path, {}).get("verdict") == verdict
        self.change_mark(path, verdict=None if same else verdict)
        table = self.query_one("#change-table", DataTable)
        row = table.cursor_row
        self.refresh_review()
        if not same:
            table.move_cursor(row=min(row + 1, table.row_count - 1))

    def action_accept_rest(self) -> None:
        """Accept every file not yet marked and without a comment asking for changes."""
        rest = [c.path for c in self.pending
                if not self.marks.get(c.path, {}).get("verdict")
                and not self.marks.get(c.path, {}).get("comment")]
        for path in rest:
            self.marks.setdefault(path, {})["verdict"] = review.ACCEPT
        review.save_marks(self.project, self.marks)
        self.notify(f"Accepted {len(rest)} file(s); S submits the review")
        self.refresh_review()

    def action_comment(self) -> None:
        path = self._row_key("#change-table")
        if not path:
            return

        def done(text: str | None) -> None:
            if text is not None:
                self.change_mark(path, comment=text)
                self.refresh_review()
                self.show_diff_of(path)

        self.push_screen(CommentScreen(path, self.marks.get(path, {}).get("comment", "")), done)

    def action_submit_review(self) -> None:
        verdicts = [m.get("verdict") for m in self.marks.values()]
        comments = sum(1 for m in self.marks.values() if m.get("comment"))
        accepted, rejected = verdicts.count(review.ACCEPT), verdicts.count(review.REJECT)
        pending = len(self.pending) - accepted - rejected

        def done(result: tuple[str, str] | None) -> None:
            if result is None:
                return
            message, overall = result
            try:
                out = review.submit(self.project, self.marks, message, overall)
            except ProjectError as e:
                self.notify(str(e), severity="error", timeout=8)
                return
            for path in out.rejected:
                journal.record(self.project, "reject", f"Discarded the change to {path}",
                               self.element_path)
            done_ = [f"committed {len(out.accepted)} file(s) as {out.head}"] if out.head else []
            done_ += [f"discarded {len(out.rejected)}"] if out.rejected else []
            done_ += ["sent the feedback to the agent"] if out.feedback else []
            self.notify("Review submitted: " + ", ".join(done_ or ["nothing to do"]),
                        title="★ Reviewed" if out.head else None)
            if out.head:
                self.celebrate()
            if out.feedback:
                self.tell_agent(out.feedback)
            self.rebuild_tree()
            self.refresh_all()

        self.push_screen(SubmitScreen(review.title(self.project), accepted, rejected, comments,
                                      pending), done)

    # --- the agent pane ----------------------------------------------------------------------

    def action_agent(self) -> None:
        """Start the selected element's agent in the pane, briefed on the element (or on the
        gate under the cursor); if it already runs, show it, and hand it the gate if picked."""
        if not self.project or not self.element:
            return
        p, e = self.project, self.element
        picked = self.selected_check()
        gate = picked[0] if picked else None
        session = self.lives.get(e.path)
        if session and session.state not in (live.FAILED, live.STOPPED):
            if gate and session.state == live.READY:
                self.run_worker(session.send(agent.brief(p, e, "", gate)), group="agent")
            self.show_agent()
            return
        written = agent.ensure_files(p)
        if written:
            self.notify(f"Added agent guardrails: {', '.join(written)} (review them)")
        session = self.lives[e.path] = live.Live(p, e.path, live.default_name(p),
                                                 self.agent_changed)
        what = f"{e.title}: {dict(GATES_ORDER)[gate]}" if gate else e.title
        journal.record(p, "agent", f"{session.title} on {what}", e.path)
        self.show_agent()

        async def run() -> None:
            await session.start()
            if session.state == live.READY and (gate or not session.resumed):
                await session.send(agent.brief(p, e, "", gate))

        self.run_worker(run(), group="agent")

    def tell_agent(self, text: str) -> None:
        """Hand a message to the agent: the selected element's if it runs, else the one
        that is running, else a new one on the selected element. It waits for a busy agent's
        turn to end."""
        alive = [s for s in self.lives.values() if s.state not in (live.FAILED, live.STOPPED)]
        session = self.lives.get(self.element_path)
        if session not in alive:
            session = alive[0] if len(alive) == 1 else None
        if session is None:
            p, e = self.project, self.element
            agent.ensure_files(p)
            session = self.lives[e.path] = live.Live(p, e.path, live.default_name(p),
                                                     self.agent_changed)
            journal.record(p, "agent", f"{session.title} on {e.title}: review feedback", e.path)
        if session.element in self.elements and session.element != self.element_path:
            self.select_element(session.element)
        self.show_agent()

        async def run() -> None:
            if session.conn is None:
                await session.start()
            while session.state in (live.WORKING, live.ASKING, live.STARTING):
                await asyncio.sleep(0.5)
            if session.state == live.READY:
                await session.send(text)

        self.run_worker(run(), group="agent")

    def agent_changed(self, session: live.Live) -> None:
        """Called by a session on every update: mark the panes for the next redraw, and say
        at once when the agent asks something."""
        if not self.query("#project-line"):
            return  # the app is closing; the session is being shut down
        self._agent_dirty = True
        before = self._agent_states.get(session.element)
        self._agent_states[session.element] = session.state
        if session.state != before:
            title = self.elements[session.element].title if (
                session.element in self.elements) else session.element
            if session.state == live.ASKING:
                self.notify(f"{session.title} on {title} asks you something", title="? Agent")
                if session.element == self.element_path:
                    self.show_agent()
                    self.call_after_refresh(self.focus_question)
            elif session.state == live.READY and before == live.WORKING:
                self.notify(f"{session.title} on {title} is waiting for you", timeout=4)
            self.refresh_topbar()
            self.refresh_home()

    def agent_tick(self) -> None:
        if self._agent_dirty:
            self._agent_dirty = False
            self.refresh_agent()

    def panes(self) -> list[AgentPane]:
        shown = [self.query_one("#drawer", AgentPane)] if self.drawer_open else []
        if self.query_one("#tabs", TabbedContent).active == "agent":
            shown.append(self.query_one("#agent-tab", AgentPane))
        return shown

    @property
    def drawer_open(self) -> bool:
        return self.query_one("#drawer", AgentPane).display

    def refresh_agent(self) -> None:
        if not self.element:
            return
        for pane in self.panes():
            pane.show(self.lives.get(self.element_path), self.element.title)

    def show_agent(self) -> None:
        """Bring the agent into view: the Agent tab if it is open, else the drawer."""
        if self.query_one("#tabs", TabbedContent).active != "agent":
            self.query_one("#drawer", AgentPane).display = True
        self.refresh_agent()
        session = self.lives.get(self.element_path)
        if session and session.asking:
            self.call_after_refresh(self.focus_question)
        else:
            self.call_after_refresh(self.focus_prompt)

    def focus_prompt(self) -> None:
        for pane in self.panes()[-1:]:
            pane.query_one(Input).focus()

    def focus_question(self) -> None:
        for pane in self.panes():
            question = pane.query_one(Question)
            if question.display:
                question.focus()
                return

    @on(TabbedContent.TabActivated, "#tabs")
    def tab_changed(self, event: TabbedContent.TabActivated) -> None:
        if event.pane.id == "agent":
            self.query_one("#drawer", AgentPane).display = False
            self.refresh_agent()

    def action_drawer(self) -> None:
        drawer = self.query_one("#drawer", AgentPane)
        if drawer.display and drawer.has_focus_within:
            drawer.display = False
            self.query_one("#elements", Tree).focus()
        elif drawer.display:
            self.call_after_refresh(self.focus_prompt)
        else:
            self.show_agent()

    @on(AgentPane.Send)
    def agent_send(self, event: AgentPane.Send) -> None:
        session = self.lives.get(self.element_path)
        if session and session.state == live.READY:
            self.run_worker(session.send(event.text), group="agent")
        elif session:
            self.notify(f"{session.title} is {session.state}; wait for its turn to end",
                        severity="warning")

    @on(AgentPane.Stop)
    def agent_stop(self) -> None:
        session = self.lives.get(self.element_path)
        if session and session.state in (live.WORKING, live.ASKING):
            self.run_worker(session.cancel(), group="agent")
            self.notify("Stopping the agent")
        else:
            self.query_one("#elements", Tree).focus()

    @on(Question.Answered)
    def agent_answer(self, event: Question.Answered) -> None:
        session = self.lives.get(self.element_path)
        if session:
            session.answer(event.choice)
            self.call_after_refresh(self.focus_prompt)

    async def on_unmount(self) -> None:
        for session in self.lives.values():
            await session.close()

    def action_agent_window(self) -> None:
        """Claude Code in its own terminal window, as before the pane."""
        if not self.project or not self.element:
            return
        if not shutil.which("claude"):
            self.notify("Claude Code (claude) is not on PATH", severity="error")
            return
        p, e = self.project, self.element
        picked = self.selected_check()
        if picked:
            gate = picked[0]
            agent.ensure_files(p)
            self.launch(agent.window_command(p, e, "", gate))
            journal.record(p, "agent", f"Agent on {e.title}: {dict(GATES_ORDER)[gate]}", e.path)
            self.notify(f"Agent session opening for {e.title} · {dict(GATES_ORDER)[gate]}")
            return
        live = agent.running(p)
        found = agent.sessions(p)

        def go(choice: tuple[str, str] | None) -> None:
            if not choice:
                return
            action, task = choice
            if action == "focus":
                if not any(agent.focus(pid) for pid in live):
                    self.notify("The session runs in a terminal Factory cannot find; "
                                "switch to it yourself", severity="warning")
                return
            written = agent.ensure_files(p)
            if written:
                self.notify(f"Added agent guardrails: {', '.join(written)} (review them)")
            if action in ("continue", "fork"):
                self.launch(agent.window(p, agent.resume_command(found[0], action == "fork")))
                self.notify("Continuing the last agent session")
            else:
                self.launch(agent.window_command(p, e, task))
                self.notify(f"Agent session opening for {e.title}")

        self.push_screen(AgentScreen(e.title, bool(live), found[0] if found else None), go)

    def action_help(self) -> None:
        self.push_screen(HelpScreen())
