"""The tools that check a project and show it: ``run_checks``, ``get_frame`` and ``contact_sheet``.

A frame is what the HyperFrames app's own preview shows (``preview.frame``: it renders the project as it is on disk), so
the app must be running with the project open; no headless browser is run here, nothing is rendered and nothing is
paid. A frame and a sheet come back as JPEG image blocks no wider than 1000 px. The checks read the page and run the
pinned CLI's lint on it.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver import Image
from mcp.types import ToolAnnotations

from hyperframes_mcp.motion import frames, page_checks
from hyperframes_mcp.server import edits
from hyperframes_mcp.server.bridge import AppBridge, AppError
from hyperframes_mcp.server.errors import reported
from hyperframes_mcp.server.project import resolve_project

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)


async def _app_frame(bridge: AppBridge, root: Path, time: float, folder: Path) -> Path:
    """The PNG of the page at ``time``, from the app's ``preview.frame``.

    Raises:
        frames.FrameError: ``time`` is not inside the video, the app is not running with the project open, or it gave
            no frame.
    """
    frames.check_time(root, time)
    project_id = await edits.app_project(bridge, root)
    if project_id is None:
        raise frames.FrameError(
            "the HyperFrames app did not answer or does not have this project open: open the project in the app, "
            "because frames are what its preview shows (try again if it is open: it may be busy loading the page)"
        )
    try:
        png = folder / f"app-frame-{time:g}.png"
        png.write_bytes(await bridge.preview_frame(project_id, time))
    except AppError as error:
        raise frames.FrameError(f"the app gave no frame at {time:g} s: {error}") from error
    return png


def register(server: MCPServer, bridge: AppBridge) -> None:
    """Add the review tools to ``server``; a frame comes from the HyperFrames app through ``bridge``."""

    @server.tool(annotations=READ_ONLY)
    @reported
    def run_checks(project: str | None = None) -> dict[str, Any]:
        """Check the page after an edit, as the app's own check cannot (it samples a few moments of the whole video
        and can miss the edited spot): every picture has a true credit line on screen with it, no layer is hidden
        behind one that covers the frame (a credit above all), and the HyperFrames lint. Nothing is drawn: to see the
        result, call get_frame. Gives each finding's severity, layer id, time and what to fix, errors first, and one ok
        line when there is nothing.
        """
        root, _ = resolve_project(project)
        return page_checks.run_checks(root)

    @server.tool(annotations=READ_ONLY)
    @reported
    async def get_frame(time: float, project: str | None = None) -> list[str | Image]:
        """The frame of the video at ``time`` seconds as an image, from the HyperFrames app's own preview: the app must
        be running with the project open. Nothing is rendered or written. Look at it after an edit instead of cutting
        frames yourself.
        """
        root, _ = resolve_project(project)
        with tempfile.TemporaryDirectory(prefix="hyperframes-mcp-frame-") as folder:
            png = await _app_frame(bridge, root, time, Path(folder))
            data = frames.jpeg(png)
        return [
            f"{root.name} at {time:g} s, from the app",
            Image(data=data, format="jpeg"),
        ]

    @server.tool(annotations=READ_ONLY)
    @reported
    async def contact_sheet(from_s: float, to_s: float, n: int = 12, project: str | None = None) -> list[str | Image]:
        """One image of ``n`` frames (up to 16) spread evenly from ``from_s`` to ``to_s`` seconds, each labelled with
        its time, left to right and top to bottom: to review a stretch at a glance. The frames are the app's preview, one
        after another (about 2 to 3 seconds each), so ask for as few as the review needs; the app must be running with
        the project open.
        """
        root, _ = resolve_project(project)
        times = frames.sheet_times(root, from_s, to_s, n)
        with tempfile.TemporaryDirectory(prefix="hyperframes-mcp-sheet-") as folder:
            pngs = [await _app_frame(bridge, root, t, Path(folder)) for t in times]
            data = frames.contact_sheet(list(zip(times, pngs, strict=True)), Path(folder))
        caption = f"{root.name}: {len(times)} frames from {times[0]:g} s to {times[-1]:g} s, from the app"
        return [caption, Image(data=data, format="jpeg")]
