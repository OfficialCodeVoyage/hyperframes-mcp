"""Master the finished video's sound: two-pass loudnorm to a target loudness, a true-peak ceiling, the picture untouched.

The settings are the ones that took an earlier prototype to -14.0 LUFS and about -1.4 dBTP: ``loudnorm`` measures the
mix (pass one) and applies one linear gain from that measurement (pass two), a sample-peak limiter 0.5 dB under
the ceiling catches what the true peak of the gain would leave over, the video stream is copied and the audio is
AAC at 48 kHz. The result is measured again with ``ebur128`` (``measure``), which is how a test or a person checks
the numbers; if the measurement is off the target, the pass is repeated with the target moved by the error (at most
three passes), so the numbers reported are those of the file delivered.

ffmpeg is ``$FFMPEG_BIN``, else the project's ffmpeg-full, else ``ffmpeg`` on PATH (``src/hyperframes_mcp/motion/sheet.py``).
"""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from hyperframes_mcp.motion.sheet import ffmpeg_binary
from hyperframes_mcp.qa.frames import UnsafeVideoPath, checked_video
from hyperframes_mcp.qa.media import parse_analysis

LUFS_TOLERANCE = 0.15
_PASSES = 3
_TIMEOUT_S = 1800
_LRA = re.compile(r"^\s*LRA:\s+(\S+)\s+LU", re.MULTILINE)
_LIMITER_HEADROOM_DB = 0.5
_MEASURED = ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")


class MasterError(RuntimeError):
    """The sound could not be measured or mastered; the message says why."""


@dataclass(frozen=True)
class Loudness:
    """EBU R128 numbers of a file: integrated loudness (LUFS), true peak (dBTP), loudness range (LU)."""

    integrated_lufs: float
    true_peak_dbtp: float
    loudness_range_lu: float


def _run(command: list[str], what: str) -> str:
    """Run ffmpeg and return what it printed on stderr; a failure is a :class:`MasterError`."""
    try:
        done = subprocess.run(
            command,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise MasterError(f"ffmpeg did not run ({what}): {error}") from error
    if done.returncode != 0:
        lines = done.stderr.strip().splitlines()
        reason = lines[-1] if lines else "no message"
        if "matches no streams" in done.stderr:
            reason = "the video has no audio stream"
        raise MasterError(f"ffmpeg failed ({what}): {reason}")
    return done.stderr


def _plain_video(video: str | Path) -> Path:
    try:
        return checked_video(video)
    except UnsafeVideoPath as error:
        raise MasterError(str(error)) from error


def measure(video: str | Path, *, ffmpeg: str | None = None) -> Loudness:
    """Integrated loudness, true peak and loudness range of the first audio stream of ``video``."""
    source = _plain_video(video)
    stderr = _run(
        [
            ffmpeg or ffmpeg_binary(),
            "-hide_banner",
            "-nostats",
            "-protocol_whitelist",
            "file",
            "-i",
            str(source),
            "-map",
            "0:a:0",
            "-vn",
            "-af",
            "ebur128=peak=true:framelog=quiet",
            "-f",
            "null",
            "-",
        ],
        f"measuring {source.name}",
    )
    analysis = parse_analysis(stderr)
    lra = _LRA.search(stderr)
    if analysis.integrated_lufs is None or analysis.true_peak_dbtp is None or lra is None:
        raise MasterError(f"ffmpeg printed no loudness summary for {source.name}")
    return Loudness(analysis.integrated_lufs, analysis.true_peak_dbtp, float(lra.group(1)))


def _loudnorm_measurement(source: Path, lufs: float, true_peak_db: float, ffmpeg: str) -> dict[str, str]:
    """Pass one: ``loudnorm`` measures the mix; its JSON feeds pass two."""
    stderr = _run(
        [
            ffmpeg,
            "-hide_banner",
            "-nostats",
            "-protocol_whitelist",
            "file",
            "-i",
            str(source),
            "-map",
            "0:a:0",
            "-vn",
            "-af",
            f"loudnorm=I={lufs:g}:TP={true_peak_db:g}:LRA=11:print_format=json",
            "-f",
            "null",
            "-",
        ],
        f"measuring {source.name}",
    )
    try:
        found = json.loads(stderr[stderr.rindex("{") : stderr.rindex("}") + 1])
        values = {key: str(found[key]) for key in _MEASURED}
        finite = all(math.isfinite(float(value)) for value in values.values())
    except (ValueError, KeyError) as error:
        raise MasterError(f"ffmpeg gave no usable loudness measurement for {source.name}") from error
    if not finite:
        raise MasterError(f"{source.name} has no audible sound to master (it is silent)")
    return values


def master(
    video: str | Path,
    out: str | Path,
    *,
    lufs: float = -14.0,
    true_peak_db: float = -1.0,
    ffmpeg: str | None = None,
) -> Loudness:
    """Write ``out``: ``video`` with its sound at ``lufs`` integrated and no true peak over ``true_peak_db``.

    Returns the measurement of the file written.

    Raises:
        MasterError: ``video`` is not a plain video file or has no audible sound, ffmpeg fails, or three passes
            do not reach the target.
    """
    binary = ffmpeg or ffmpeg_binary()
    source = _plain_video(video)
    destination = Path(out)
    destination.parent.mkdir(parents=True, exist_ok=True)
    measured = _loudnorm_measurement(source, lufs, true_peak_db, binary)
    limit = round(10 ** ((true_peak_db - _LIMITER_HEADROOM_DB) / 20), 4)
    partial = destination.with_name(f".{destination.stem}.mastering{destination.suffix}")
    target = lufs
    reached = ""
    try:
        for _ in range(_PASSES):
            sound = (
                f"loudnorm=I={target:.2f}:TP={true_peak_db:g}:LRA=11"
                f":measured_I={measured['input_i']}:measured_TP={measured['input_tp']}"
                f":measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}"
                f":offset={measured['target_offset']}:linear=true,"
                f"alimiter=limit={limit}:attack=5:release=80:level=false,aresample=48000"
            )
            _run(
                [
                    binary, "-v", "error", "-y", "-protocol_whitelist", "file",
                    "-i", str(source), "-map", "0:v", "-map", "0:a:0", "-c:v", "copy",
                    "-af", sound, "-c:a", "aac", "-b:a", "256k",
                    "-movflags", "+faststart", "-f", "mp4", str(partial),
                ],
                f"mastering {source.name}",
            )  # fmt: skip
            result = measure(partial, ffmpeg=binary)
            error = lufs - result.integrated_lufs
            over = result.true_peak_dbtp - true_peak_db
            if abs(error) <= LUFS_TOLERANCE and over <= 0:
                os.replace(partial, destination)
                return result
            target += error - (over + 0.05 if over > 0 else 0.0)
            reached = f"{result.integrated_lufs:.1f} LUFS and {result.true_peak_dbtp:.1f} dBTP"
        raise MasterError(
            f"after {_PASSES} passes the sound is {reached}; "
            f"the target is {lufs:g} LUFS and at most {true_peak_db:g} dBTP"
        )
    finally:
        partial.unlink(missing_ok=True)
