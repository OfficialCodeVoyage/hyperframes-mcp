"""Sampling frames from a finished render: which moments, and one ffmpeg call per frame.

The moments are an even spread (one frame about every ``interval_s``) plus the scene changes
ffmpeg detects, capped at ``max_frames``; the even spread is kept first so a long video is never
left with a blind stretch. Every value that reaches an ffmpeg command line is built here from a
checked piece: the video path is made absolute and must be a plain video file (never a URL, a
device or a protocol such as ``concat:``), timestamps are finite numbers we format ourselves, and
output names are fixed by the caller (security.md, "Inputs that reach FFmpeg").
"""

from __future__ import annotations

import math
import os
import re
from pathlib import Path

from hyperframes_mcp.qa.media import run_command
from hyperframes_mcp.qa.models import QAToolError, Tools
from hyperframes_mcp.qa.safe_input import UnsafeProjectInput, check_input_name

DEFAULT_INTERVAL_S = 30.0
DEFAULT_MAX_FRAMES = 40
SCENE_THRESHOLD = 0.4
# A scene-change frame is taken a little after the cut, so it shows the new shot and not the last
# frame of the old one.
SCENE_OFFSET_S = 0.25
MIN_GAP_S = 2.0
TILE_WIDTH = 512
TILE_HEIGHT = 288

VIDEO_SUFFIXES = frozenset({".mp4", ".m4v", ".mov", ".mkv", ".webm"})
_SHOWINFO_TIME = re.compile(r"Parsed_showinfo\S*\s.*?pts_time:\s*([0-9]+(?:\.[0-9]+)?)")
# Only plain files are ever opened as input.
_INPUT_OPTIONS = ["-protocol_whitelist", "file"]


class UnsafeVideoPath(ValueError):
    """The path is not a plain local video file, so it must not be handed to ffmpeg."""


def _last_line(text: str, fallback: str) -> str:
    lines = text.strip().splitlines()
    return lines[-1] if lines else fallback


def format_clock(seconds: float) -> str:
    """``MM:SS``, or ``H:MM:SS`` from an hour on (the form printed on the contact sheet tiles)."""
    total = max(0, int(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def checked_video(path: str | os.PathLike[str]) -> Path:
    """The absolute path of an existing local video file.

    Raises:
        UnsafeVideoPath: a protocol or device name, control characters, a missing file, a
            directory, or a file that is not a video container we render.
    """
    text = os.fspath(path) if isinstance(path, (str, os.PathLike)) else ""
    try:
        check_input_name(text, "video")
    except UnsafeProjectInput as exc:
        raise UnsafeVideoPath(str(exc)) from exc
    resolved = Path(text).resolve()
    if resolved.suffix.lower() not in VIDEO_SUFFIXES:
        raise UnsafeVideoPath(f"video: {resolved.name!r} is not a video file")
    if not resolved.is_file():
        raise UnsafeVideoPath(f"video: {resolved} is not a file")
    return resolved


def _evenly(items: list[float], count: int) -> list[float]:
    """``count`` of ``items``, spread evenly across them in order."""
    if count <= 0:
        return []
    if count >= len(items):
        return list(items)
    if count == 1:
        return [items[len(items) // 2]]
    step = (len(items) - 1) / (count - 1)
    return [items[round(i * step)] for i in range(count)]


def plan_timestamps(
    duration_s: float,
    scene_times: list[float],
    *,
    interval_s: float = DEFAULT_INTERVAL_S,
    max_frames: int = DEFAULT_MAX_FRAMES,
    min_gap_s: float = MIN_GAP_S,
) -> list[tuple[float, str]]:
    """The moments to sample, in time order, each with why: ``interval`` or ``scene_change``.

    Raises:
        ValueError: ``interval_s`` is not above zero or ``max_frames`` is below one.
    """
    if not interval_s > 0 or not math.isfinite(interval_s):
        raise ValueError("interval_s must be above zero")
    if max_frames < 1:
        raise ValueError("max_frames must be at least one")
    if not math.isfinite(duration_s) or duration_s <= 0:
        return []

    count = min(max_frames, max(1, math.ceil(duration_s / interval_s)))
    plan = [((i + 0.5) * duration_s / count, "interval") for i in range(count)]
    taken = [t for t, _ in plan]

    extra: list[float] = []
    for cut in sorted(scene_times):
        moment = cut + SCENE_OFFSET_S
        if not 0 <= moment < duration_s - 0.05:
            continue
        if all(abs(moment - other) >= min_gap_s for other in (*taken, *extra)):
            extra.append(moment)
    plan += [(t, "scene_change") for t in _evenly(extra, max_frames - count)]
    return sorted(plan)


def detect_scene_changes(
    video: str | os.PathLike[str],
    tools: Tools,
    *,
    threshold: float = SCENE_THRESHOLD,
) -> list[float]:
    """Times (seconds) of the hard cuts ffmpeg's scene score finds. Empty if the pass fails.

    The picture is scaled down first, so this decodes a 10 minute 1080p render in well under a
    minute. A failure is not an error: the even spread alone still covers the video.

    Raises:
        ValueError: ``threshold`` is not between 0 and 1 (exclusive).
        UnsafeVideoPath: ``video`` is not a plain local video file.
    """
    if not (math.isfinite(threshold) and 0 < threshold < 1):
        raise ValueError("threshold must be between 0 and 1")
    source = checked_video(video)
    proc = run_command(
        [
            tools.ffmpeg,
            "-hide_banner",
            "-nostats",
            *_INPUT_OPTIONS,
            "-i",
            str(source),
            "-an",
            "-sn",
            "-vf",
            f"scale=320:-2,select='gt(scene,{threshold:g})',showinfo",
            "-f",
            "null",
            "-",
        ]
    )
    if proc.returncode != 0:
        return []
    return [float(m[1]) for m in _SHOWINFO_TIME.finditer(proc.stderr)]


def extract_frame(
    video: str | os.PathLike[str],
    seconds: float,
    out: Path,
    tools: Tools,
    *,
    width: int = TILE_WIDTH,
    height: int = TILE_HEIGHT,
) -> Path:
    """Write the frame at ``seconds`` to ``out`` (a ``.jpg``), scaled to fit ``width`` x ``height``.

    Raises:
        ValueError: a timestamp that is negative or not finite, a size outside 16-4096, or an
            output that is not a ``.jpg``.
        UnsafeVideoPath: ``video`` is not a plain local video file.
        QAToolError: ffmpeg failed or wrote nothing (a time past the end of the video).
    """
    if not (math.isfinite(seconds) and seconds >= 0):
        raise ValueError("seconds must be a finite number, zero or more")
    if not (16 <= width <= 4096 and 16 <= height <= 4096):
        raise ValueError("width and height must be between 16 and 4096")
    if out.suffix.lower() != ".jpg":
        raise ValueError("the frame is written as a .jpg")
    source = checked_video(video)
    proc = run_command(
        [
            tools.ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{seconds:.3f}",
            *_INPUT_OPTIONS,
            "-i",
            str(source),
            "-an",
            "-sn",
            "-frames:v",
            "1",
            "-vf",
            f"scale={int(width)}:{int(height)}:force_original_aspect_ratio=decrease",
            "-q:v",
            "3",
            str(out),
        ]
    )
    if proc.returncode != 0:
        raise QAToolError(f"ffmpeg could not take the frame at {seconds:.1f}s: {_last_line(proc.stderr, 'no output')}")
    if not out.is_file() or out.stat().st_size == 0:
        raise QAToolError(f"ffmpeg wrote no frame at {seconds:.1f}s")
    return out
