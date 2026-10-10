"""The manifest of a composed project (``video.project.json``): which profile and topic it is for, and its sources.

``compose`` writes it beside ``index.html``; a project composed before it existed gets one from
``python -m hyperframes_mcp.server adopt``. The tools of the video MCP server read it to pick the profile's style and limits,
the cost ledger's topic and the narration's word timings. The source paths are absolute: a project folder is usually
outside the repository, and its sources (the shot list, the voice) stay where they were. Once the HyperFrames app has
edited ``index.html``, the page is the truth and the shot list may be stale; the manifest records only what the project
was composed from. ``meta.json`` stays the HyperFrames CLI's own file.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, ValidationError

from hyperframes_mcp.motion import tokens
from hyperframes_mcp.util.atomic import atomic_write_bytes

MANIFEST_FILE = "video.project.json"
SCHEMA_VERSION = 1


class ManifestError(ValueError):
    """The project has no manifest, or one that cannot be read."""


class Canvas(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    width: int = Field(gt=0)
    height: int = Field(gt=0)
    fps: int = Field(gt=0)


def default_canvas() -> Canvas:
    """The canvas of the motion library's tokens (1920x1080 at 24 fps)."""
    return Canvas(**tokens.default_tokens().canvas)


class ProjectManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = SCHEMA_VERSION
    # the profile whose style and limits the project follows; manifests written as ``channel_slug`` still load
    profile: str = Field(default="default", validation_alias=AliasChoices("profile", "channel_slug"))
    topic_id: int | None = None  # the pipeline topic, for the cost ledger; None outside a pipeline run
    title: str | None = None
    shotlist: Path | None = None
    shotlist_sha256: str | None = None
    alignment: Path | None = None  # the narration's word timings (a words.json)
    assets: Path | None = None
    voice: Path | None = None
    canvas: Canvas = Field(default_factory=default_canvas)
    hyperframes_version: str | None = None
    composed_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))


def file_sha256(path: Path) -> str:
    """The sha256 of a file's bytes, as hex."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_manifest(project: str | Path) -> ProjectManifest:
    """The manifest of ``project``.

    Raises:
        ManifestError: there is none, or it is not a valid manifest of this schema.
    """
    path = Path(project) / MANIFEST_FILE
    if not path.is_file():
        raise ManifestError(
            f"{Path(project)} has no {MANIFEST_FILE}; compose the project again, or adopt it with "
            "`python -m hyperframes_mcp.server adopt`"
        )
    try:
        manifest = ProjectManifest.model_validate_json(path.read_text(encoding="utf-8"))
    except ValidationError as error:
        raise ManifestError(f"{path} is not a valid manifest: {error}") from error
    if manifest.schema_version != SCHEMA_VERSION:
        raise ManifestError(
            f"{path} has schema version {manifest.schema_version}; this code reads version {SCHEMA_VERSION}"
        )
    return manifest


def write_manifest(project: str | Path, manifest: ProjectManifest) -> Path:
    """Write ``manifest`` into ``project`` in one step (the app may be watching the folder); returns its path."""
    path = Path(project) / MANIFEST_FILE
    atomic_write_bytes(path, (manifest.model_dump_json(indent=2) + "\n").encode("utf-8"))
    return path


def find_project(start: str | Path) -> Path | None:
    """The nearest folder at or above ``start`` that holds a manifest, or None."""
    here = Path(start).resolve()
    for folder in (here, *here.parents):
        if (folder / MANIFEST_FILE).is_file():
            return folder
    return None
