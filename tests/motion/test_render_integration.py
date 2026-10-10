"""Opt-in: stage ``credit-line`` and render it with the real pinned HyperFrames and a real browser.

Skipped unless ``HYPERFRAMES_MCP_RENDER_INTEGRATION=1``. It needs `hyperframes-mcp setup` (the pinned HyperFrames and GSAP;
``HYPERFRAMES_MCP_NODE_DIR`` points at another install), a chrome-headless-shell
(``SCENE_RENDER_BROWSER`` or ``~/.cache/hyperframes/chrome``) and ffmpeg-full (``FFMPEG_BIN``, default the Homebrew
one), and makes no network call and no paid call. A render takes a few seconds:

    HYPERFRAMES_MCP_RENDER_INTEGRATION=1 pytest tests/motion/test_render_integration.py
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from PIL import Image

from hyperframes_mcp.hfcli.cli import NODE_DIR, RenderFailed
from hyperframes_mcp.motion import render, sheet, tokens
from hyperframes_mcp.motion.stage import stage_block

pytestmark = pytest.mark.skipif(
    os.environ.get("HYPERFRAMES_MCP_RENDER_INTEGRATION") != "1",
    reason="set HYPERFRAMES_MCP_RENDER_INTEGRATION=1 to render with the real HyperFrames",
)

NODE = NODE_DIR


def night() -> tuple[int, int, int]:
    value = tokens.default_tokens().colors["night"]
    return tuple(int(value[i : i + 2], 16) for i in (1, 3, 5))


def test_credit_line_renders_a_1080p_24fps_clip_that_fades_in_holds_and_fades_out(
    tmp_path,
):
    staged = stage_block("credit-line", None, tmp_path / "project")

    first = render.render_project(staged.project_dir, tmp_path / "a.mp4", node_dir=NODE)

    assert (first.width, first.height, first.fps) == (1920, 1080, 24)
    assert first.duration_s == pytest.approx(staged.total_s, abs=0.5)
    frames, _ = sheet.review_sheet(first.path, staged.duration_s)
    # the credit sits in the lower left of the safe zone: empty at 0 s, drawn once the fade-in has ended
    # and still drawn at the start of the fade-out
    corner = (96, 940, 1300, 1030)
    colours = [len(set(Image.open(frame).convert("RGB").crop(corner).getdata())) for frame in frames]
    assert colours[0] == 1
    # the ground is the night colour, give or take what H.264's YUV 4:2:0 rounds to
    centre = Image.open(frames[0]).convert("RGB").getpixel((960, 540))
    assert all(abs(got - want) <= 2 for got, want in zip(centre, night(), strict=True))
    assert colours[1] > 1 and colours[2] > 1 and colours[3] > 1

    second = render.render_project(staged.project_dir, tmp_path / "b.mp4", node_dir=NODE)
    assert second.sha256 == first.sha256


def lit_share(frame: Path, box: tuple[int, int, int, int]) -> float:
    """The share of pixels in ``box`` that are bright: the faint empty cells of a ``bar-grid`` are far darker than a lit bar."""
    pixels = list(Image.open(frame).convert("L").crop(box).getdata())
    return sum(1 for luma in pixels if luma > 120) / len(pixels)


def test_bar_grid_lights_its_grid_over_the_slot_and_two_renders_are_the_same(
    tmp_path,
):
    """The cold open (the block's defaults): 8198 cells, a canvas redrawn from the time by a modifier on a ticker."""
    staged = stage_block("bar-grid", None, tmp_path / "project")

    first = render.render_project(staged.project_dir, tmp_path / "a.mp4", node_dir=NODE)

    assert (first.width, first.height, first.fps) == (1920, 1080, 24)
    assert first.duration_s == pytest.approx(staged.total_s, abs=0.5)
    frames, _ = sheet.review_sheet(first.path, staged.duration_s)
    grid = (100, 230, 1810, 810)
    shares = [lit_share(frame, grid) for frame in frames]
    # nothing is lit at 0 s, a part is lit when the entrance ends (0.7 s: 12% of the cells have started), and the
    # whole grid is lit from the middle of the slot to the start of the fade out
    assert shares[0] == 0
    assert 0.02 < shares[1] < shares[2]
    assert shares[2] > 0.3 and shares[3] == pytest.approx(shares[2], abs=0.02)

    second = render.render_project(staged.project_dir, tmp_path / "b.mp4", node_dir=NODE)
    assert second.sha256 == first.sha256


def test_a_block_that_throws_fails_the_render_instead_of_writing_empty_frames(tmp_path):
    """credit-line throws by design when its slot is shorter than the fades plus the reading time.

    HyperFrames in its default best-effort mode waits 45 seconds, then writes a clip of empty frames and exits 0;
    the strict render must fail and carry the block's own reason (this takes about a minute).
    """
    text = "Bank of England Archive. " + "A long credit that needs time to read. " * 3
    staged = stage_block("credit-line", {"durationSeconds": 4, "text": text}, tmp_path / "project")

    with pytest.raises(
        RenderFailed,
        match=r"credit-line.*shorter than",
    ):
        render.render_project(staged.project_dir, tmp_path / "a.mp4", node_dir=NODE)

    assert not (tmp_path / "a.mp4").exists()
