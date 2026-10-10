"""Render a staged project to an MP4 with the pinned HyperFrames CLI, the hardened way.

The hardening is not written again here: the scrubbed environment, the no-network sandbox, the process-group
timeout and the probing of the output are ``src/visual_generator/animated/hyperframes_cli.py``, the same code
the animated scene renderer runs. What this module adds is what a motion render is: the project is the one
``stage.stage_block`` wrote, the CLI runs strict, the clip must be H.264 at the canvas of the tokens (1920x1080,
24 fps) and as long as the project's root composition, and the result carries the sha256 of the file so two
renders can be compared.

Limits (workers, timeout factor, size, network isolation, node and browser overrides) are the ``animated_scene``
section of ``config/visuals_pipeline.yaml``: one place to change them for every HyperFrames render.
"""

from __future__ import annotations

import hashlib
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from hyperframes_mcp.hfcli.cli import (
    NODE_DIR,
    RendererUnavailable,
    RenderFailed,
    accept_clip,
    detect_isolation,
    find_toolchain,
    render_timeout,
    run_cli,
)
from hyperframes_mcp.hfcli.settings import (
    AnimatedSettings,
    load_animated_settings,
)
from hyperframes_mcp.motion import tokens
from hyperframes_mcp.motion.sheet import media_environment
from hyperframes_mcp.util.atomic import atomic_write_bytes

# The scene limits cap a clip at 40 MB, which is right for a 6 to 12 second scene. A whole video is longer: the
# v3 cold open (87 s, graded photographs with grain) came to 55 MB, about 0.63 MB a second.
MAX_MB_PER_SECOND = 1.5

_ROOT_TAG = re.compile(r'<[^>]*\bdata-composition-id="[^"]*"[^>]*>')
_DURATION = re.compile(r'\bdata-duration="([0-9.]+)"')


@dataclass(frozen=True)
class RenderResult:
    """A rendered clip and its measurements."""

    path: Path
    duration_s: float
    width: int
    height: int
    fps: int
    size_bytes: int
    sha256: str
    render_seconds: float
    isolation: str


def project_duration(project_dir: Path) -> float:
    """The length the project asks for: ``data-duration`` of the first composition in its ``index.html``.

    Raises:
        RenderFailed: there is no ``index.html`` or no root composition with a duration.
    """
    index = project_dir / "index.html"
    try:
        text = index.read_text(encoding="utf-8")
    except OSError as error:
        raise RenderFailed(f"the project has no index.html: {error}") from error
    root = _ROOT_TAG.search(text)
    duration = _DURATION.search(root.group(0)) if root else None
    if duration is None:
        raise RenderFailed("index.html has no root composition with a data-duration to render")
    return float(duration.group(1))


def settings_for_length(seconds: float) -> AnimatedSettings:
    """The animated-scene limits with the size cap raised to fit a video ``seconds`` long (never below the cap itself)."""
    base = load_animated_settings()
    cap = max(base.max_output_mb, MAX_MB_PER_SECOND * seconds)
    return base.model_copy(update={"max_output_mb": cap})


def render_project(
    project_dir: str | Path,
    output: str | Path,
    *,
    timeout: float | None = None,
    node_dir: Path = NODE_DIR,
    settings: AnimatedSettings | None = None,
) -> RenderResult:
    """Render the HyperFrames project in ``project_dir`` to ``output`` (an MP4) and return its measurements.

    ``timeout`` is in seconds (default: the larger of the settings' floor and their factor times the project's
    length). ``node_dir`` is the folder whose ``node_modules`` holds the pinned CLI (``hyperframes-mcp setup`` installs it). The CLI runs strict (``--no-best-effort``): a block whose script
    threw never registers its timeline, and best-effort mode waits 45 seconds, writes empty frames and exits 0.
    ``output`` is only written once the clip is accepted.

    Raises:
        RendererUnavailable: node, the pinned CLI, the browser or ffmpeg is missing, or isolation is required and absent.
        RenderFailed: the project is not renderable, the CLI failed (a block that threw says why), or the clip is
            not what the project asked for.
        RenderTimeout: the render ran past its limit and was killed.
    """
    project = Path(project_dir)
    settings = settings or load_animated_settings()
    expected = project_duration(project)
    tools = find_toolchain(node_dir, settings, media_env=media_environment())
    availability = detect_isolation(settings)
    if not availability.ok:
        raise RendererUnavailable(availability.reason)
    canvas = tokens.default_tokens().canvas
    limit = timeout if timeout is not None else render_timeout(settings, expected)

    with tempfile.TemporaryDirectory(prefix="motion-render-") as scratch_name:
        scratch = Path(scratch_name)
        clip = scratch / "clip.mp4"
        wall = run_cli(
            tools,
            project,
            scratch,
            clip,
            fps=canvas["fps"],
            workers=settings.workers,
            timeout=limit,
            isolation=availability.isolation,
            strict=True,
        )
        width, height, duration, size = accept_clip(
            tools.media,
            clip,
            expected_s=expected,
            width=canvas["width"],
            height=canvas["height"],
            max_output_mb=settings.max_output_mb,
            fps=canvas["fps"],
            subject="project",
        )
        data = clip.read_bytes()
        atomic_write_bytes(output, data)

    return RenderResult(
        path=Path(output),
        duration_s=duration,
        width=width,
        height=height,
        fps=canvas["fps"],
        size_bytes=size,
        sha256=hashlib.sha256(data).hexdigest(),
        render_seconds=round(wall, 2),
        isolation=availability.isolation,
    )
