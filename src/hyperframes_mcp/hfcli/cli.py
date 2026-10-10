"""The pinned HyperFrames CLI, run the hardened way.

HyperFrames as shipped can send telemetry and reach the network, so the CLI only ever runs through this module. The
motion library's renders (``motion/render.py``) and its calls to the CLI (``motion/hf_cli.py``) share it, so there is one
copy of the hardening:

- the child gets a scrubbed environment: telemetry, update check, auto-install and skill installs
  switched off, a private throwaway HOME, font and frame caches inside the temp directory, the
  browser pinned to a local chrome-headless-shell, and nothing of the parent's environment but PATH;
- the render runs under a no-network sandbox where the OS has one (macOS ``sandbox-exec``); where it
  has none the hardening above is all there is, and ``network_isolation: require`` makes that
  unavailable instead;
- a timeout kills the whole process group, the output is probed (codec, size, frame rate, length,
  file size) before it is accepted, and every failure is a typed error with a short reason.

Nothing is imported or run at import time that needs node, the CLI, the browser or ffmpeg.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from hyperframes_mcp.hfcli.settings import AnimatedSettings
from hyperframes_mcp.qa.media import first_stream, format_duration, probe_media, resolve_tools
from hyperframes_mcp.qa.models import QAToolError, Tools, UnreadableMediaError
from hyperframes_mcp.util.paths import NODE_TEMPLATE_DIR
from hyperframes_mcp.util.paths import node_dir as default_node_dir

logger = logging.getLogger(__name__)

NODE_DIR = default_node_dir()
MIN_NODE_MAJOR = 22
# the CLI's own reporting and fetching, off wherever it runs: telemetry, update check, auto-install, skill installs
QUIET_ENV = {
    "HYPERFRAMES_NO_TELEMETRY": "1",
    "DO_NOT_TRACK": "1",
    "HYPERFRAMES_NO_UPDATE_CHECK": "1",
    "HYPERFRAMES_NO_AUTO_INSTALL": "1",
    "HYPERFRAMES_SKIP_SKILLS": "1",
}
# The macOS sandbox profile: everything allowed except outbound network, then outbound loopback and
# unix sockets allowed again. The render needs loopback (its own file server and the DevTools
# connection) and nothing else. Verified by tests/visual_generator/animated/test_renderer.py (a
# connection to 1.1.1.1 and a DNS lookup fail, loopback works); a profile that also allowed
# `(local ip "localhost:*")` was tried and blocked nothing.
SANDBOX_PROFILE = (
    "(version 1)(allow default)(deny network-outbound)"
    '(allow network-outbound (remote ip "localhost:*"))'
    "(allow network-outbound (remote unix-socket))"
)
_BROWSER_GLOB = "chrome-headless-shell/*/*/chrome-headless-shell"
_OUTPUT_DURATION_SLACK_S = 0.5
_FPS_SLACK = 0.01
_LOG_TAIL_CHARS = 1500
# A block whose script throws prints "[Browser:ERROR] <the error the page logged>", its reason included.
_PAGE_ERROR = re.compile(r"^\[Browser:ERROR\]\s*(.+)$", re.MULTILINE)
_PAGE_ERRORS_SHOWN = 3


class AnimatedSceneError(Exception):
    """Base of the errors of the animated scene layer; the message is safe to show a person."""


class RendererUnavailable(AnimatedSceneError):
    """Node, the pinned HyperFrames CLI, the libraries, the browser or ffmpeg is missing."""


class RenderFailed(AnimatedSceneError):
    """The render ran and did not give a usable clip."""


class RenderTimeout(RenderFailed):
    """The render exceeded its time limit and was killed."""


@dataclass(frozen=True)
class Availability:
    """Whether renders can run here, and if not, what is missing."""

    ok: bool
    reason: str = ""
    # "sandbox" (no network at the OS level) or "environment" (opt-out variables only)
    isolation: str = "environment"


@dataclass(frozen=True)
class Toolchain:
    """The programs a render runs: node, the pinned CLI, the browser, the PATH it gets, ffmpeg and ffprobe."""

    node: str
    cli: Path
    browser: Path
    path_dirs: tuple[str, ...]
    media: Tools


def pinned_version(node_dir: Path | None = None) -> str:
    """The HyperFrames version this package pins: its ``node/package.json`` (or the one in ``node_dir``)."""
    package = json.loads(((node_dir or NODE_TEMPLATE_DIR) / "package.json").read_text(encoding="utf-8"))
    return package["dependencies"]["hyperframes"]


# -- finding the tools ------------------------------------------------------------------------------


def cli_script(node_dir: Path) -> Path:
    """The pinned CLI's entry script in ``node_dir`` (it is there once ``npm ci`` has run)."""
    return node_dir / "node_modules" / "hyperframes" / "bin" / "hyperframes.mjs"


