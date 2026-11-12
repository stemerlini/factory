import asyncio
import sys
from pathlib import Path

from factory import acp, config, live

FAKE = Path(__file__).parent / "fake_acp.py"


def test_policy_refuses_the_users_calls_and_asks_outside_the_project(tmp_path):
    def decide(kind, command="", path=None, allowed=()):
        tool = {"kind": kind, "rawInput": {"command": command} if command else {},
                "locations": [{"path": str(path)}] if path else []}
        return acp.decide(tool, tmp_path, set(allowed))

    assert decide("execute", "git -C sub push origin main") == acp.DENY
    assert decide("execute", "factory lock fee", allowed=["factory lock"]) == acp.DENY
    assert decide("read", path="/etc/passwd") == acp.ALLOW
    assert decide("edit", path=tmp_path / "a.md") == acp.ALLOW
    assert decide("edit", path=tmp_path / "../elsewhere.md") == acp.ASK
    assert decide("execute", "pytest -q", allowed=["pytest"]) == acp.ALLOW
    assert acp.scope_of("ls; rm -rf ~") == "ls; rm -rf ~"
    assert acp.scope_of("git diff --stat") == "git diff"


def test_agent_session_follows_the_policy_and_resumes(make):
    p = make()
    config.save({"agents": {"fake": {"command": [sys.executable, str(FAKE)]}}})

    async def go():
        one = live.Live(p, "", "fake")
        await one.start()
        assert one.state == live.READY
        turn = asyncio.create_task(one.send("go"))
        while one.state != live.ASKING:
            await asyncio.sleep(0.01)
        assert one.asking.command == "pytest"
        one.answer("always")
        await turn
        said = [e.text for e in one.entries if e.kind == "agent"]
        assert said == ["Hello there", "reject-once allow-once allow-once allow-once"]
        assert [e.text for e in one.entries if e.kind == "refused"] == ["git commit -am done"]
        assert all(e.status == "completed" for e in one.entries if e.kind == "tool")
        await one.close()

        two = live.Live(p, "", "fake")
        await two.start()
        assert two.session_id == "s1" and two.resumed
        await two.close()

    asyncio.run(go())


OPTIONS = [{"optionId": "yes", "kind": "allow_once"}, {"optionId": "no", "kind": "reject_once"}]


def test_questions_asked_together_each_get_their_answer(make):
    p = make()

    async def go():
        one = live.Live(p, "", "fake")
        ask = [{"toolCall": {"toolCallId": f"t{n}", "kind": "execute",
                             "rawInput": {"command": cmd}}, "options": OPTIONS}
               for n, cmd in enumerate(["uname", "hostname"])]
        first, second = (asyncio.create_task(one.permission(a)) for a in ask)
        while len(one.questions) < 2:
            await asyncio.sleep(0)
        assert one.asking.command == "uname"
        one.answer("deny")
        assert await first == "no"
        assert one.state == live.ASKING and one.asking.command == "hostname"
        one.answer("allow")
        assert await second == "yes" and one.state != live.ASKING

    asyncio.run(go())
