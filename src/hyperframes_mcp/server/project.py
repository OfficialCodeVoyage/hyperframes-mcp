"""Which project a tool call is about, and where in it a tool may write.

A call names its project, or the server takes the session's folder: Claude Code sets ``CLAUDE_PROJECT_DIR`` for the
servers it starts (the HyperFrames app's chat starts in the project folder), else the working folder. From there the
nearest folder holding a manifest (``video.project.json``) is the project, so a call from inside ``assets/`` works too.
Nothing is remembered between calls: every result names the project it used.
"""

from __future__ import annotations

import os
from pathlib import Path

from hyperframes_mcp.motion.manifest import ProjectManifest, find_project, read_manifest


class ProjectError(ValueError):
    """No project could be found, or a path leaves it."""


def resolve_project(project: str | None = None) -> tuple[Path, ProjectManifest]:
    """The project folder (resolved) and its manifest, for ``project`` or else the session's folder.

    Raises:
        ProjectError: no folder at or above the starting point holds a manifest.
        ManifestError: the project's manifest cannot be read.
    """
    start = project or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    root = find_project(Path(start).expanduser())
    if root is None:
        raise ProjectError(
            f"no video project at or above {start}: a composed project holds video.project.json "
            "(compose it again, or adopt it with `python -m hyperframes_mcp.server adopt`)"
        )
    return root, read_manifest(root)


def writable(root: Path, path: str | Path) -> Path:
    """``path`` (relative to the project, or absolute) resolved, if it stays inside ``root``.

    Raises:
        ProjectError: the path, links followed, leaves the project.
    """
    target = (root / path).resolve()
    if not target.is_relative_to(root.resolve()):
        raise ProjectError(f"{path} is outside the project {root}; a tool writes only inside it")
    return target
