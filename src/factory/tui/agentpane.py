"""The agent pane: the conversation with the selected element's agent, a prompt line, and
the question bar when the agent asks to do something the policy leaves to the user.

The same widget is used twice, as the Agent tab and as the drawer beside any tab; both show
the one :class:`~factory.live.Live` session of the selected element."""

from __future__ import annotations

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Input, Markdown, Static

from .. import acp, live

TOOL_ICON = {"read": "◇", "search": "⌕", "edit": "✎", "delete": "✗", "move": "→",
             "execute": "$", "fetch": "↓", "think": "∴"}
STATUS_MARK = {"pending": ("…", "dim"), "in_progress": ("◐", "cyan"),
               "completed": ("✓", "green"), "failed": ("✗", "red")}
STATE_STYLE = {live.STARTING: ("starting…", "dim"), live.WORKING: ("working", "cyan"),
               live.ASKING: ("asks you", "bold magenta"), live.READY: ("your turn", "green"),
               live.STOPPED: ("stopped", "yellow"), live.FAILED: ("could not start", "red")}
PLAN_MARK = {"completed": ("✓", "green"), "in_progress": ("◐", "cyan")}


def blocks(session: live.Live | None, element: str) -> list[str | Text]:
    """The transcript as blocks: the agent's replies as Markdown source, everything between
    them as one styled text. Both become widgets the user can select and copy from."""
    if session is None:
        return [Text.assemble(
            ("No agent on ", "dim"), (element, "bold"), (" yet.\n\n", "dim"),
            ("a", "bold"), ("  start one here, briefed on this element (on a gate: that gate)\n",
                           "dim"),
            ("A", "bold"), ("  open Claude Code in its own window instead", "dim"))]
    found: list[str | Text] = []
    lines: list[Text] = []

    def line(text: Text) -> None:
        lines.append(text)

    def flush() -> None:
        if lines:
            found.append(Text("\n").join(lines))
            lines.clear()

    for e in session.entries:
        if e.kind == "user":
            first = e.text.strip().splitlines()[0] if e.text.strip() else ""
            more = len(e.text.strip().splitlines()) - 1
            line(Text(""))
            line(Text.assemble(("› ", "bold magenta"), (first, "bold"),
                               (f"  (+{more} lines)" if more > 0 else "", "dim")))
        elif e.kind == "agent":
            flush()
            found.append(e.text)
        elif e.kind == "thought":
            text = " ".join(e.text.split())
            line(Text("∴ " + (text[:117] + "…" if len(text) > 118 else text), "dim italic"))
        elif e.kind == "tool":
            symbol, style = STATUS_MARK.get(e.status, ("·", "dim"))
            icon = TOOL_ICON.get(e.raw.get("kind", ""), "·")
            detail = acp.command_of(e.raw) if e.raw.get("kind") == "execute" else e.text
            rows = detail.splitlines() or [""]
            line(Text.assemble((f"  {symbol} ", style), (f"{icon} ", "bold"),
                               (rows[0], "" if e.status != "failed" else "red"),
                               (f"  (+{len(rows) - 1} lines)" if len(rows) > 1 else "",
                                "dim")))
        elif e.kind == "plan":
            for step in e.raw.get("entries", []):
                symbol, style = PLAN_MARK.get(step.get("status"), ("□", "dim"))
                line(Text.assemble((f"  {symbol} ", style), step.get("content", "")))
        elif e.kind == "refused":
            line(Text.assemble(("  ⊘ refused ", "bold red"), (e.text, "red"),
                               ("  (committing, locking and deciding are yours)", "dim")))
        elif e.kind == "error":
            line(Text("  ! " + e.text, "bold red"))
    if session.state == live.STARTING:
        line(Text(f"Starting {session.title}… (the first start may download its "
                  "ACP adapter)", "dim"))
    flush()
    return found


class Earlier(Static):
    """The line above a long transcript: how much is not shown, and a click shows more."""

    def on_click(self) -> None:
        self.parent.show_more()


class Transcript(Vertical):
    """The conversation as a column of widgets, updated in place: only the blocks that
    changed are re-rendered, so a streaming reply does not redraw the whole history. Only
    the latest blocks are mounted: every widget adds to each layout pass, and a long session
    would otherwise make a streaming reply stutter."""

    LIMIT = 20  # blocks mounted at first
    STEP = 10  # the window moves on in steps, so a new block rarely remounts anything

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.shown: list[str | Text] = []
        # The widget of each shown block: mounting and removing finish later, so
        # ``children`` can lag behind when updates come quickly.
        self.widgets: list[Widget] = []
        self.found: list[str | Text] = []
        self.start = 0
        self.limit = self.LIMIT

    def compose(self) -> ComposeResult:
        yield Earlier(classes="agent-earlier")

    def reset(self) -> None:
        for w in self.widgets:
            w.remove()
        self.widgets, self.shown, self.start, self.limit = [], [], 0, self.LIMIT

    def show_more(self) -> None:
        limit, found = self.limit + self.LIMIT, self.found
        self.reset()
        self.limit = limit
        self.show(found)

    def show(self, found: list[str | Text]) -> None:
        self.found = found
        widgets = self.widgets
        if not self.shown or self.start > len(found):
            for w in widgets:
                w.remove()
            widgets, self.shown = [], []
            self.start = max(0, len(found) - self.limit)
        elif len(found) - self.start > self.limit + self.STEP:
            drop = min(len(found) - self.limit - self.start, len(self.shown))
            for w in widgets[:drop]:
                w.remove()
            widgets, self.shown = widgets[drop:], self.shown[drop:]
            self.start += drop
        visible = found[self.start:]
        for i, block in enumerate(visible):
            if i < len(self.shown) and type(self.shown[i]) is type(block):
                old = self.shown[i]
                if old == block:
                    continue
                # A streaming reply only grows: parse the new tail, not the whole reply.
                if isinstance(block, str) and block.startswith(old):
                    widgets[i].append(block[len(old):])
                else:
                    widgets[i].update(block)
                continue
            for w in widgets[i:]:
                w.remove()
            widgets = widgets[:i] + [Markdown(b, classes="agent-reply") if isinstance(b, str)
                                     else Static(b, classes="agent-lines") for b in visible[i:]]
            self.mount_all(widgets[i:])
            break
        else:
            for w in widgets[len(visible):]:
                w.remove()
            widgets = widgets[:len(visible)]
        self.widgets = widgets
        self.shown = [b.copy() if isinstance(b, Text) else b for b in visible]
        earlier = self.query_one(Earlier)
        earlier.display = self.start > 0
        if self.start:
            earlier.update(Text.assemble((f"▲ {self.start} earlier blocks", "dim"),
                                         ("  click to show more", "dim italic")))


