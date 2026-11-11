import json
from pathlib import Path

from factory.cli import main


def test_new_status_run_req(tmp_path, capsys):
    root = tmp_path / "thr"
    fixture = Path(__file__).parent / "fixtures/plasma-thruster"
    assert main(["new", str(root), "-t", str(fixture)]) == 0
    manifest = root / "factory.toml"
    manifest.write_text(manifest.read_text() + f'\n[store]\nroot = "{tmp_path / "store"}"\n')
    capsys.readouterr()

    assert main(["run", "S001", "-w", "-C", str(root), "--json"]) == 0
    run = json.loads(capsys.readouterr().out)
    assert run["status"] == "done" and run["metrics"]["Isp"]["unit"] == "s"

    main(["req", "-C", str(root), "--json"])
    reqs = {r["id"]: r for r in json.loads(capsys.readouterr().out)}
    assert reqs["SYS-001"]["status"] == "pass"

    main(["status", "-C", str(root), "--json"])
    status = json.loads(capsys.readouterr().out)
    assert [e["path"] for e in status["elements"]] == ["", "feed", "head", "ppu"]
    assert status["elements"][0]["gates"]["describe"] == "done"

    main(["arch", "-C", str(root)])
    assert "Thruster head" in capsys.readouterr().out

    main(["projects", "--json"])
    assert str(root) in json.loads(capsys.readouterr().out)


def test_outside_project_is_an_error(tmp_path, capsys):
    assert main(["status", "-C", str(tmp_path)]) == 2
    assert "not inside a Factory project" in capsys.readouterr().err


def test_check_finds_problems(make, capsys):
    p = make()
    assert main(["check", "-C", str(p.root)]) == 0
    (p.root / "studies/S003-x").mkdir()
    (p.root / "studies/S003-x/study.toml").write_text(
        'task = "warp.drive"\nverifies = ["SYS-99"]\n')
    (p.root / "req/SYS-007.md").write_text("---\nid: SYS-007\nparent: SYS-42\n---\n")
    capsys.readouterr()
    assert main(["check", "-C", str(p.root), "--json"]) == 1
    found = "\n".join(json.loads(capsys.readouterr().out)["problems"])
    assert "unknown module 'warp'" in found
    assert "unknown parent SYS-42" in found
    assert "not allocated" not in found  # allocation is a gate status, not a problem


def test_tasks_lists_params(capsys):
    main(["tasks", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert any(t["task"] == "ngspice.run" for t in data["tasks"])
    assert {"electronics", "software"} <= {k["kind"] for k in data["kinds"]}
