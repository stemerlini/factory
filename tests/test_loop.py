"""The human in the loop: decisions, review (approve / reject) and the agent brief."""

import json
import os
import subprocess

import pytest

from factory import agent, decisions, review
from factory.cli import main
from factory.project import ProjectError


def commit_all(p):
    subprocess.run(["git", "-C", str(p.root), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(p.root), "commit", "-qm", "base"], check=True)


def test_new_projects_carry_guardrails(make):
    p = make()
    settings = json.loads((p.root / ".claude/settings.json").read_text())
    deny = settings["permissions"]["deny"]
    assert "Bash(git commit *)" in deny and "Bash(factory decide *)" in deny
    assert "factory" in (p.root / "CLAUDE.md").read_text()
    (p.root / "CLAUDE.md").unlink()
    assert agent.ensure_files(p) == ["CLAUDE.md"]


def test_ask_and_decide(make):
    p = make()
    fee = p.element("fee")
    d = decisions.ask(p, fee, "Shaping time", ["0.5 us: faster", "2 us: quieter"], "B",
                      "Noise vs pile-up.")
    assert (d.key, d.status, [o.id for o in d.options]) == ("fee/D001", "proposed", ["A", "B"])
    assert decisions.get(p, "fee/D001").options[1].summary == "quieter"
    decided = decisions.decide(p, "fee/D001", "accept", note="Rates are low in LEO.")
    assert (decided.status, decided.chosen) == ("accepted", "B")
    again = decisions.get(p, "fee/D001")
    assert again.chosen == "B" and "Rates are low" in again.verdict
    assert again.context == "Noise vs pile-up."
    decisions.ask(p, fee, "Preamp", ["a", "b"])
    with pytest.raises(ProjectError, match="choose one of"):
        decisions.decide(p, "fee/D002", "accept", option="Z")
    with pytest.raises(ProjectError, match="two options"):
        decisions.ask(p, fee, "Only one", ["a"])


def test_review_approve_and_reject(make):
    p = make()
    commit_all(p)
    assert review.changes(p) == []
    req = p.root / "elements/fee/req/EL-001.md"
    req.write_text(req.read_text().replace("0.3", "0.5"))
    (p.root / "elements/fee/notes.md").write_text("new\n")
    (p.root / "req/SYS-001.md").write_text("---\nid: SYS-001\ntitle: changed\n---\n")
    (p.root / ".factory").mkdir(exist_ok=True)
    (p.root / ".factory/proposal.md").write_text("# Raise the gain margin\n\nBecause.\n")
    found = {c.path: (c.kind, c.element) for c in review.changes(p)}
    assert found == {
        "elements/fee/req/EL-001.md": ("modified", "fee"),
        "elements/fee/notes.md": ("new", "fee"),
        "req/SYS-001.md": ("modified", ""),
    }
    assert review.title(p) == "Raise the gain margin"
    gain = next(c for c in review.changes(p) if c.path.endswith("EL-001.md"))
    assert "+criterion: {metric: gain, op: \">=\", value: 0.5" in review.diff(p, gain)

    review.reject(p, "req/SYS-001.md")
    review.reject(p, "elements/fee/notes.md")
    assert [c.path for c in review.changes(p)] == ["elements/fee/req/EL-001.md"]
    review.approve(p, ["elements/fee/req/EL-001.md"], review.title(p))
    assert review.changes(p) == []
    assert p.git("log", "-1", "--format=%s") == "Raise the gain margin"
    assert not (p.root / ".factory/proposal.md").exists()  # archived once all is approved
    assert list((p.root / ".factory/proposals").glob("*.md"))


def test_cli_review_flow(make, capsys):
    p = make("blank")
    commit_all(p)
    main(["ask", "Orbit", "-o", "LEO", "-o", "GEO", "-r", "A", "-C", str(p.root)])
    capsys.readouterr()
    main(["review", "-C", str(p.root), "--json"])
    data = json.loads(capsys.readouterr().out)
    assert data["open_decisions"] == ["D001"]
    assert [c["path"] for c in data["changes"]][0].startswith("decisions/D001")
    assert main(["decide", "D001", "accept", "-C", str(p.root)]) == 0
    assert main(["approve", "-m", "Pick the orbit", "-C", str(p.root)]) == 0
    capsys.readouterr()
    main(["decisions", "--open", "-C", str(p.root)])
    assert "No decisions" in capsys.readouterr().out


def test_agent_brief_and_window(make):
    p = make()
    decisions.ask(p, p.tree(), "Orbit", ["LEO", "GEO"])
    text = agent.brief(p, p.element("sensor-head"))
    assert "Sensor head (sensor-head)" in text and "next gate:" in text
    assert "D001" in text and "Wait for my go-ahead" in text
    argv = agent.window_command(p, p.element("fee"), "Size the CSA")
    assert argv[0] in ("omarchy-launch-tui", "xdg-terminal-exec")
    assert "claude" in argv and argv[-1].endswith("Size the CSA")
    assert str(p.root) in argv


def fake_session(p, sid, prompt, mtime):
    """A Claude Code transcript as it lands in ~/.claude/projects/<folder>."""
    folder = agent.session_dir(p)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{sid}.jsonl"
    path.write_text(json.dumps({"type": "user", "message": "hi"}) + "\n"
                    + json.dumps({"type": "last-prompt", "lastPrompt": prompt}) + "\n")
    os.utime(path, (mtime, mtime))
    return path


def test_agent_sessions_continue_and_handover(make, capsys):
    p = make()
    assert agent.sessions(p) == [] and agent.running(p) == []
    assert main(["agent", "--continue", "-C", str(p.root)]) != 0
    fake_session(p, "old", "first", 1_000)
    fake_session(p, "new", "size the\nADC", 2_000)
    found = agent.sessions(p)
    assert [s.id for s in found] == ["new", "old"] and found[0].prompt == "size the ADC"
    assert agent.resume_command(found[0]) == ["claude", "--resume", "new"]
    assert agent.resume_command(found[0], fork=True)[-1] == "--fork-session"
    capsys.readouterr()
    main(["agent", "--sessions", "--json", "-C", str(p.root)])
    data = json.loads(capsys.readouterr().out)
    assert data["running"] == [] and data["sessions"][0]["prompt"] == "size the ADC"
    assert "handover" not in agent.brief(p, p.tree())
    (p.root / review.PROPOSAL).parent.mkdir(exist_ok=True)
    (p.root / review.PROPOSAL).write_text("Mass budget\n\n## Where I stopped\n")
    assert ".factory/proposal.md: read it first" in agent.brief(p, p.tree())