def find_toolchain(
    node_dir: Path,
    settings: AnimatedSettings,
    *,
    media_env: Mapping[str, str] | None = None,
) -> Toolchain:
    """Node 22+, the pinned CLI, a chrome-headless-shell and ffmpeg, or ``RendererUnavailable`` saying which is missing.

    ``media_env`` is the environment ffmpeg and ffprobe are looked up in (``resolve_tools``; the process
    environment when None).
    """
    node = shutil.which(settings.node_bin or "node")
    if node is None:
        raise RendererUnavailable("node is not installed (need Node 22 or newer)")
    major = node_major(node)
    if major is None or major < MIN_NODE_MAJOR:
        raise RendererUnavailable(
            f"node {major or 'of unknown version'} found; HyperFrames needs Node {MIN_NODE_MAJOR}+"
        )

    cli = cli_script(node_dir)
    if not cli.is_file():
        raise RendererUnavailable("HyperFrames is not installed: run `hyperframes-mcp setup`")
    installed = json.loads((cli.parents[1] / "package.json").read_text(encoding="utf-8"))["version"]
    if installed != pinned_version():
        raise RendererUnavailable(
            f"HyperFrames {installed} is installed but {pinned_version()} is pinned: run `hyperframes-mcp setup`"
        )

    browser = _find_browser(settings.browser_path)
    if browser is None:
        raise RendererUnavailable(
            "no chrome-headless-shell found: set animated_scene.browser_path or "
            "SCENE_RENDER_BROWSER, or run `hyperframes-mcp setup`"
        )
    try:
        media = resolve_tools(media_env)
    except QAToolError as error:
        raise RendererUnavailable(str(error)) from error
    return Toolchain(
        node=node,
        cli=cli,
        browser=browser,
        path_dirs=(
            str(Path(node).parent),
            str(Path(media.ffmpeg).parent),
            "/usr/bin",
            "/bin",
        ),
        media=media,
    )


def detect_isolation(settings: AnimatedSettings) -> Availability:
    """The network isolation a render gets here: the OS sandbox where there is one, else the environment alone."""
    if settings.network_isolation == "off":
        isolation = "environment"
    elif sys.platform == "darwin" and shutil.which("sandbox-exec"):
        isolation = "sandbox"
    else:
        isolation = "environment"
    if isolation == "environment" and settings.network_isolation == "require":
        return Availability(
            False,
            "network_isolation is 'require' but this system has no network sandbox (macOS sandbox-exec)",
        )
    return Availability(True, isolation=isolation)


def node_major(node: str) -> int | None:
    try:
        out = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=10, check=False).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = re.match(r"v(\d+)\.", out.strip())
    return int(match.group(1)) if match else None


def _find_browser(configured: str | None) -> Path | None:
    """The chrome-headless-shell to render with: configured, from the environment, or cached."""
    for candidate in (configured, os.environ.get("SCENE_RENDER_BROWSER")):
        if candidate:
            path = Path(candidate).expanduser()
            return path if path.is_file() and os.access(path, os.X_OK) else None
    cache = Path.home() / ".cache" / "hyperframes" / "chrome"
    found = sorted(cache.glob(_BROWSER_GLOB))
    return found[-1] if found and os.access(found[-1], os.X_OK) else None


# -- running it --------------------------------------------------------------------------------------


def render_timeout(settings: AnimatedSettings, duration_s: float) -> float:
    """Seconds a render of a ``duration_s`` clip may run before it is killed."""
    return max(settings.render_timeout_floor_s, settings.render_timeout_factor * duration_s)


def child_environment(tools: Toolchain, scratch: Path) -> dict[str, str]:
    """The child's whole environment: nothing is inherited. Creates ``scratch/home``."""
    home = scratch / "home"
    home.mkdir()
    return {
        "PATH": os.pathsep.join(tools.path_dirs),
        "HOME": str(home),
        "TMPDIR": str(scratch),
        **QUIET_ENV,
        "HYPERFRAMES_FONT_CACHE_DIR": str(scratch / "font-cache"),
        "HYPERFRAMES_EXTRACT_CACHE_DIR": str(scratch / "extract-cache"),
        "HYPERFRAMES_BROWSER_PATH": str(tools.browser),
        "NO_COLOR": "1",
    }


