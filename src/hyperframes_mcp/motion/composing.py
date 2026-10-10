"""Compose a shot list into a HyperFrames project as a library call: the wiring ``compose`` and ``build`` share.

``compose_project`` loads the shot list and the narration's word timings, measures what ffprobe can (the voice, the
clips, the sound effects), runs ``compose.compose``, and writes the two files a person's tools read: ``VIDEO.md`` (the
notes for the desktop app's Claude Code) and ``video.project.json`` (``manifest.py``: the profile, the topic and the
sources the project was composed from). Nothing is printed: a check that could not run (ffprobe is missing) is a note,
and the shot list's problems are warnings, both returned as data for the caller to show. ``load_inputs`` is the first
half alone, for ``--check`` and ``build``, which write no project of their own.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache, partial
from pathlib import Path

from hyperframes_mcp.hfcli.cli import NODE_DIR
from hyperframes_mcp.motion import compose, hf_cli, sheet, timing
from hyperframes_mcp.motion.manifest import (
    MANIFEST_FILE,
    ProjectManifest,
    file_sha256,
    write_manifest,
)
from hyperframes_mcp.motion.project_edit import NOTES_FILE, project_notes
from hyperframes_mcp.motion.shotlist import ShotList, load_shotlist
from hyperframes_mcp.qa.media import (
    first_stream,
    format_duration,
    probe_media,
    resolve_tools,
    stream_duration,
)
from hyperframes_mcp.qa.models import QAToolError, Tools


@dataclass
class ComposeInputs:
    """What ``compose.plan`` and ``compose.compose`` take, read from files. ``notes`` say which checks could not run."""

    shotlist: ShotList
    alignment: list[timing.Word]
    voice_duration: float | None
    clip_seconds: compose.ClipLength | None
    notes: list[str]


@dataclass
class ComposedProject:
    """A project ``compose_project`` wrote.

    ``result`` is ``compose.compose``'s; ``files`` are relative to ``project_dir`` and include ``VIDEO.md`` and the
    manifest. ``notes`` are checks that could not run (the voice's, the clips' or the sound effects' lengths, when ffprobe
    is missing); the shot list's warnings are ``warnings``.
    """

    project_dir: Path
    result: compose.ComposeResult
    manifest: ProjectManifest
    files: list[str]
    notes: list[str]

    @property
    def warnings(self) -> list[str]:
        return self.result.warnings


def load_inputs(shotlist_path: Path, alignment_path: Path | None, assets: Path) -> ComposeInputs:
    """Read the shot list and the alignment (none: no cue can be resolved) and probe the voice's and the clips' lengths.

    Raises:
        ShotListError: the shot list cannot be read or breaks a rule of its own.
        timing.AlignmentError: the alignment cannot be read.
        UnreadableMediaError: ffprobe cannot read the voice.
    """
    shotlist = load_shotlist(shotlist_path)
    alignment = timing.load_alignment(alignment_path) if alignment_path else []
    notes: list[str] = []
    return ComposeInputs(
        shotlist=shotlist,
        alignment=alignment,
        voice_duration=_voice_duration(shotlist, assets, notes),
        clip_seconds=_clip_lengths(shotlist, notes),
        notes=notes,
    )


def compose_project(
    shotlist_path: Path,
    alignment_path: Path | None,
    out: Path,
    *,
    assets: Path,
    voice: Path | None = None,
    profile_name: str = "default",
    topic_id: int | None = None,
    node_dir: Path = NODE_DIR,
) -> ComposedProject:
    """Write the HyperFrames project of a shot list into ``out``, with its ``VIDEO.md`` and ``video.project.json``.

    ``voice`` is the narration file the manifest records (the shot list's own voice file in ``assets`` when not given);
    ``profile_name`` and ``topic_id`` say which profile's style and limits the project follows and which pipeline topic
    its spend is booked to (None outside a pipeline run); ``node_dir`` is where the pinned HyperFrames is installed, for
    the version the manifest records (None when it is not).

    Raises:
        ShotListError: the shot list breaks a rule (nothing is written).
        timing.AlignmentError: the alignment cannot be read.
        UnreadableMediaError: ffprobe cannot read the voice.
        registry.RegistryError: a block cannot be installed.
    """
    inputs = load_inputs(shotlist_path, alignment_path, assets)
    shotlist = inputs.shotlist
    result = compose.compose(
        shotlist,
        inputs.alignment,
        inputs.voice_duration,
        out,
        assets_dir=assets,
        clip_seconds=inputs.clip_seconds,
        sound_seconds=_sound_lengths(shotlist, inputs.notes),
    )
    # what the desktop app's Claude Code needs: it starts in the project folder, without this repository's rules
    (out / NOTES_FILE).write_text(project_notes(out, profile_name=profile_name), encoding="utf-8")
    manifest = ProjectManifest(
        profile=profile_name,
        topic_id=topic_id,
        title=shotlist.meta.title,
        shotlist=shotlist_path.resolve(),
        shotlist_sha256=file_sha256(shotlist_path),
        alignment=alignment_path.resolve() if alignment_path else None,
        assets=assets.resolve(),
        voice=(voice or assets / shotlist.audio.voice.path).resolve(),
        hyperframes_version=hf_cli.version(node_dir),
    )
    write_manifest(out, manifest)
    return ComposedProject(
        project_dir=result.project_dir.resolve(),
        result=result,
        manifest=manifest,
        files=[*result.files, NOTES_FILE, MANIFEST_FILE],
        notes=inputs.notes,
    )


def clip_probe(notes: list[str]) -> compose.ClipLength | None:
    """A probe of a clip's length (its video stream's), or None with a note appended to ``notes`` when ffprobe is
    missing."""
    tools = _media_tools("the clips' lengths are", notes)
    return None if tools is None else partial(_stream_length, "video", tools)


def _media_tools(what: str, notes: list[str]) -> Tools | None:
    """ffprobe's tools, or None with a note that ``what`` ("the voice's length is") is not checked."""
    try:
        return resolve_tools(sheet.media_environment())
    except QAToolError as error:
        notes.append(f"{what} not checked: {error}")
        return None


