"""Shared types for render QA: limits, results, errors and small number helpers."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any

PASS = "pass"
FAIL = "fail"
SKIP = "skip"


class QAToolError(RuntimeError):
    """ffmpeg or ffprobe is missing or cannot run. The CLI maps this to exit code 2."""


class UnreadableMediaError(ValueError):
    """ffprobe or ffmpeg could not read the file as media."""


class CaptionParseError(ValueError):
    """A caption sidecar is not valid SRT or ASS."""


@dataclass(frozen=True)
class QALimits:
    """Pass and fail thresholds. Defaults are conservative thresholds for a short video."""

    width: int = 1920
    height: int = 1080
    fps: float = 30.0
    fps_tolerance: float = 0.01
    video_codec: str = "h264"
    pix_fmt: str = "yuv420p"
    color_range: str = "tv"
    color_standard: str = "bt709"
    duration_tolerance_s: float = 1.0
    target_lufs: float = -16.0
    lufs_tolerance: float = 1.5
    true_peak_max_dbtp: float = -1.0
    sync_tolerance_s: float = 0.1
    max_black_s: float = 1.0
    max_freeze_s: float = 3.0
    max_silence_s: float = 2.0
    black_pix_th: float = 0.10
    black_pic_th: float = 0.98
    freeze_noise_db: float = -60.0
    silence_noise_db: float = -50.0
    max_cue_overlap_s: float = 0.1
    caption_end_slack_s: float = 0.1
    caption_samples: int = 5
    caption_band: float = 0.25
    # Fraction of the frame height between the caption band and the bottom edge: 0 for 16:9
    # captions at the bottom, above the player controls of a 9:16 Short for the Shorts layout.
    caption_band_offset: float = 0.0
    # A Short may not run longer than this (YouTube: 60 s); None means no ceiling (long form).
    max_duration_s: float | None = None
    caption_min_score: float = 0.04


@dataclass(frozen=True)
class Tools:
    """Resolved ffmpeg and ffprobe executables."""

    ffmpeg: str
    ffprobe: str


@dataclass(frozen=True)
class Interval:
    """A detected span in seconds. ``open_ended`` means the file ended before it did."""

    start: float
    end: float
    open_ended: bool = False

    @property
    def duration(self) -> float:
        return self.end - self.start

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": rnd(self.start),
            "end": rnd(self.end),
            "duration": rnd(self.duration),
            "open_ended": self.open_ended,
        }


@dataclass(frozen=True)
class Analysis:
    """What the single ffmpeg analysis pass measured. ``None`` means not measured."""

    integrated_lufs: float | None = None
    true_peak_dbtp: float | None = None
    black: list[Interval] = field(default_factory=list)
    freeze: list[Interval] = field(default_factory=list)
    silence: list[Interval] = field(default_factory=list)


@dataclass(frozen=True)
class Cue:
    """One caption cue."""

    start: float
    end: float
    text: str = ""


@dataclass
class CheckResult:
    """The outcome of one check. ``findings`` are human-readable lines for the table."""

    name: str
    status: str
    summary: str
    details: dict[str, Any] = field(default_factory=dict)
    findings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "summary": self.summary,
            "details": self.details,
            "findings": self.findings,
        }


@dataclass
class QAReport:
    """All check results for one video."""

    path: str
    ffmpeg: str
    ffprobe: str
    checks: list[CheckResult]

    @property
    def passed(self) -> bool:
        return not any(check.status == FAIL for check in self.checks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.path,
            "passed": self.passed,
            "ffmpeg": self.ffmpeg,
            "ffprobe": self.ffprobe,
            "checks": [check.to_dict() for check in self.checks],
        }


def rnd(value: float | None) -> float | None:
    """Round for output. Non-finite numbers (ffmpeg's -inf) become None: valid JSON."""
    if value is None or not math.isfinite(value):
        return None
    return round(value, 3)


def num(value: Any) -> float | None:
    """A float from an ffprobe field, or None for missing, N/A and nan."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) else number


def frame_rate(value: Any) -> float | None:
    """A frame rate such as '30000/1001' as a float; None for '0/0' and missing."""
    try:
        fraction = Fraction(str(value))
    except (ValueError, ZeroDivisionError):
        return None
    return float(fraction) if fraction > 0 else None


def make_check(
    name: str,
    ok: bool,
    summary: str,
    details: dict[str, Any] | None = None,
    findings: list[str] | None = None,
) -> CheckResult:
    return CheckResult(name, PASS if ok else FAIL, summary, details or {}, findings or [])


def make_skip(name: str, summary: str, details: dict[str, Any] | None = None) -> CheckResult:
    return CheckResult(name, SKIP, summary, details or {})
