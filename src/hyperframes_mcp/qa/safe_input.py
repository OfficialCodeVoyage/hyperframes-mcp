"""Checks for everything in an editor project that reaches an FFmpeg command line.

The editor state is client-supplied JSON. Its colours, numbers, styles and file paths end up
inside ``-filter_complex`` and ``-i`` arguments, so each one is accepted only in a form that
cannot change what FFmpeg does: colours as hex (``#rgb``, ``#rrggbb``, ``#rrggbbaa``) or a short
named list, finite bounded numbers, styles from a fixed list, and media files that resolve under
``data/`` or ``projects/``.

The same helpers run when the state is saved (the models in ``models.py``, the state route) and
when the command is built (``render_engine``, ``RenderService``), so a project row written by
anything other than the API is held to the same rules.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

# Filter option values and ``-i`` arguments are only ever built from these.
_HEX_COLOR = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
NAMED_COLORS: dict[str, str] = {
    "white": "#ffffff",
    "black": "#000000",
    "red": "#ff0000",
    "green": "#008000",
    "blue": "#0000ff",
    "yellow": "#ffff00",
    "cyan": "#00ffff",
    "magenta": "#ff00ff",
    "orange": "#ffa500",
    "purple": "#800080",
    "pink": "#ffc0cb",
    "gray": "#808080",
    "grey": "#808080",
    "gold": "#ffd700",
}

# FFmpeg reads "<scheme>:" (and "subfile,...:") at the start of an input name as a protocol.
_PROTOCOL_PREFIX = re.compile(r"^[A-Za-z0-9+.\-]+:")
CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")

MAX_PATH_LENGTH = 1024
# Track types whose clips are opened as FFmpeg inputs.
MEDIA_TRACK_TYPES = frozenset({"video", "audio", "music"})
LUT_ROOT = Path("data") / "luts"


class UnsafeProjectInput(ValueError):
    """A value in an editor project that must not reach an FFmpeg command line."""


def normalize_color(value: Any, field: str = "color") -> str:
    """A colour as ``#rrggbb`` or ``#rrggbbaa`` (``#rgb`` is expanded, names become hex).

    Raises:
        UnsafeProjectInput: not a string in one of the accepted forms.
    """
    if not isinstance(value, str):
        raise UnsafeProjectInput(f"{field}: expected a colour string")
    named = NAMED_COLORS.get(value.lower())
    if named is not None:
        return named
    if _HEX_COLOR.fullmatch(value) is None:
        raise UnsafeProjectInput(f"{field}: use #rgb, #rrggbb, #rrggbbaa or one of {', '.join(sorted(NAMED_COLORS))}")
    digits = value[1:]
    if len(digits) == 3:
        return "#" + "".join(ch * 2 for ch in digits)
    return value


def check_input_name(path: Any, field: str = "file_path") -> str:
    """A file name FFmpeg will open as a plain file, never as a protocol or a device.

    This is the check ``build_ffmpeg_args`` always makes. It does not look at where the file
    is: ``check_media_path`` does that.

    Raises:
        UnsafeProjectInput: not a string, control characters, or a protocol-looking name.
    """
    if not isinstance(path, str) or not path:
        raise UnsafeProjectInput(f"{field}: expected a file path")
    if len(path) > MAX_PATH_LENGTH or CONTROL_CHARS.search(path):
        raise UnsafeProjectInput(f"{field}: not a usable file path")
    if _PROTOCOL_PREFIX.match(path) or path.startswith("subfile,"):
        raise UnsafeProjectInput(
            f"{field}: URLs and FFmpeg protocols are not allowed, use a file under data/ or projects/"
        )
    return path


def default_media_roots() -> tuple[Path, ...]:
    """The folders media may live in: ``data/`` and ``projects/`` under the working directory."""
    return (Path("data"), Path("projects"))


def _resolved_roots(roots: Sequence[Path] | None) -> tuple[Path, ...]:
    return tuple(root.resolve() for root in (roots or default_media_roots()))


def check_media_path(
    path: Any,
    field: str = "file_path",
    roots: Sequence[Path] | None = None,
) -> Path:
    """The resolved path of a media file that lies under one of ``roots``.

    Symlinks are followed before the check, so a link inside a root that points outside it is
    refused. The file need not exist yet.

    Raises:
        UnsafeProjectInput: a protocol-looking name, or the file resolves outside the roots.
    """
    return _resolve_under(path, field, _resolved_roots(roots))


def _resolve_under(path: Any, field: str, allowed: Sequence[Path]) -> Path:
    """``check_media_path`` for roots that are already resolved (once per project, not per clip)."""
    check_input_name(path, field)
    try:
        resolved = Path(path).resolve()
    except (OSError, RuntimeError) as exc:  # a symlink loop, an unreadable component
        raise UnsafeProjectInput(f"{field}: cannot resolve {path!r}") from exc
    if not any(resolved.is_relative_to(root) for root in allowed):
        raise UnsafeProjectInput(f"{field}: {path!r} is outside {' and '.join(root.name + '/' for root in allowed)}")
    return resolved


def check_lut_name(path: Any, field: str = "custom_lut_path") -> str:
    """A LUT path as the editor may store it: a ``.cube`` file name FFmpeg opens as a file."""
    check_input_name(path, field)
    if not path.lower().endswith(".cube"):
        raise UnsafeProjectInput(f"{field}: only .cube files are accepted")
    return path


def check_project_media(state: Any, roots: Sequence[Path] | None = None) -> None:
    """Every media file and the LUT of an ``EditorProjectState`` lie under the allowed roots.

    Raises:
        UnsafeProjectInput: naming the clip or setting at fault.
    """
    allowed = _resolved_roots(roots)
    for track in state.tracks:
        if track.track_type not in MEDIA_TRACK_TYPES:
            continue
        for clip in track.clips:
            if clip.file_path:
                _resolve_under(clip.file_path, f"clip {clip.clip_id} file_path", allowed)
    grade = state.color_grade or {}
    lut = grade.get("custom_lut_path")
    if lut:
        check_lut_name(lut)
        _resolve_under(lut, "custom_lut_path", _resolved_roots(roots or (LUT_ROOT,)))


# --------------------------------------------------------------------------- .cube LUT files

CUBE_MIN_SIZE = 2
CUBE_MAX_SIZE = 256
CUBE_MAX_LINE = 200
_FLOAT = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
_CUBE_ROW = re.compile(rf"^{_FLOAT}(?:\s+{_FLOAT}){{2}}$")
_CUBE_DOMAIN = re.compile(rf"^DOMAIN_(?:MIN|MAX)(?:\s+{_FLOAT}){{3}}$")
_CUBE_SIZE_LINE = re.compile(r"^LUT_3D_SIZE\s+(\S+)$")
_CUBE_TITLE = re.compile(r"^TITLE\s.*$")


def check_cube_lut(content: bytes) -> int:
    """Check that ``content`` is a 3D ``.cube`` LUT and return its size.

    Accepted lines: ``#`` comments, blank lines, ``TITLE``, ``DOMAIN_MIN`` / ``DOMAIN_MAX``
    with three numbers, one ``LUT_3D_SIZE n`` (2 to 256), and at least ``n**3`` rows of three
    numbers. Anything else is refused, so a file that passes is data for ``lut3d`` and nothing
    else.

    Raises:
        UnsafeProjectInput: saying what is wrong.
    """
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UnsafeProjectInput("Invalid LUT file: not UTF-8 text") from exc
    if "\x00" in text or not re.search(r"^\s*LUT_3D_SIZE\b", text, re.MULTILINE):
        raise UnsafeProjectInput("Invalid LUT file: missing LUT_3D_SIZE header")

    size: int | None = None
    rows = 0
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if len(line) > CUBE_MAX_LINE:
            raise UnsafeProjectInput(f"Invalid LUT file: line {number} is too long")
        if not line or line.startswith("#"):
            continue
        header = _CUBE_SIZE_LINE.match(line)
        if header and size is None and rows == 0:
            if not header.group(1).isdigit() or not (CUBE_MIN_SIZE <= int(header.group(1)) <= CUBE_MAX_SIZE):
                raise UnsafeProjectInput(
                    f"Invalid LUT file: the cube size must be a whole number from {CUBE_MIN_SIZE} to {CUBE_MAX_SIZE}"
                )
            size = int(header.group(1))
        elif _CUBE_ROW.match(line):
            rows += 1
        elif (_CUBE_TITLE.match(line) or _CUBE_DOMAIN.match(line)) and rows == 0:
            continue
        else:
            raise UnsafeProjectInput(f"Invalid LUT file: line {number} is not a cube LUT line")
    if size is None:
        raise UnsafeProjectInput("Invalid LUT file: missing LUT_3D_SIZE header")
    if rows < size**3:
        raise UnsafeProjectInput(f"Invalid LUT file: {rows} data rows, a size {size} cube needs {size**3}")
    return size
