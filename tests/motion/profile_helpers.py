"""A home folder of the test's own with profile folders in it, for the tests that show each profile gets its own
settings: ``HYPERFRAMES_MCP_HOME`` points at it, so ``config/profiles/<name>/`` is read from there."""

from __future__ import annotations

from pathlib import Path

import pytest

from hyperframes_mcp.util.paths import HOME_ENV


class ProfilesRoot:
    """The temporary home; ``write`` puts a file into one profile's folder."""

    def __init__(self, home: Path) -> None:
        self.home = home

    def write(self, name: str, file: str, text: str) -> Path:
        path = self.home / "config" / "profiles" / name / file
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path


@pytest.fixture
def profiles_root(tmp_path, monkeypatch) -> ProfilesRoot:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv(HOME_ENV, str(home))
    return ProfilesRoot(home)
