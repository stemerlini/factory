from pathlib import Path

import pytest

from factory import project as project_mod

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Keep config and run store inside the test's temp dir."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setattr(project_mod, "DEFAULT_STORE", tmp_path / "store")
    for var in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        monkeypatch.setenv(var, "Test")
    for var in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(var, "test@example.com")


@pytest.fixture
def make(tmp_path):
    def make(template="particle-telescope", name="p"):
        """A project from a shipped template, or from tests/fixtures when one has that name."""
        fixture = FIXTURES / template
        p = project_mod.create(tmp_path / name, str(fixture) if fixture.is_dir() else template)
        manifest = p.root / "factory.toml"
        manifest.write_text(manifest.read_text() + f'\n[store]\nroot = "{tmp_path / "store"}"\n')
        return project_mod.Project.load(p.root)

    return make
