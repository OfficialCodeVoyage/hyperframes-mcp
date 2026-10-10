"""Settings of the review renders, from ``visuals_pipeline.yaml`` (section ``animated_scene``): the user's copy in
``~/.hyperframes-mcp/config`` or the bundled one.

A missing file or key gives the defaults below. The limits that protect the machine (render timeout, output size)
live here, so they are changed in one place.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from hyperframes_mcp.util.paths import config_path

SETTINGS_PATH = config_path("visuals_pipeline.yaml")
MIN_SCENE_SECONDS = 4.0  # the bounds of any rendered clip
MAX_SCENE_SECONDS = 30.0


class AnimatedSettings(BaseModel):
    """How animated scenes are filled, rendered and bounded."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # Whether review renders are offered at all.
    enabled: bool = True
    # Clip length when the cue's beat slot is not known, and the bounds of any clip.
    default_scene_seconds: float = Field(default=8.0, ge=MIN_SCENE_SECONDS, le=MAX_SCENE_SECONDS)
    min_scene_seconds: float = Field(default=MIN_SCENE_SECONDS, ge=MIN_SCENE_SECONDS, le=MAX_SCENE_SECONDS)
    max_scene_seconds: float = Field(default=20.0, ge=MIN_SCENE_SECONDS, le=MAX_SCENE_SECONDS)
    # A render is killed after max(floor, factor x clip length) seconds (measured: 0.8 to 0.96x real time).
    render_timeout_factor: float = Field(default=6.0, gt=0)
    render_timeout_floor_s: float = Field(default=60.0, gt=0)
    # A clip larger than this is rejected.
    max_output_mb: float = Field(default=40.0, gt=0)
    # Parallel Chrome capture workers per render (about 256 MB each), and how many renders may run at once.
    workers: int = Field(default=2, ge=1, le=8)
    max_parallel_renders: int = Field(default=1, ge=1, le=4)
    # auto: run the render under a no-network sandbox where the OS has one (macOS sandbox-exec),
    # else with the environment hardening only. require: unavailable without the sandbox.
    # off: environment hardening only.
    network_isolation: Literal["auto", "require", "off"] = "auto"
    # Overrides; None finds node on PATH and the chrome-headless-shell in ~/.cache/hyperframes.
    node_bin: str | None = None
    browser_path: str | None = None


def load_animated_settings(path: Path = SETTINGS_PATH) -> AnimatedSettings:
    """Read ``animated_scene`` from ``visuals_pipeline.yaml``; defaults when absent.

    Raises:
        ValueError: a key is unknown or out of range (pydantic's ValidationError).
    """
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        return AnimatedSettings()
    return AnimatedSettings(**(data.get("animated_scene") or {}))