def _voice_duration(shotlist: ShotList, assets: Path, notes: list[str]) -> float | None:
    """The voice's length by ffprobe, or None when the file is not there yet (the plan reports that) or ffprobe is."""
    voice = assets / shotlist.audio.voice.path
    if not voice.is_file() or (tools := _media_tools("the voice's length is", notes)) is None:
        return None
    return format_duration(probe_media(voice, tools))


def _stream_length(kind: str, tools: Tools, path: Path) -> float:
    """A clip's or a sound's length in seconds: its ``kind`` stream's (video or audio), which is what plays (a container
    whose other stream runs on is longer). ValueError says why the file has none."""
    try:
        probe = probe_media(path, tools)
    except QAToolError as error:
        raise ValueError(str(error)) from error
    stream = first_stream(probe, kind)
    if stream is None:
        raise ValueError(f"it has no {kind} stream")
    seconds = stream_duration(stream) or format_duration(probe)
    if not seconds:
        raise ValueError("ffprobe gave it no length")
    return seconds


def _clip_lengths(shotlist: ShotList, notes: list[str]) -> compose.ClipLength | None:
    """A probe of a clip's length, or None when no layer names a clip or ffprobe is missing (a note says so)."""
    if not any(compose.CLIP_VARIABLE in layer.variables for shot in shotlist.shots for layer in shot.layers):
        return None
    return clip_probe(notes)


def _sound_lengths(shotlist: ShotList, notes: list[str]) -> compose.ClipLength | None:
    """A probe of a sound effect's length, once per file (only the Studio rows use it, so only a project that is kept
    asks), or None when there is no effect or ffprobe is missing."""
    if not shotlist.audio.sfx:
        return None
    tools = _media_tools("the sound effects' lengths are", notes)
    return None if tools is None else cache(partial(_stream_length, "audio", tools))
