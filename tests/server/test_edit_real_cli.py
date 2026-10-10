"""Opt-in: the edit tools on a small project composed from the real blocks, with the real pinned HyperFrames CLI and no
app (the disk route); no headless browser is run. It makes no network call and no paid call; the picture and the voice are
made here with Pillow and ffmpeg. Skipped unless ``HYPERFRAMES_MCP_REAL_CLI=1``:

    hyperframes-mcp setup   # once: installs the pinned HyperFrames CLI
    HYPERFRAMES_MCP_REAL_CLI=1 FFMPEG_BIN=/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg pytest tests/server/test_edit_real_cli.py

``HYPERFRAMES_MCP_NODE_DIR`` can point at another folder with ``node_modules``. The CLI's
``history`` writes a log outside the project, so ``HOME`` is a folder of the test's own for those calls.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest import mock

import pytest
from mcp import Client
from PIL import Image, ImageDraw

from hyperframes_mcp.hfcli.cli import NODE_DIR
from hyperframes_mcp.motion import hf_cli, manifest
from hyperframes_mcp.motion.compose import compose
from hyperframes_mcp.motion.shotlist import parse_shotlist
from hyperframes_mcp.server.bridge import AppBridge
from hyperframes_mcp.server.server import build_server

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.environ.get("HYPERFRAMES_MCP_REAL_CLI") != "1",
        reason="set HYPERFRAMES_MCP_REAL_CLI=1 to run the edit tools on the real HyperFrames CLI",
    ),
]

NODE = NODE_DIR
CREDIT = "US Army Signal Corps, NARA 111-SC-204515"
KINDS = ["act-title", "lower-third", "photo-shot", "credit-line"]


@pytest.fixture(autouse=True)
def hyperframes(monkeypatch, tmp_path):
    """The real CLI from ``NODE``, with a folder of the test's own as ``HOME`` for its history commands."""
    real = hf_cli.run
    home = tmp_path / "home"
    home.mkdir()

    def run(project, node_dir, *argv, **kwargs):
        with mock.patch.dict(os.environ, {"HOME": str(home)} if argv[0] == "history" else {}):
            return real(project, NODE, *argv, **kwargs)

    monkeypatch.setattr(hf_cli, "run", run)


@pytest.fixture
def project(tmp_path, monkeypatch) -> Path:
    """A 22 s video: an act title with a lower third, then two photographs, each with its credit."""
    assets = tmp_path / "assets"
    (assets / "photos").mkdir(parents=True)
    (assets / "audio").mkdir()
    for name, ground in (("mine.jpg", (200, 60, 40)), ("other.jpg", (40, 90, 200))):
        image = Image.new("RGB", (1920, 1080), ground)
        draw = ImageDraw.Draw(image)
        for x in range(0, 1920, 120):
            draw.line([(x, 0), (x + 400, 1080)], fill=(240, 220, 160), width=30)
        image.save(assets / "photos" / name)
    ffmpeg = os.environ.get("FFMPEG_BIN", "ffmpeg")
    subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=stereo:d=24",
            "-ac",
            "2",
            str(assets / "audio/voice.wav"),
        ],
        check=True,
    )

    def photo(name):
        variables = {
            "image": f"photos/{name}",
            "from": "[0, 0, 1, 1]",
            "to": "[0.12, 0.1, 0.8, 0.8]",
            "scrim": "none",
            "grade": '{"exposure":0}',
        }
        return {"component": "photo-shot", "at": 0, "variables": variables}

    credit = {"component": "credit-line", "at": 0.3, "variables": {"text": CREDIT}}
    raw = {
        "version": 1,
        "meta": {"title": "Scratch video", "language": "en"},
        "audio": {"voice": {"path": "audio/voice.wav"}},
        "shots": [
            {
                "id": "s01", "start": 0, "duration": 6,
                "layers": [
                    {"component": "act-title", "at": 0, "variables": {"kicker": "Part 1", "title": "A Test Title", "subtitle": ""}},
                    {"component": "lower-third", "at": 0.3, "variables": {"name": "Jay Cooke", "role": "Financier", "side": "left"}},
                ],
            },
            {"id": "p1", "start": 6, "duration": 8, "layers": [photo("mine.jpg"), credit]},
            {"id": "p2", "start": 14, "duration": 8, "layers": [photo("other.jpg"), credit]},
        ],
    }  # fmt: skip
    root = tmp_path / "project"
    compose(parse_shotlist(raw), [], 22.0, root, assets_dir=assets)
    manifest.write_manifest(root, manifest.ProjectManifest(profile="history"))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    return root


async def call(name: str, arguments: dict | None = None):
    async with Client(build_server(AppBridge(Path("/nonexistent/hyperframes-app-support")))) as client:
        return await client.call_tool(name, arguments or {})


def layers(result) -> dict[str, tuple[float, float]]:
    return {row["id"]: (row["start"], row["end"]) for row in result.structured_content["layers"]}


async def test_a_move_and_a_trim_are_checked_undone_and_leave_the_page_as_it_was(
    project,
):
    original = (project / "index.html").read_bytes()
    ops = [
        {"verb": "move", "layer": "l-s01-2", "time": 0.5},
        {"verb": "trim", "layer": "l-p1-2", "end": 12},
    ]

    planned = await call("edit_timeline", {"ops": ops})
    assert (project / "index.html").read_bytes() == original  # a dry run writes nothing
    applied = await call("edit_timeline", {"ops": ops, "dry_run": False})

    assert not applied.is_error and applied.structured_content["route"] == "disk"
    assert [(c["layer"], c["now"]) for c in planned.structured_content["changes"]] == [
        (c["layer"], c["now"]) for c in applied.structured_content["changes"]
    ]
    table = await call("layer_table", {"kinds": KINDS})
    assert layers(table)["l-s01-2"] == (0.5, 6.2) and layers(table)["l-p1-2"] == (
        6.3,
        12.0,
    )
    checked = await call("run_checks")
    assert checked.structured_content["ok"] is True, checked.structured_content["findings"]

    undone = await call("undo")

    assert undone.structured_content["undone"] == ["Timeline: move l-s01-2, trim l-p1-2"]
    assert (project / "index.html").read_bytes() == original


async def test_a_split_layer_is_named_and_drawn_like_any_other_and_goes_back_with_undo(
    project,
):
    original = (project / "index.html").read_bytes()

    result = await call(
        "edit_timeline",
        {"ops": [{"verb": "split", "layer": "l-p1-1", "time": 10}], "dry_run": False},
    )

    assert not result.is_error
    table = await call("layer_table", {"kinds": KINDS})
    assert layers(table)["l-p1-1"] == (6.0, 10.0) and layers(table)["l-p1-3"] == (
        10.0,
        14.0,
    )
    assert "-split" not in (project / "index.html").read_text(encoding="utf-8")
    checked = await call("run_checks")
    assert checked.structured_content["ok"] is True, checked.structured_content["findings"]

    await call("undo")

    assert (project / "index.html").read_bytes() == original