class Question(Vertical, can_focus=True):
    """What the agent asks, and the keys that answer it. A long command scrolls in its own
    box, so the keys stay in view however much the agent wants to run."""

    BINDINGS = [Binding("y", "answer('allow')", "Allow"),
                Binding("s", "answer('always')", "Always this session"),
                Binding("n", "answer('deny')", "Deny"),
                Binding("up", "scroll_what(-1)", "Scroll", show=False),
                Binding("down", "scroll_what(1)", "Scroll", show=False),
                Binding("pageup", "scroll_what(-10)", "Scroll", show=False),
                Binding("pagedown", "scroll_what(10)", "Scroll", show=False)]

    class Answered(Message):
        def __init__(self, choice: str) -> None:
            super().__init__()
            self.choice = choice

    def compose(self) -> ComposeResult:
        yield Static(id="question-title")
        with VerticalScroll(id="question-scroll"):
            yield Static(id="question-text")
        yield Static(id="question-keys")

    def ask(self, title: str, what: str, keys: Text) -> None:
        if getattr(self, "_what", None) != what:
            self._what = what
            self.query_one("#question-scroll", VerticalScroll).scroll_home(animate=False)
        self.query_one("#question-title", Static).update(Text(title, "bold magenta"))
        self.query_one("#question-text", Static).update(Text(what, "bold"))
        lines = len(what.splitlines())
        self.query_one("#question-keys", Static).update(
            keys + Text(f"   ↑↓ scroll the {lines} lines", "dim") if lines > 8 else keys)

    def action_scroll_what(self, rows: int) -> None:
        scroll = self.query_one("#question-scroll", VerticalScroll)
        scroll.scroll_relative(y=rows, animate=False)

    def action_answer(self, choice: str) -> None:
        self.post_message(self.Answered(choice))


class AgentPane(Vertical):
    class Send(Message):
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    class Stop(Message):
        pass

    def compose(self) -> ComposeResult:
        yield Static(classes="agent-head")
        with VerticalScroll(classes="agent-scroll"):
            yield Transcript(classes="agent-transcript")
        yield Question(classes="agent-question")
        yield Input(placeholder="Tell the agent…  (enter sends · esc stops it · "
                          "drag to select, ctrl+c copies)",
                    classes="agent-input")

    def show(self, session: live.Live | None, element: str) -> None:
        state, style = STATE_STYLE.get(session.state, ("", "")) if session else ("", "")
        self.query_one(".agent-head", Static).update(Text.assemble(
            (session.title if session else "Agent", "bold"),
            (f" ({session.model})" if session and session.model else "", "dim"),
            "  ·  ", (element, "dim"), "  ", (state, style)))
        scroll = self.query_one(".agent-scroll", VerticalScroll)
        follow = scroll.scroll_y >= scroll.max_scroll_y - 2
        transcript = self.query_one(Transcript)
        if getattr(self, "_session", None) is not session:
            self._session = session
            transcript.reset()
        transcript.show(blocks(session, element))
        if follow:
            scroll.call_after_refresh(scroll.scroll_end, animate=False)
        question = self.query_one(Question)
        asking = session.asking if session else None
        question.display = asking is not None
        if asking:
            what = (acp.command_of(asking.entry.raw) if asking.command else
                    asking.entry.text + "".join(f"\n  {p}" for p in acp.paths_of(asking.entry.raw)))
            keys = Text.assemble(("y", "bold"), " allow  ", ("n", "bold"), " deny")
            if asking.command:
                keys.append_text(Text.assemble("  ", ("s", "bold"),
                                               f" always allow `{asking.command}` this session"))
            more = len(session.questions) - 1
            if more:
                keys.append(f"   ({more} more waiting)", style="dim")
            question.ask(f"{session.title} asks to run:" if asking.command else
                         f"{session.title} asks:", what, keys)
        self.query_one(Input).disabled = session is None or session.state in (
            live.STARTING, live.FAILED, live.STOPPED)

    @on(Input.Submitted)
    def submitted(self, event: Input.Submitted) -> None:
        event.stop()
        if event.value.strip():
            self.post_message(self.Send(event.value.strip()))
            event.input.value = ""

    def key_escape(self) -> None:
        self.post_message(self.Stop())
