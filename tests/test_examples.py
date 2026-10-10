"""The shipped example composes: its shot list is valid against the real blocks and the project is written."""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

from hyperframes_mcp.motion import __main__ as cli
from hyperframes_mcp.motion import manifest

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "hello"


def test_the_hello_example_composes(tmp_path):
    spec = importlib.util.spec_from_file_location("make_assets", EXAMPLE / "make_assets.py")
    make_assets = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(make_assets)
    for name in ("shotlist.json", "words.json"):
        shutil.copy(EXAMPLE / name, tmp_path / name)
    make_assets.write_voice(tmp_path / "assets")
    argv = ["compose", str(tmp_path / "shotlist.json"), "--alignment", str(tmp_path / "words.json")]

    assert cli.main([*argv, "--assets", str(tmp_path / "assets"), "--check"]) == 0
    assert cli.main([*argv, "--assets", str(tmp_path / "assets"), "--out", str(tmp_path / "project")]) == 0

    saved = manifest.read_manifest(tmp_path / "project")
    assert saved.title == "Hello, HyperFrames" and saved.profile == "default"
    assert (tmp_path / "project" / "index.html").is_file()
