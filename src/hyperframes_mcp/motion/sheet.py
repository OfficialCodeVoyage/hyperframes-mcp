"""Look at a render: frames at chosen times and a labelled contact sheet.

A rendered block cannot be reviewed from its code (layout, spacing and the moment a fade ends only show in
pixels), so the review is a few frames side by side. ``extract_frames`` pulls PNGs out of an MP4 with ffmpeg and
``build_sheet`` lays them out with a label under each, in the library's own label face and colours.

ffmpeg is ``$FFMPEG_BIN``, else the ffmpeg-full of the project's Homebrew install, else ``ffmpeg`` on PATH.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Sequence
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from hyperframes_mcp.motion import tokens
from hyperframes_mcp.qa.frames import UnsafeVideoPath, checked_video

DEFAULT_FFMPEG = "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg"
_FRAME_TIMEOUT_S = 60
_GAP = 24
_LABEL_HEIGHT = 64
_LABEL_SIZE = 32


class SheetError(RuntimeError):
    """A frame could not be extracted or the sheet could not be built; the message says which."""


def ffmpeg_binary() -> str:
    """``$FFMPEG_BIN``, else the project's ffmpeg-full when it is installed, else ``ffmpeg``."""
    configured = os.environ.get("FFMPEG_BIN")
    if configured:
        return configured
    return DEFAULT_FFMPEG if Path(DEFAULT_FFMPEG).is_file() else "ffmpeg"


def media_environment() -> dict[str, str]:
    """The process environment with ``FFMPEG_BIN`` settled by :func:`ffmpeg_binary`, for ``resolve_tools``."""
    return {**os.environ, "FFMPEG_BIN": ffmpeg_binary()}


def extract_frames(
    video: Path,
    times: Sequence[float],
    out_dir: Path,
    *,
    ffmpeg: str | None = None,
) -> list[Path]:
    """One PNG per time (seconds into ``video``) in ``out_dir``, in the order given.

    Only a plain local video file is ever handed to ffmpeg (``checked_video``, as the visual QA does).

    Raises:
        SheetError: ``video`` is not a plain video file, ffmpeg is missing, fails, times out, or writes no
            frame (a time past the end of the video).
    """
    binary = ffmpeg or ffmpeg_binary()
    try:
        source = checked_video(video)
    except UnsafeVideoPath as error:
        raise SheetError(str(error)) from error
    out_dir.mkdir(parents=True, exist_ok=True)
    frames: list[Path] = []
    for index, at in enumerate(times):
        target = out_dir / f"{source.stem}-frame-{index:02d}-{at:.2f}s.png"
        command = [
            binary,
            "-v",
            "error",
            "-y",
            "-ss",
            f"{at:.3f}",
            "-protocol_whitelist",
            "file",
            "-i",
            str(source),
            "-frames:v",
            "1",
            str(target),
        ]
        try:
            done = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=_FRAME_TIMEOUT_S,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise SheetError(f"ffmpeg did not run for the frame at {at:.2f} s: {error}") from error
        if done.returncode != 0 or not target.is_file():
            reason = " ".join(done.stderr.split())[-300:] or "no frame was written"
            raise SheetError(f"no frame at {at:.2f} s of {source.name}: {reason}")
        frames.append(target)
    return frames


def review_times(duration_s: float, start: float = 0.0) -> list[tuple[str, float]]:
    """The moments of a block worth a frame, as ``(label, seconds into the video)``.

    The start (nothing drawn yet), the end of the fade-in, the middle, and the start of the fade-out: the enter
    and exit durations are the tokens', which every block fades with.
    """
    duration = tokens.default_tokens().duration
    return [
        ("start", start),
        ("fade-in end", start + duration["enter"]),
        ("50%", start + duration_s / 2),
        ("fade-out start", start + duration_s - duration["exit"]),
    ]


def _label_font() -> ImageFont.FreeTypeFont:
    """The library's label face, at medium weight."""
    faces = tokens.default_tokens().fonts["label"].files
    face = next((f for f in faces if f.weight == "500"), faces[0])
    return ImageFont.truetype(str(tokens.FONTS_DIR / face.file), _LABEL_SIZE)


def build_sheet(
    frames: Sequence[tuple[str, Path]],
    target: Path,
    *,
    columns: int = 2,
    tile_width: int = 960,
) -> Path:
    """Lay ``(label, png)`` pairs out in a grid, each with its label beneath, and write the PNG ``target``.

    Raises:
        SheetError: there are no frames, or a frame cannot be read.
    """
    if not frames:
        raise SheetError("a contact sheet needs at least one frame")
    colors = tokens.default_tokens().colors
    font = _label_font()
    tiles: list[tuple[str, Image.Image]] = []
    for label, path in frames:
        try:
            with Image.open(path) as opened:
                image = opened.convert("RGB")
        except OSError as error:
            raise SheetError(f"cannot read the frame {path.name}: {error}") from error
        height = round(image.height * tile_width / image.width)
        tiles.append((label, image.resize((tile_width, height), Image.Resampling.LANCZOS)))

    tile_height = max(image.height for _, image in tiles)
    columns = max(1, min(columns, len(tiles)))
    rows = -(-len(tiles) // columns)
    cell_height = tile_height + _LABEL_HEIGHT
    sheet = Image.new(
        "RGB",
        (
            _GAP + columns * (tile_width + _GAP),
            _GAP + rows * (cell_height + _GAP),
        ),
        colors["scrim"],
    )
    draw = ImageDraw.Draw(sheet)
    for index, (label, image) in enumerate(tiles):
        left = _GAP + (index % columns) * (tile_width + _GAP)
        top = _GAP + (index // columns) * (cell_height + _GAP)
        sheet.paste(image, (left, top))
        draw.rectangle(
            (left - 1, top - 1, left + tile_width, top + image.height),
            outline=colors["paper_aged"],
            width=1,
        )
        draw.text(
            (left, top + image.height + _LABEL_HEIGHT // 2),
            label,
            font=font,
            fill=colors["paper"],
            anchor="lm",
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(target, format="PNG")
    return target


def review_sheet(video: Path, duration_s: float, *, start: float = 0.0) -> tuple[list[Path], Path]:
    """The review of a rendered block: its four moments as PNGs and a contact sheet, beside ``video``.

    The frames go to ``<video>.frames/`` and the sheet to ``<video>.sheet.png`` (the video's suffix replaced).
    Returns ``(frames, sheet)``.

    Raises:
        SheetError: see ``extract_frames`` and ``build_sheet``.
    """
    moments = review_times(duration_s, start)
    frames = extract_frames(video, [at for _, at in moments], video.with_suffix(".frames"))
    labelled = [(f"{label}  {at:.2f} s", frame) for (label, at), frame in zip(moments, frames, strict=True)]
    return frames, build_sheet(labelled, video.with_suffix(".sheet.png"))
