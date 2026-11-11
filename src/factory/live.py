"""Agent sessions hosted in Factory's own pane, one per element, over ACP.

Any agent that speaks ACP works: GitHub Copilot and opencode do natively, Claude Code and
Codex through their ACP adapters. The user picks one in ``~/.config/factory/config.toml``
(``[agent] default``), a project can pick its own in ``factory.toml``, and ``[agents.<name>]
command = [...]`` adds or overrides how one is started.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field

from . import acp, config, journal
from .project import Project

AGENTS = {
    "claude": ("Claude Code", ["claude-agent-acp"],
               ["npx", "-y", "@agentclientprotocol/claude-agent-acp"]),
    "copilot": ("GitHub Copilot", ["copilot", "--acp"], None),
    "opencode": ("opencode", ["opencode", "acp"], None),
    "codex": ("Codex", ["codex-acp"], ["npx", "-y", "@zed-industries/codex-acp"]),
}
DEFAULT = "claude"
STATE_FILE = ".factory/agents.json"

STARTING, WORKING, ASKING, READY, STOPPED, FAILED = (
    "starting", "working", "asking", "ready", "stopped", "failed")


def default_name(project: Project | None = None) -> str:
    return (project and project.agent) or config.load().get("agent", {}).get("default") or DEFAULT


def title(name: str) -> str:
    return AGENTS.get(name, (name,))[0]


def argv(name: str) -> list[str]:
    """How to start agent ``name``: the user's command, else the installed one, else its
    package through npx. Raises :class:`acp.AgentError` saying what to install."""
    custom = config.load().get("agents", {}).get(name, {}).get("command")
    if custom:
        return list(custom)
    if name not in AGENTS:
        raise acp.AgentError(f"unknown agent '{name}': add [agents.{name}] command = [...] "
                             "to ~/.config/factory/config.toml")
    _, command, fallback = AGENTS[name]
    for candidate in (command, fallback):
        if candidate and shutil.which(candidate[0]):
            return list(candidate)
    need = f"{command[0]}" + (f" (or npx, for {fallback[-1]})" if fallback else "")
    raise acp.AgentError(f"{title(name)} needs {need} on the PATH")


def remembered(project: Project) -> dict:
    try:
        return json.loads((project.root / STATE_FILE).read_text())
    except (OSError, ValueError):
        return {}


def remember(project: Project, element: str, name: str, session_id: str) -> None:
    data = remembered(project)
    data[element] = {"agent": name, "session": session_id}
    path = project.root / STATE_FILE
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(data, indent=1))


@dataclass
class Entry:
    """One thing in the conversation: ``user``, ``agent``, ``thought``, ``tool``, ``plan``,
    ``refused`` or ``error``."""

    kind: str
    text: str = ""
    id: str | None = None
    status: str = ""
    raw: dict = field(default_factory=dict)


@dataclass
class Asking:
    entry: Entry
    command: str  # what "always this session" would cover, when it is a command
    options: list[dict]
    future: asyncio.Future


class Live:
    """A running agent working on one element, and its conversation so far."""

    def __init__(self, project: Project, element: str, name: str,
                 changed: Callable[[Live], None] = lambda live: None) -> None:
        self.project, self.element, self.name = project, element, name
        self.changed = changed
        self.entries: list[Entry] = []
        self.state = STARTING
        self.error = ""
        self.session_id: str | None = None
        self.asking: Asking | None = None
        self.allowed: set[str] = set()
        self.conn: acp.Connection | None = None
        self._tools: dict[str, Entry] = {}

    @property
    def title(self) -> str:
        return title(self.name)

    def _set(self, state: str) -> None:
        self.state = state
        self.changed(self)

    async def start(self) -> None:
        """Start the agent and open a session: the element's last one if the agent can
        load it (its history replays into the pane), else a new one."""
        try:
            self.conn = acp.Connection(argv(self.name), self.project.root, self.update,
                                       self.permission)
            await self.conn.start()
            params = {"cwd": str(self.project.root), "mcpServers": []}
            last = remembered(self.project).get(self.element, {})
            if self.conn.can_load and last.get("agent") == self.name and last.get("session"):
                try:
                    await self.conn.request("session/load", {**params,
                                                              "sessionId": last["session"]})
                    self.session_id = last["session"]
                except acp.AgentError:
                    self.entries.clear()
            if not self.session_id:
                self.session_id = (await self.conn.request("session/new", params))["sessionId"]
                remember(self.project, self.element, self.name, self.session_id)
            self._set(READY)
        except (acp.AgentError, KeyError, TypeError) as exc:
            self.fail(exc)

    @property
    def resumed(self) -> bool:
        return any(e.kind == "user" for e in self.entries)

    async def send(self, text: str) -> None:
        if not self.session_id or self.state in (WORKING, ASKING):
            return
        self.entries.append(Entry("user", text))
        self._set(WORKING)
        try:
            result = await self.conn.request("session/prompt", {
                "sessionId": self.session_id, "prompt": [{"type": "text", "text": text}]})
        except acp.AgentError as exc:
            if "authentication required" in str(exc).lower():
                exc = acp.AgentError(f"{self.title} is not logged in: log in once in a terminal "
                                     f"(for Claude Code, run claude and /login), or pick another "
                                     f"agent with [agent] default in ~/.config/factory/config.toml")
            self.fail(exc)
            return
        reason = result.get("stopReason", "end_turn")
        if reason not in ("end_turn", "cancelled"):
            self.entries.append(Entry("error", f"Stopped: {reason.replace('_', ' ')}"))
        self._set(READY)

    def fail(self, exc: Exception) -> None:
        self.error = str(exc)
        self.entries.append(Entry("error", self.error))
        self._set(FAILED if self.state == STARTING else STOPPED)

    async def cancel(self) -> None:
        if self.asking:
            self.answer(None)
        if self.state == WORKING and self.conn:
            await self.conn.notify("session/cancel", {"sessionId": self.session_id})

    async def close(self) -> None:
        if self.conn:
            await self.conn.close()

    # --- what the agent says ---------------------------------------------------------------

    def update(self, u: dict) -> None:
        kind = u.get("sessionUpdate")
        if kind in ("agent_message_chunk", "agent_thought_chunk", "user_message_chunk"):
            what = {"agent_message_chunk": "agent", "agent_thought_chunk": "thought",
                    "user_message_chunk": "user"}[kind]
            content = u.get("content") or {}
            text = content.get("text", "") if content.get("type") == "text" else ""
            last = self.entries[-1] if self.entries else None
            if last and last.kind == what and last.id == u.get("messageId"):
                last.text += text
            else:
                self.entries.append(Entry(what, text, u.get("messageId")))
        elif kind in ("tool_call", "tool_call_update"):
            self.tool(u)
        elif kind == "plan":
            plan = {"entries": u.get("entries") or []}
            if self.entries and self.entries[-1].kind == "plan":
                self.entries[-1].raw = plan
            else:
                self.entries.append(Entry("plan", raw=plan))
        else:
            return
        self.changed(self)

    def tool(self, u: dict) -> Entry:
        tid = u.get("toolCallId") or ""
        entry = self._tools.get(tid)
        if entry is None:
            entry = self._tools[tid] = Entry("tool", id=tid)
            self.entries.append(entry)
        entry.raw.update({k: v for k, v in u.items() if v is not None and k != "sessionUpdate"})
        entry.text = entry.raw.get("title") or entry.text or "tool"
        entry.status = entry.raw.get("status") or entry.status or "pending"
        return entry

    async def permission(self, params: dict) -> str | None:
        entry = self.tool(params.get("toolCall") or {})
        options = params.get("options") or []
        verdict = acp.decide(entry.raw, self.project.root, self.allowed)
        if verdict == acp.DENY:
            self.entries.append(Entry("refused", acp.command_of(entry.raw)))
            journal.record(self.project, "agent", f"Refused {self.title}: "
                           f"{acp.command_of(entry.raw)[:80]}", self.element)
            self.changed(self)
            return acp.pick(options, allow=False)
        if verdict == acp.ALLOW:
            return acp.pick(options, allow=True)
        command = acp.command_of(entry.raw) if entry.raw.get("kind") == "execute" else ""
        future = asyncio.get_running_loop().create_future()
        self.asking = Asking(entry, acp.scope_of(command) if command else "", options, future)
        self._set(ASKING)
        try:
            choice = await future
        finally:
            self.asking = None
            if self.state == ASKING:
                self._set(WORKING)
        return None if choice is None else acp.pick(options, allow=choice != "deny")

    def answer(self, choice: str | None) -> None:
        """The user's answer to what is being asked: ``allow``, ``always`` (this kind of
        command, for the rest of the session), ``deny``, or None to cancel."""
        if not self.asking or self.asking.future.done():
            return
        if choice == "always" and self.asking.command:
            self.allowed.add(self.asking.command)
        self.asking.future.set_result(choice)
