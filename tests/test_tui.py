import sys
from pathlib import Path

from textual.widgets import DataTable, Tree

from factory import config, runs
from factory.tui.app import FactoryApp


def use_fake_agent():
    config.save({"agent": {"default": "fake"}, "agents": {"fake": {
        "command": [sys.executable, str(Path(__file__).parent / "fake_acp.py")]}}})


async def test_tui_elements_tabs_and_new_study(make):
    p = make()
    runs.execute(p, p.study("S001"))
    app = FactoryApp(p.root)
    async with app.run_test(size=(150, 44)) as pilot:
        await pilot.pause()
        assert app.query_one("#req-table", DataTable).row_count == 6
        assert app.query_one("#study-table", DataTable).row_count == 2
        assert app.query_one("#run-table", DataTable).row_count == 1
        assert app.query_one("#block-table", DataTable).row_count == 3
        await pilot.press("m")
        await pilot.pause()
        assert app.screen.query_one("#module-table", DataTable).row_count > 0
        await pilot.press("escape")
        app.query_one("#elements", Tree).focus()
        await pilot.press("down")  # Front-end electronics
        await pilot.pause()
        assert app.element_path == "fee"
        assert app.query_one("#req-table", DataTable).row_count == 1
        await pilot.press("5", "n")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "NewStudyScreen"
        await pilot.press(*"Noise", "enter")
        await pilot.pause()
        assert p.study("fee/S002").task in ("kicad.erc", "ngspice.run", "script")
        assert app.query_one("#study-table", DataTable).row_count == 2


async def test_new_project_in_a_chosen_location(tmp_path, monkeypatch):
    from factory import portal

    elsewhere = tmp_path / "work"
    elsewhere.mkdir()
    monkeypatch.setattr(portal, "choose_folder", lambda title, start: elsewhere)
    use_fake_agent()
    app = FactoryApp(None)
    async with app.run_test(size=(150, 44)) as pilot:
        await pilot.pause()
        await pilot.press("end", "enter")  # "New project from a template…"
        await pilot.pause()
        await pilot.press(*"Demo", "ctrl+b")
        for _ in range(50):
            await pilot.pause(0.05)
            if app.screen.query_one("#location").value == str(elsewhere):
                break
        app.screen.query_one("#idea").text = "A camera for cubesats"
        await pilot.press("enter")
        await pilot.pause()
        assert app.project and app.project.root == elsewhere / "demo"
        assert app.screen.__class__.__name__ != "NewProjectScreen"
        assert "A camera for cubesats" in (app.project.root / "element.md").read_text()
        for _ in range(100):
            await pilot.pause(0.02)
            if app.lives[""].entries:
                break
        brief = app.lives[""].entries[0].text
        assert "A camera for cubesats" in brief and "# Kickoff" in brief
        assert not (app.project.root / ".factory/kickoff").exists()


async def test_home_opens_what_waits(make):
    from factory import decisions

    p = make()
    decisions.ask(p, p.element("fee"), "ADC architecture", ["SAR", "Sigma-delta"], "A")
    app = FactoryApp(p.root)
    async with app.run_test(size=(150, 44)) as pilot:
        await pilot.pause()
        waiting = app.query_one("#waiting-table", DataTable)
        assert app._tab() == "home"
        waiting.focus()
        row = next(i for i, item in enumerate(app.home.waiting) if item.kind == "decision")
        waiting.move_cursor(row=row)
        await pilot.press("enter")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "DecideScreen"
        assert app.element_path == "fee"


async def test_gate_items_do_themselves(make, monkeypatch):
    p = make()
    app = FactoryApp(p.root)
    opened = []
    monkeypatch.setattr(app, "edit_file", opened.append)
    async with app.run_test(size=(150, 44)) as pilot:
        await pilot.pause()
        await pilot.press("2")
        table = app.query_one("#check-table", DataTable)
        table.focus()
        table.move_cursor(row=table.get_row_index("check:concept:0"))
        await pilot.press("space")
        await pilot.pause()
        assert opened == [p.root / "concept/concept.md"]
        assert "## Options" in opened[0].read_text()


