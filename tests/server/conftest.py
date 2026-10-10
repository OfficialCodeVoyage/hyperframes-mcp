"""Fixtures shared with the motion tests: the faked paid calls, and the
central spend log in the test's own folder. Also the fake HyperFrames app (``fake_app.py``) and a bridge to it."""

import pytest

from hyperframes_mcp.motion import hf_cli
from hyperframes_mcp.server.bridge import AppBridge
from tests.motion.conftest import (  # noqa: F401 -- fixtures
    central_spend_log,
    paid,
    profiles_root,
)
from tests.server.archive_helpers import archives  # noqa: F401 -- fixture
from tests.server.fake_app import FakeApp
from tests.server.fake_cli import FakeCli


@pytest.fixture(autouse=True)
def hyperframes(monkeypatch):
    """The HyperFrames CLI's timeline and history commands, faked in every test of the server: no node, and no real
    project history is ever written. ``calls`` holds the arguments of every call."""
    fake = FakeCli()
    monkeypatch.setattr(hf_cli, "run", fake)
    return fake


@pytest.fixture
def app_folder(tmp_path):
    """A project folder the fake app is open on: just a page."""
    folder = tmp_path / "hf-project"
    folder.mkdir()
    (folder / "index.html").write_text("<html>v1</html>", "utf-8")
    return folder


@pytest.fixture
def app(tmp_path, app_folder):
    """The fake app, open on ``app_folder`` as project ``demo``, discoverable in the test's own support folder."""
    return FakeApp(tmp_path / "app-support", {"demo": app_folder})


@pytest.fixture
def bridge(app):
    """A bridge that finds the fake app through its files and talks to it over its transport."""
    return AppBridge(app.support, transport=app.transport)
