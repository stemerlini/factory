"""The agent pane: the conversation with the selected element's agent, a prompt line, and
the question bar when the agent asks to do something the policy leaves to the user.

The same widget is used twice, as the Agent tab and as the drawer beside any tab; both show
the one :class:`~factory.live.Live` session of the selected element."""

from __future__ import annotations

from rich.console import Group
from rich.markdown import Markdown
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import Input, Static

from .. import acp, live

TOOL_ICON = {"read": "◇", "search": "⌕", "edit": "✎", "delete": "✗", "move": "→",
             "execute": "$", "fetch": "↓", "think": "∴"}
STATUS_MARK = {"pending": ("…", "dim"), "in_progress": ("◐", "cyan"),
               "completed": ("✓", "green"), "failed": ("✗", "red")}
STATE_STYLE = {live.STARTING: ("starting…", "dim"), live.WORKING: ("working", "cyan"),
               live.ASKING: ("asks you", "bold magenta"), live.READY: ("your turn", "green"),
               live.STOPPED: ("stopped", "yellow"), live.FAILED: ("could not start", "red")}
PLAN_MARK = {"completed": ("✓", "green"), "in_progress": ("◐", "cyan")}


def render(session: live.Live | None, element: str) -> Group:
    if session is None:
        return Group(Text.assemble(
            ("No agent on ", "dim"), (element, "bold"), (" yet.\n\n", "dim"),
            ("a", "bold"), ("  start one here, briefed on this element (on a gate: that gate)\n",
                           "dim"),
            ("A", "bold"), ("  open Claude Code in its own window instead", "dim")))
    parts: list = []
    for e in session.entries:
        if e.kind == "user":
            first = e.text.strip().splitlines()[0] if e.text.strip() else ""
            more = len(e.text.strip().splitlines()) - 1
            parts += [Text(""), Text.assemble(("› ", "bold magenta"), (first, "bold"),
                                              (f"  (+{more} lines)" if more > 0 else "", "dim"))]
        elif e.kind == "agent":
            parts.append(Markdown(e.text))
        elif e.kind == "thought":
            line = " ".join(e.text.split())
            parts.append(Text("∴ " + (line[:117] + "…" if len(line) > 118 else line),
                              "dim italic"))
        elif e.kind == "tool":
            symbol, style = STATUS_MARK.get(e.status, ("·", "dim"))
            icon = TOOL_ICON.get(e.raw.get("kind", ""), "·")
            detail = acp.command_of(e.raw) if e.raw.get("kind") == "execute" else e.text
            parts.append(Text.assemble((f"  {symbol} ", style), (f"{icon} ", "bold"),
                                       (detail, "" if e.status != "failed" else "red")))
        elif e.kind == "plan":
            for step in e.raw.get("entries", []):
                symbol, style = PLAN_MARK.get(step.get("status"), ("□", "dim"))
                parts.append(Text.assemble((f"  {symbol} ", style), step.get("content", "")))
        elif e.kind == "refused":
            parts.append(Text.assemble(("  ⊘ refused ", "bold red"), (e.text, "red"),
                                       ("  (committing, locking and deciding are yours)", "dim")))
        elif e.kind == "error":
            parts.append(Text("  ! " + e.text, "bold red"))
    if session.state == live.STARTING:
        parts.append(Text(f"Starting {session.title}… (the first start may download its "
                          "ACP adapter)", "dim"))
    return Group(*parts)


class Question(Horizontal, can_focus=True):
    """What the agent asks, and the keys that answer it."""

    BINDINGS = [Binding("y", "answer('allow')", "Allow"),
                Binding("s", "answer('always')", "Always this session"),
                Binding("n", "answer('deny')", "Deny")]

    class Answered(Message):
        def __init__(self, choice: str) -> None:
            super().__init__()
            self.choice = choice

    def compose(self) -> ComposeResult:
        yield Static(id="question-text")

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
            yield Static(classes="agent-transcript")
        yield Question(classes="agent-question")
        yield Input(placeholder="Tell the agent…  (enter sends · esc stops it)",
                    classes="agent-input")

    def show(self, session: live.Live | None, element: str) -> None:
        state, style = STATE_STYLE.get(session.state, ("", "")) if session else ("", "")
        self.query_one(".agent-head", Static).update(Text.assemble(
            (session.title if session else "Agent", "bold"), "  ·  ", (element, "dim"),
            "  ", (state, style)))
        scroll = self.query_one(".agent-scroll", VerticalScroll)
        follow = scroll.scroll_y >= scroll.max_scroll_y - 2
        self.query_one(".agent-transcript", Static).update(render(session, element))
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
            question.query_one("#question-text", Static).update(Text.assemble(
                (f"{session.title} asks to run:\n" if asking.command else
                 f"{session.title} asks:\n", "bold magenta"), (what, "bold"), "\n", keys))
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
