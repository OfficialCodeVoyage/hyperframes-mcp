"""Frames of a composed page for the agent to look at: the times a frame may be asked at, a JPEG made small, and a labelled
contact sheet of a stretch. The frames come from the app's preview (``tests/server/test_review_tools.py`` has it
faked); here they are noisy full-size PNGs, as the preview's are."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from hyperframes_mcp.motion import frames
from tests.motion.test_project_edit import composed, two_shots


@pytest.fixture
def project(tmp_path) -> Path:
    """A composed project 20 s long."""
    return composed(tmp_path, two_shots())[0]


def noisy(path: Path, size=(1920, 1080)) -> Path:
    Image.effect_noise(size, 60).convert("RGB").save(path)
    return path


def opened(data: bytes) -> Image.Image:
    return Image.open(BytesIO(data))


def test_a_frame_is_a_jpeg_no_wider_than_the_limit(tmp_path):
    png = noisy(tmp_path / "frame.png")

    data = frames.jpeg(png)

    assert png.stat().st_size > 1_000_000
    assert opened(data).format == "JPEG"
    assert opened(data).width == frames.MAX_WIDTH and opened(data).height == 562
    assert len(data) < 400_000


def test_a_small_frame_is_not_enlarged(tmp_path):
    path = tmp_path / "small.png"
    Image.new("RGB", (640, 360)).save(path)

    assert opened(frames.jpeg(path)).size == (640, 360)


def test_a_time_past_the_end_of_the_video_is_refused_with_its_length(project):
    with pytest.raises(frames.FrameError, match=r"20 s long"):
        frames.check_time(project, 20.5)
    with pytest.raises(frames.FrameError, match="before the start"):
        frames.check_time(project, -1)
    frames.check_time(project, 20)


def test_the_sheet_times_run_evenly_from_end_to_end(project):
    times = frames.sheet_times(project, 2, 13, 12)

    assert times == pytest.approx([2 + n for n in range(12)])
    assert frames.sheet_times(project, 5, 6, 1) == [5]


def test_the_limits_of_a_sheet(project):
    with pytest.raises(frames.FrameError, match="at most 16"):
        frames.sheet_times(project, 0, 10, 17)
    with pytest.raises(frames.FrameError, match="after from_s"):
        frames.sheet_times(project, 5, 5, 4)
    with pytest.raises(frames.FrameError, match="20 s long"):
        frames.sheet_times(project, 10, 30, 4)


def test_the_sheet_is_one_labelled_jpeg_of_the_frames_given(project, tmp_path):
    times = frames.sheet_times(project, 2, 13, 12)
    pngs = [noisy(tmp_path / f"f{n}.png") for n in range(12)]

    data = frames.contact_sheet(list(zip(times, pngs, strict=True)), tmp_path)

    sheet = opened(data)
    assert sheet.format == "JPEG" and sheet.width <= frames.MAX_WIDTH
    assert len(data) < 500_000
    # the time of each frame is written under it, in every tile's row
    assert sheet.height > 4 * 100