def run_cli(
    tools: Toolchain,
    project: Path,
    scratch: Path,
    clip: Path,
    *,
    fps: int,
    workers: int,
    timeout: float,
    isolation: str,
    strict: bool = False,
) -> float:
    """Render ``project`` to ``clip`` with the CLI in ``project`` as its working directory.

    Returns the wall seconds. By default the CLI is best-effort: it exits 0 and writes a clip even when a
    block's script threw and the frames are empty, printing only a warning. ``strict`` passes
    ``--no-best-effort``, which makes such a warning a failure (non-zero exit).

    ``scratch`` is an existing empty directory for the child's HOME, TMPDIR and caches.

    Raises:
        RenderFailed: the CLI could not start or exited non-zero. The message carries the errors the page
            reported (a block that threw says why), else the tail of the CLI's output.
        RenderTimeout: the render ran past ``timeout`` seconds and was killed with everything it started.
    """
    command = [
        tools.node,
        str(tools.cli),
        "render",
        "--output",
        str(clip),
        "--fps",
        str(fps),
        "--quality",
        "standard",
        "--workers",
        str(workers),
        "--quiet",
    ]
    if strict:
        command.append("--no-best-effort")
    if isolation == "sandbox":
        command = ["sandbox-exec", "-p", SANDBOX_PROFILE, *command]
    started = time.monotonic()
    try:
        process = subprocess.Popen(
            command,
            cwd=project,
            env=child_environment(tools, scratch),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            start_new_session=True,
        )
    except OSError as error:
        raise RenderFailed(f"could not start the renderer: {error}") from error
    try:
        output, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RenderTimeout(f"render exceeded {timeout:.0f} s and was killed") from None
    finally:
        # on a timeout this kills the render; after an exit it removes any Chrome children
        # the CLI left behind, so nothing outlives the call
        _kill_group(process)
    wall = time.monotonic() - started
    if process.returncode != 0:
        reason = "; ".join(_page_errors(output)[:_PAGE_ERRORS_SHOWN]) or _tail(output)
        raise RenderFailed(f"renderer exited with status {process.returncode}: {reason}")
    return wall


def accept_clip(
    media: Tools,
    clip: Path,
    *,
    expected_s: float,
    width: int,
    height: int,
    max_output_mb: float,
    fps: int | None = None,
    subject: str = "scene",
) -> tuple[int, int, float, int]:
    """Probe ``clip``; only an H.264 clip of the right size, length and (when given) frame rate is accepted.

    Returns ``(width, height, duration_s, size_bytes)``. ``subject`` names what asked for the length in the message.
    """
    if not clip.is_file():
        raise RenderFailed("the renderer finished without writing a clip")
    size = clip.stat().st_size
    limit = int(max_output_mb * 1_000_000)
    if size > limit:
        raise RenderFailed(f"the clip is {size / 1e6:.1f} MB; the limit is {max_output_mb} MB")
    try:
        probe = probe_media(clip, media)
    except UnreadableMediaError as error:
        raise RenderFailed(f"the clip cannot be read: {error}") from error
    video = first_stream(probe, "video")
    duration = format_duration(probe)
    if video is None or duration is None:
        raise RenderFailed("the clip has no video stream")
    got_width, got_height = int(video.get("width", 0)), int(video.get("height", 0))
    if (got_width, got_height) != (width, height) or video.get("codec_name") != "h264":
        raise RenderFailed(
            f"the clip is {video.get('codec_name')} {got_width}x{got_height}; expected h264 {width}x{height}"
        )
    if fps is not None:
        got_fps = _frame_rate(video)
        if got_fps is None or abs(got_fps - fps) > _FPS_SLACK:
            shown = "an unknown rate" if got_fps is None else f"{got_fps:g} fps"
            raise RenderFailed(f"the clip runs at {shown}; expected {fps} fps")
    if abs(duration - expected_s) > _OUTPUT_DURATION_SLACK_S:
        raise RenderFailed(f"the clip is {duration:.2f} s long; the {subject} asks for {expected_s} s")
    return got_width, got_height, duration, size


def _frame_rate(video: Mapping[str, object]) -> float | None:
    """The average frame rate of a probed video stream (ffprobe writes it as ``24/1``), or None."""
    match = re.fullmatch(r"(\d+)/(\d+)", str(video.get("avg_frame_rate", "")))
    if match is None or int(match[2]) == 0:
        return None
    return int(match[1]) / int(match[2])


def _kill_group(process: subprocess.Popen) -> None:
    """Kill the render's process group (the CLI, Chrome workers, ffmpeg) and reap it."""
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        logger.warning("render process %s did not exit after SIGKILL", process.pid)


def _page_errors(output: str) -> list[str]:
    """The errors the page reported, each once, in the order printed."""
    return list(dict.fromkeys(m[1].strip() for m in _PAGE_ERROR.finditer(output)))


def _tail(text: str) -> str:
    text = " ".join(text.split())
    return text[-_LOG_TAIL_CHARS:]
