"""Runtime settings of the I2V clip cap and adapter, read from ``config/visuals_i2v.yaml``.

A missing file gives the defaults below, which are the numbers in the file as shipped. Unknown
keys are an error, so a misspelt limit cannot silently fall back to its default.
"""

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from hyperframes_mcp.util.paths import config_path

SETTINGS_PATH = config_path("visuals_i2v.yaml")


class _Section(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ClipLimits(_Section):
    """The per-video cap: 6 clips, about 30 s, about $2.25 on the cheap tier.

    ``max_spend_usd`` is the one per-video image-to-video ceiling. $2.25 is the cheap tier; a mid tier is about $5.
    Change this single value to pick one. The server's own limits (``config/limits.yaml``) apply on top.
    """

    max_clips: int = Field(default=6, ge=1)
    max_attempts_per_clip: int = Field(default=3, ge=1)
    max_total_seconds: float = Field(default=30.0, gt=0)
    max_spend_usd: float = Field(default=2.25, gt=0)


class DownloadSettings(_Section):
    """Bounds on fetching the finished video (the stock downloader's pattern)."""

    max_clip_mb: float = Field(default=100.0, gt=0)
    timeout_seconds: float = Field(default=300.0, gt=0)


class QueueSettings(_Section):
    """How long to wait on fal's queue."""

    poll_interval_seconds: float = Field(default=2.0, gt=0)
    job_timeout_seconds: float = Field(default=600.0, gt=0)


class I2VSettings(_Section):
    # The model and clip length the visual stage asks for, until the bake-off picks one. The
    # cheapest priced model at its shortest length: 6 clips cost about $0.72 in a clean run.
    model: str = "veo-3.1-lite"
    clip_seconds: int = Field(default=4, ge=1)
    limits: ClipLimits = ClipLimits()
    download: DownloadSettings = DownloadSettings()
    queue: QueueSettings = QueueSettings()
    max_still_mb: float = Field(default=7.0, gt=0)
    ledger_db: str = "data/i2v_clips.db"


def load_i2v_settings(path: Path = SETTINGS_PATH) -> I2VSettings:
    """Read the ``i2v`` section of ``config/visuals_i2v.yaml``.

    Raises:
        pydantic.ValidationError: a value is malformed or a key is unknown.
    """
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        data = {}
    return I2VSettings(**(data.get("i2v") or {}))
