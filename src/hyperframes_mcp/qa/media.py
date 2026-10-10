"""ffmpeg and ffprobe access for render QA: tool lookup, probing, the analysis pass.

Binaries: ``$FFMPEG_BIN`` if set, else ``ffmpeg`` on PATH. ffprobe is ``$FFPROBE_BIN``,
else the ``ffprobe`` beside ``$FFMPEG_BIN``, else ``ffprobe`` on PATH.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from hyperframes_mcp.qa.models import (
    Analysis,
    Interval,
    QALimits,
    QAToolError,
    Tools,
    UnreadableMediaError,
    num,
)

# Intervals shorter than these are not reported; longer ones are listed, and only those
# above the configured maximum fail. A maximum below the floor lowers the floor, so a
# strict limit still sees every interval it would fail on.
BLACK_REPORT_FLOOR_S = 0.5
FREEZE_REPORT_FLOOR_S = 1.0
SILENCE_REPORT_FLOOR_S = 0.5

_BLACK_RE = re.compile(r"black_start:(\S+)\s+black_end:(\S+)")
_FREEZE_START_RE = re.compile(r"freeze_start:\s*(\S+)")
_FREEZE_END_RE = re.compile(r"freeze_end:\s*(\S+)")
_SILENCE_START_RE = re.compile(r"silence_start:\s*(\S+)")
_SILENCE_END_RE = re.compile(r"silence_end:\s*(\S+)")
_LUFS_RE = re.compile(r"^\s*I:\s+(\S+)\s+LUFS", re.MULTILINE)
_TRUE_PEAK_RE = re.compile(r"True peak:\s*\n\s*Peak:\s+(\S+)\s+dBFS")
_DURATION_TAG_RE = re.compile(r"(\d+):(\d+):(\d+(?:\.\d+)?)")


def resolve_tools(env: Mapping[str, str] | None = None) -> Tools:
    """Find ffmpeg and ffprobe as the module docstring says; QAToolError if absent."""
    env = os.environ if env is None else env
    ffmpeg_setting = env.get("FFMPEG_BIN") or "ffmpeg"
    search = env.get("PATH")
    ffmpeg = shutil.which(ffmpeg_setting, path=search)
    if ffmpeg is None:
        where = "FFMPEG_BIN" if env.get("FFMPEG_BIN") else "PATH"
        raise QAToolError(f"ffmpeg not found ({where}: {ffmpeg_setting})")

    if env.get("FFPROBE_BIN"):
        candidates = [env["FFPROBE_BIN"]]
    elif env.get("FFMPEG_BIN"):
        candidates = [str(Path(ffmpeg).with_name("ffprobe")), "ffprobe"]
    else:
        candidates = ["ffprobe"]
    for candidate in candidates:
        ffprobe = shutil.which(candidate, path=search)
        if ffprobe:
            return Tools(ffmpeg=ffmpeg, ffprobe=ffprobe)
    raise QAToolError(f"ffprobe not found (tried {', '.join(candidates)}); set FFPROBE_BIN or install ffprobe")


def run_command(cmd: Sequence[str], *, text: bool = True) -> subprocess.CompletedProcess:
    """Run a command, capturing output. A binary that cannot start is a QAToolError."""
    try:
        return subprocess.run(
            list(cmd),
            capture_output=True,
            text=text,
            errors="replace" if text else None,
            check=False,
        )
    except OSError as exc:
        raise QAToolError(f"cannot run {cmd[0]}: {exc}") from exc


def _last_line(output: str, fallback: str) -> str:
    lines = output.strip().splitlines()
    return lines[-1] if lines else fallback


def probe_media(path: str | Path, tools: Tools) -> dict[str, Any]:
    """Run ffprobe and return its JSON; UnreadableMediaError for an unreadable file."""
    proc = run_command(
        [
            tools.ffprobe,
            "-v",
            "error",
            "-show_format",
            "-show_streams",
            "-of",
            "json",
            str(path),
        ]
    )
    if proc.returncode != 0:
        raise UnreadableMediaError(_last_line(proc.stderr, "unknown ffprobe error"))
    try:
        probe = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise UnreadableMediaError(f"ffprobe returned invalid JSON: {exc}") from exc
    if not probe.get("streams"):
        raise UnreadableMediaError("no streams found")
    return probe


def first_stream(probe: Mapping[str, Any], kind: str) -> dict[str, Any] | None:
    """The first video or audio stream, ignoring cover art."""
    for stream in probe.get("streams", []):
        if stream.get("codec_type") != kind:
            continue
        if (stream.get("disposition") or {}).get("attached_pic"):
            continue
        return stream
    return None


def format_duration(probe: Mapping[str, Any]) -> float | None:
    """Container duration in seconds."""
    return num((probe.get("format") or {}).get("duration"))


def stream_duration(stream: Mapping[str, Any]) -> float | None:
    """Stream duration in seconds, from the stream field or a Matroska DURATION tag."""
    duration = num(stream.get("duration"))
    if duration is not None:
        return duration
    tag = str((stream.get("tags") or {}).get("DURATION") or "").strip()
    if match := _DURATION_TAG_RE.fullmatch(tag):
        return int(match[1]) * 3600 + int(match[2]) * 60 + float(match[3])
    return None


def _ffmpeg_float(token: str) -> float | None:
    """ffmpeg prints '-inf' for a silent peak, which float() understands."""
    try:
        return float(token)
    except ValueError:
        return None


def _pair_intervals(starts: list[float], ends: list[float], total: float | None) -> list[Interval]:
    """Pair detector start and end events in order. An unmatched start runs to the end."""
    intervals = [Interval(s, e) for s, e in zip(starts, ends)]
    for start in starts[len(ends) :]:
        end = max(start, total) if total is not None else start
        intervals.append(Interval(start, end, open_ended=True))
    return intervals


def parse_analysis(stderr: str, total_duration: float | None = None) -> Analysis:
    """Parse the analysis pass log: ebur128, blackdetect, freezedetect, silencedetect."""
    black: list[Interval] = []
    freeze_starts: list[float] = []
    freeze_ends: list[float] = []
    silence_starts: list[float] = []
    silence_ends: list[float] = []
    for line in stderr.splitlines():
        if m := _BLACK_RE.search(line):
            black.append(Interval(float(m[1]), float(m[2])))
        elif m := _FREEZE_START_RE.search(line):
            freeze_starts.append(float(m[1]))
        elif m := _FREEZE_END_RE.search(line):
            freeze_ends.append(float(m[1]))
        elif m := _SILENCE_START_RE.search(line):
            silence_starts.append(float(m[1]))
        elif m := _SILENCE_END_RE.search(line):
            silence_ends.append(float(m[1]))
    lufs = _LUFS_RE.search(stderr)
    peak = _TRUE_PEAK_RE.search(stderr)
    return Analysis(
        integrated_lufs=_ffmpeg_float(lufs[1]) if lufs else None,
        true_peak_dbtp=_ffmpeg_float(peak[1]) if peak else None,
        black=black,
        freeze=_pair_intervals(freeze_starts, freeze_ends, total_duration),
        silence=_pair_intervals(silence_starts, silence_ends, total_duration),
    )


def analyze_media(path: str | Path, tools: Tools, probe: Mapping[str, Any], limits: QALimits) -> Analysis:
    """Decode once and measure loudness, black frames, frozen frames and silence."""
    video = first_stream(probe, "video")
    audio = first_stream(probe, "audio")
    if video is None and audio is None:
        return Analysis()
    cmd = [tools.ffmpeg, "-hide_banner", "-nostats", "-i", str(path)]
    if video is not None:
        black_d = min(BLACK_REPORT_FLOOR_S, limits.max_black_s)
        freeze_d = min(FREEZE_REPORT_FLOOR_S, limits.max_freeze_s)
        vf = (
            f"blackdetect=d={black_d}:pic_th={limits.black_pic_th}"
            f":pix_th={limits.black_pix_th},"
            f"freezedetect=n={limits.freeze_noise_db}dB:d={freeze_d}"
        )
        cmd += ["-map", f"0:{video['index']}", "-vf", vf]
    if audio is not None:
        silence_d = min(SILENCE_REPORT_FLOOR_S, limits.max_silence_s)
        af = f"ebur128=peak=true:framelog=quiet,silencedetect=n={limits.silence_noise_db}dB:d={silence_d}"
        cmd += ["-map", f"0:{audio['index']}", "-af", af]
    proc = run_command([*cmd, "-f", "null", "-"])
    if proc.returncode != 0:
        reason = _last_line(proc.stderr, "no output")
        raise UnreadableMediaError(f"ffmpeg could not decode the file: {reason}")
    return parse_analysis(proc.stderr, format_duration(probe))
