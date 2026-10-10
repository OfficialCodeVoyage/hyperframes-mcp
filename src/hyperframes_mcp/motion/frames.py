"""Frames of a composed page for the agent to look at: which times may be asked, and a labelled contact sheet of a stretch.

The frames themselves come from the HyperFrames app's own preview (``preview.frame``, ``hyperframes_mcp/server/review_tools.py``):
no headless browser is run here. What goes to the agent is a JPEG no wider than ``MAX_WIDTH``, so a result stays small.
"""

from __future__ import annotations

import io
from collections.abc import Sequence
from pathlib import Path

from PIL import Image

from hyperframes_mcp.hfcli.cli import RenderFailed
from hyperframes_mcp.motion import render, sheet

MAX_WIDTH = 1000
JPEG_QUALITY = 80
MAX_SHEET_FRAMES = 16
SHEET_COLUMNS = 3
# the sheet is MAX_WIDTH wide: three tiles and four gaps of the library's sheet
_TILE_WIDTH = (MAX_WIDTH - 4 * 24) // SHEET_COLUMNS


class FrameError(ValueError):
    """The time or the stretch asked for is not in the video; the message says what is."""


def check_time(project: Path, at: float) -> None:
    """Refuse a time that is not inside the video, before the app is asked for its frame.

    Raises:
        FrameError: ``at`` is not inside the video.
    """
    _inside(project, [at])


def sheet_times(project: Path, from_s: float, to_s: float, n: int) -> list[float]:
    """The ``n`` times a contact sheet shows, spread evenly from ``from_s`` to ``to_s`` (both included).

    Raises:
        FrameError: ``n`` or the stretch is not one a sheet can show, or a time is not inside the video.
    """
    if not 1 <= n <= MAX_SHEET_FRAMES:
        raise FrameError(f"a contact sheet takes at most {MAX_SHEET_FRAMES} frames, not {n}")
    if n > 1 and to_s <= from_s:
        raise FrameError(f"to_s ({to_s:g}) must be after from_s ({from_s:g}) for {n} frames")
    times = [from_s + (to_s - from_s) * i / max(n - 1, 1) for i in range(n)]
    _inside(project, [times[0], times[-1]])
    return times


def contact_sheet(frames: Sequence[tuple[float, Path]], out_dir: Path) -> bytes:
    """A JPEG sheet of the ``(time, PNG)`` frames, each labelled with its time.

    Raises:
        sheet.SheetError: there are no frames, or one cannot be read.
    """
    labelled = [(f"{t:.2f} s", png) for t, png in frames]
    target = sheet.build_sheet(labelled, out_dir / "sheet.png", columns=SHEET_COLUMNS, tile_width=_TILE_WIDTH)
    return jpeg(target)


def jpeg(png: Path) -> bytes:
    """``png`` as a JPEG, scaled down to ``MAX_WIDTH`` wide when it is wider."""
    with Image.open(png) as opened:
        image = opened.convert("RGB")
    if image.width > MAX_WIDTH:
        height = round(image.height * MAX_WIDTH / image.width)
        image = image.resize((MAX_WIDTH, height), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=JPEG_QUALITY)
    return buffer.getvalue()


def _inside(project: Path, times: list[float]) -> None:
    """Refuse a time that is before the start or past the end of the video."""
    try:
        length = render.project_duration(project)
    except RenderFailed as error:
        raise FrameError(str(error)) from error
    for t in times:
        if t < 0:
            raise FrameError(f"{t:g} s is before the start of the video; times run from 0 to {length:g} s")
        if t > length:
            raise FrameError(f"{t:g} s is past the end: the video is {length:g} s long")