async def test_agent_pane_asks_and_remembers(make):
    from factory import live
    from factory.tui.agentpane import AgentPane, Question

    use_fake_agent()
    p = make()
    app = FactoryApp(p.root)

    async def until(state):
        for _ in range(200):
            await pilot.pause(0.02)
            if app.lives[""].state == state:
                return

    async with app.run_test(size=(150, 44)) as pilot:
        await pilot.pause()
        await pilot.press("a")
        await until(live.ASKING)
        assert app.query_one("#drawer", AgentPane).display
        assert isinstance(app.focused, Question)
        await pilot.press("s")  # always allow pytest this session
        await until(live.READY)
        await pilot.press(*"go on", "enter")
        await pilot.pause(0.3)
        await until(live.READY)
        said = [e.text for e in app.lives[""].entries if e.kind == "agent"]
        assert said[-1] == said[-3] == "reject-once allow-once allow-once allow-once"


def test_editor_command_splits_flags(monkeypatch, tmp_path):
    from factory.tui.app import editor_command

    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setenv("EDITOR", "omarchy-launch-editor --inline")
    assert editor_command(tmp_path / "a b.md") == [
        "omarchy-launch-editor", "--inline", str(tmp_path / "a b.md")]


async def test_review_tab_decide_approve_reject(make):
    import subprocess

    from factory import decisions, review

    p = make()
    subprocess.run(["git", "-C", str(p.root), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(p.root), "commit", "-qm", "base"], check=True)
    decisions.ask(p, p.tree(), "Orbit", ["LEO: 550 km", "GEO"], "A", "Drives the spectra.")
    (p.root / "concept/sketch.md").write_text("# idea\n")
    (p.root / "scratch.txt").write_text("junk\n")
    app = FactoryApp(p.root)
    async with app.run_test(size=(150, 44)) as pilot:
        await pilot.pause()
        await pilot.press("8")
        await pilot.pause()
        assert app.query_one("#tabs").get_tab("review").label.plain == "Review (4)"
        app.query_one("#decision-table").focus()
        await pilot.press("enter")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "DecideScreen"
        await pilot.click("#accept")
        await pilot.pause()
        assert decisions.get(p, "D001").chosen == "A"

        app.query_one("#change-table").focus()
        table = app.query_one("#change-table")
        table.move_cursor(row=[c.path for c in app.pending].index("scratch.txt"))
        await pilot.press("x")
        await pilot.pause()
        await pilot.press("y")  # confirm the discard
        await pilot.pause()
        assert not (p.root / "scratch.txt").exists()

        await pilot.press("y")  # approve
        await pilot.pause()
        assert app.screen.__class__.__name__ == "ApproveScreen"
        app.screen.query_one("#message").value = "Orbit decided, first sketch"
        await pilot.press("enter")
        await pilot.pause()
        assert review.changes(p) == []
        assert p.git("log", "-1", "--format=%s") == "Orbit decided, first sketch"


async def test_agent_dialog_continues_last_session(make, monkeypatch):
    from factory import agent
    from factory.tui.screens import AgentScreen

    p = make()
    folder = agent.session_dir(p)
    folder.mkdir(parents=True)
    (folder / "abc.jsonl").write_text('{"type": "last-prompt", "lastPrompt": "size the ADC"}\n')
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    app = FactoryApp(p.root)
    launched = []
    monkeypatch.setattr(app, "launch", launched.append)
    async with app.run_test(size=(150, 44)) as pilot:
        await pilot.pause()
        await pilot.press("A")
        await pilot.pause()
        assert isinstance(app.screen, AgentScreen)
        await pilot.click("#continue")
        await pilot.pause()
    assert launched and launched[0][-3:] == ["claude", "--resume", "abc"]
    assert str(p.root) in launched[0]
