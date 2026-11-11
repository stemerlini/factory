from textual.widgets import DataTable, Tree

from factory import runs
from factory.tui.app import FactoryApp


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
        app.query_one("#elements", Tree).focus()
        await pilot.press("down")  # Front-end electronics
        await pilot.pause()
        assert app.element_path == "fee"
        assert app.query_one("#req-table", DataTable).row_count == 1
        await pilot.press("4", "n")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "NewStudyScreen"
        await pilot.press(*"Noise", "enter")
        await pilot.pause()
        assert p.study("fee/S002").task in ("kicad.erc", "ngspice.run", "script")
        assert app.query_one("#study-table", DataTable).row_count == 2


async def test_new_project_from_picker_opens_it(tmp_path):
    (tmp_path / "config/factory").mkdir(parents=True)
    (tmp_path / "config/factory/config.toml").write_text(
        f'projects_root = "{tmp_path / "projects"}"\n')
    app = FactoryApp(None)
    async with app.run_test(size=(150, 44)) as pilot:
        await pilot.pause()
        await pilot.press("end", "enter")  # "New project from a template…"
        await pilot.pause()
        await pilot.click("#name")
        await pilot.press(*"Demo", "enter")
        await pilot.pause()
        assert app.project and app.project.root == tmp_path / "projects/demo"
        assert app.screen.__class__.__name__ != "NewProjectScreen"


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
        await pilot.press("a")
        await pilot.pause()
        assert isinstance(app.screen, AgentScreen)
        await pilot.click("#continue")
        await pilot.pause()
    assert launched and launched[0][-3:] == ["claude", "--resume", "abc"]
    assert str(p.root) in launched[0]
