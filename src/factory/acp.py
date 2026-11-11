"""A small Agent Client Protocol client, so Factory can host any ACP agent in its own pane.

ACP is JSON-RPC 2.0 over the agent's stdin and stdout, one message per line. Factory is the
client: it starts the agent, opens a session in the project folder, sends prompts, shows the
streamed ``session/update`` notifications and answers ``session/request_permission`` by its
policy (see :func:`decide`), asking the user only when the policy says so.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import shlex
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__

PROTOCOL_VERSION = 1

# Agents never commit, rewrite history, lock or decide: those are the user's, in Review.
FORBIDDEN = re.compile(
    r"\bgit(?:\s+-\S+(?:\s+(?!-)\S+)?)*\s+(commit|push|reset|checkout|restore|tag|rebase)\b"
    r"|\bfactory\s+(lock|decide|approve|reject)\b")
ALLOW, DENY, ASK = "allow", "deny", "ask"
SAFE_KINDS = {"read", "search", "think", "fetch"}
EDIT_KINDS = {"edit", "delete", "move"}


class AgentError(Exception):
    pass


def command_of(tool: dict) -> str:
    """The shell command an ``execute`` tool call runs, as well as it can be told."""
    raw = tool.get("rawInput")
    if isinstance(raw, dict):
        cmd = raw.get("command") or raw.get("cmd")
        if isinstance(cmd, list):
            return shlex.join(str(c) for c in cmd)
        if cmd:
            return str(cmd)
    elif isinstance(raw, str):
        return raw
    return str(tool.get("title") or "")


def scope_of(command: str) -> str:
    """What "always this session" covers: the program and its subcommand (``pytest``,
    ``git diff``), never the whole shell line, so ``ls; rm -rf x`` is not a pass for ``ls``."""
    try:
        words = shlex.split(command)
    except ValueError:
        return command
    if not words or any(c in command for c in ";&|`$<>\n"):
        return command
    if len(words) > 1 and not words[1].startswith("-"):
        return " ".join(words[:2])
    return words[0]


def paths_of(tool: dict) -> list[Path]:
    paths = [loc.get("path") for loc in tool.get("locations") or []]
    paths += [c.get("path") for c in tool.get("content") or [] if c.get("type") == "diff"]
    raw = tool.get("rawInput")
    if isinstance(raw, dict):
        paths += [raw.get(k) for k in ("file_path", "path", "notebook_path")]
    return [Path(p) for p in paths if isinstance(p, str) and p]


def inside(path: Path, root: Path) -> bool:
    try:
        (root / path).resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def decide(tool: dict, root: Path, allowed: set[str] = frozenset()) -> str:
    """The policy for a permission request: reads are fine, edits inside the project are fine,
    commands and anything outside the project are asked, and committing, locking or deciding
    is refused whatever was allowed before."""
    kind = tool.get("kind") or "other"
    text = " ".join([command_of(tool), str(tool.get("title") or "")])
    if FORBIDDEN.search(text):
        return DENY
    if kind in SAFE_KINDS:
        return ALLOW
    if kind in EDIT_KINDS:
        paths = paths_of(tool)
        return ALLOW if paths and all(inside(p, root) for p in paths) else ASK
    if kind == "execute" and scope_of(command_of(tool)) in allowed:
        return ALLOW
    return ASK


def pick(options: list[dict], allow: bool) -> str | None:
    """The option id that allows (or rejects) once, falling back to any option of that sense."""
    for kinds in (("allow_once",), ("allow_once", "allow_always")) if allow else (
            ("reject_once",), ("reject_once", "reject_always")):
        for o in options:
            if o.get("kind") in kinds:
                return o.get("optionId")
    return None


@dataclass
class Connection:
    """One running agent process and the JSON-RPC conversation with it."""

    argv: list[str]
    cwd: Path
    on_update: Callable[[dict], None]
    on_permission: Callable[[dict], Awaitable[str | None]]
    proc: asyncio.subprocess.Process | None = None
    info: dict = field(default_factory=dict)
    stderr: deque = field(default_factory=lambda: deque(maxlen=40))
    _next: int = 0
    _waiting: dict = field(default_factory=dict)
    _tasks: list = field(default_factory=list)

    async def start(self) -> dict:
        try:
            self.proc = await asyncio.create_subprocess_exec(
                *self.argv, cwd=self.cwd, stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                limit=16 * 1024 * 1024)
        except OSError as exc:
            raise AgentError(f"cannot start {self.argv[0]}: {exc.strerror or exc}") from exc
        self._tasks = [asyncio.create_task(self._read()), asyncio.create_task(self._errors())]
        self.info = await self.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False},
                                   "terminal": False},
            "clientInfo": {"name": "factory", "title": "Factory", "version": __version__},
        })
        return self.info

    @property
    def can_load(self) -> bool:
        return bool(self.info.get("agentCapabilities", {}).get("loadSession"))

    async def request(self, method: str, params: dict) -> dict:
        if not self.proc or self.proc.returncode is not None:
            raise AgentError(self.why_stopped())
        self._next += 1
        future = asyncio.get_running_loop().create_future()
        self._waiting[self._next] = future
        await self._send({"jsonrpc": "2.0", "id": self._next, "method": method,
                          "params": params})
        return await future

    async def notify(self, method: str, params: dict) -> None:
        await self._send({"jsonrpc": "2.0", "method": method, "params": params})

    async def _send(self, message: dict) -> None:
        try:
            self.proc.stdin.write(json.dumps(message).encode() + b"\n")
            await self.proc.stdin.drain()
        except (ConnectionError, RuntimeError) as exc:
            raise AgentError(self.why_stopped()) from exc

    async def _read(self) -> None:
        while line := await self.proc.stdout.readline():
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if "method" in message:
                if "id" in message:
                    self._tasks.append(asyncio.create_task(self._answer(message)))
                elif message["method"] == "session/update":
                    self.on_update(message.get("params", {}).get("update", {}))
            elif (future := self._waiting.pop(message.get("id"), None)) and not future.done():
                if "error" in message:
                    err = message["error"]
                    future.set_exception(AgentError(err.get("message") or str(err)))
                else:
                    future.set_result(message.get("result") or {})
        await self.proc.wait()
        for future in self._waiting.values():
            if not future.done():
                future.set_exception(AgentError(self.why_stopped()))
        self._waiting.clear()

    async def _errors(self) -> None:
        while line := await self.proc.stderr.readline():
            self.stderr.append(line.decode(errors="replace").rstrip())

    async def _answer(self, message: dict) -> None:
        """Requests from the agent: permissions; the file system and terminals are not offered."""
        reply: dict = {"jsonrpc": "2.0", "id": message["id"]}
        if message["method"] == "session/request_permission":
            option = await self.on_permission(message.get("params", {}))
            reply["result"] = {"outcome": {"outcome": "selected", "optionId": option}
                               if option else {"outcome": "cancelled"}}
        else:
            reply["error"] = {"code": -32601, "message": f"{message['method']} not supported"}
        with contextlib.suppress(AgentError):
            await self._send(reply)

    def why_stopped(self) -> str:
        code = self.proc.returncode if self.proc else None
        tail = next((s for s in reversed(self.stderr) if s.strip()), "")
        return f"the agent stopped (exit {code})" + (f": {tail}" if tail else "")

    async def close(self) -> None:
        if self.proc and self.proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), 3)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    self.proc.kill()
        for task in self._tasks:
            task.cancel()
