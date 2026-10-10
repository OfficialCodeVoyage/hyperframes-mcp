"""Vector graphics for a composed video: icons, arrows, seals and stamps that stay sharp, can be recoloured and animated.

Three free sources, one gate:

- ``search_icons`` finds one in the vendored subset of three open icon sets (``assets/icons``: Maki CC0, Tabler MIT,
  Material Symbols Apache-2.0), each with its licence;
- ``vectorize`` traces a picture of the project into paths on this machine, with the optional ``vtracer`` package;
- an SVG the chat wrote itself comes in as text.

Every SVG goes through ``check_svg`` before it is saved under ``assets/media/vectors/`` with a record beside it
(``<name>.svg.json``: source, licence, credit). The check is strict, as the app's agent runs without prompts and the
file is later read into the page by the ``graphic-stamp`` block: only drawing elements, no script, no style, no outside
reference, no raster, a ``viewBox``, a size and a count limit, and colours from the palette of ``tokens.json`` or
``currentColor`` (so the block can recolour it). The server makes no model call: the chat's own model writes the SVG.
"""

from __future__ import annotations

import hashlib
import importlib
import io
import json
import re
from dataclasses import dataclass
from functools import cache
from importlib import metadata
from pathlib import Path
from typing import Any

from defusedxml import ElementTree as safe_xml
from defusedxml.common import DefusedXmlException
from PIL import Image

from hyperframes_mcp.motion import tokens
from hyperframes_mcp.motion.compose import MEDIA_DIR
from hyperframes_mcp.util.atomic import atomic_write_bytes

VECTORS_DIR = f"{MEDIA_DIR}/vectors"
ICONS_DIR = tokens.MOTION_DIR.parent / "icons"
MAX_BYTES = 100_000
MAX_ELEMENTS = 1000
MAX_HITS = 20
# how many problems of one SVG are told at once; the rest is counted
MAX_PROBLEMS = 8
# a picture is scaled to fit this many pixels on its long edge before it is traced
TRACE_MAX_EDGE = 1000
COLOR_MODES = {"mono": "binary", "palette": "color"}
# vtracer's own defaults after the colour mode (hierarchical, mode, filter_speckle, color_precision, layer_difference,
# corner_threshold, length_threshold, max_iterations, splice_threshold, path_precision), except a speckle filter of 8
# and one decimal in a path. They are passed by position: vtracer 0.6.15 crashes the whole process (a segfault) on any
# keyword argument, seen on Python 3.14 on an M-series Mac, 2026-10-07.
TRACE_ARGUMENTS = ("stacked", "spline", 8, 6, 16, 60, 4.0, 10, 45, 1)
INSTALL_HINT = (
    "vectorize needs the optional vtracer package: ./venv/bin/pip install 'vtracer>=0.6' "
    "(it is not in requirements.txt)"
)
INTERNAL_LICENCE = "LicenseRef-claude-written"

SVG_NS = "http://www.w3.org/2000/svg"
_XLINK = "http://www.w3.org/1999/xlink"
_XML = "http://www.w3.org/XML/1998/namespace"
# the drawing elements; no text (fonts), image, style, filter, link, animation or script
ALLOWED_TAGS = frozenset(
    [
        "svg",
        "g",
        "defs",
        "symbol",
        "use",
        "path",
        "circle",
        "ellipse",
        "line",
        "polyline",
        "polygon",
        "rect",
        "linearGradient",
        "radialGradient",
        "stop",
        "clipPath",
        "mask",
        "title",
        "desc",
    ]
)
_COLOUR_ATTRIBUTES = frozenset({"fill", "stroke", "stop-color", "flood-color", "lighting-color", "color"})
_COLOUR_WORDS = frozenset({"none", "transparent", "currentcolor", "inherit"})
_URL = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.IGNORECASE)
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
_HEX = re.compile(r"#[0-9a-fA-F]{6}")
_FILL_OR_STROKE = re.compile(r'\b(fill|stroke|stop-color)="(#[0-9a-fA-F]{6})"')


class VectorError(ValueError):
    """The vector cannot be made or saved; the message says what to change, and nothing was written."""


@dataclass(frozen=True)
class Icon:
    """One icon of the vendored subset; ``id`` (``set/name``) is what ``save_vector`` takes."""

    set: str
    name: str
    tags: tuple[str, ...]
    licence: str
    path: Path

    @property
    def id(self) -> str:
        return f"{self.set}/{self.name}"


@dataclass(frozen=True)
class Saved:
    """A vector saved in a project: its path relative to the project, where it came from and under what licence."""

    path: str
    source: str
    licence: str | None
    credit: str | None
    bytes: int


# -- the check --------------------------------------------------------------------------------------


def check_svg(svg: str) -> None:
    """Refuse an SVG that could run code, leave the file, hide a raster, break a limit or use a colour off the palette.

    Raises:
        VectorError: listing the problems found (up to ``MAX_PROBLEMS``), so they can be fixed together.
    """
    problems = _problems(svg)
    if problems:
        more = len(problems) - MAX_PROBLEMS
        shown = problems[:MAX_PROBLEMS] + ([f"and {more} more"] if more > 0 else [])
        raise VectorError("the SVG is refused:\n- " + "\n- ".join(shown))


def _problems(svg: str) -> list[str]:
    size = len(svg.encode("utf-8"))
    if size > MAX_BYTES:
        return [f"{size // 1000} KB is over the {MAX_BYTES // 1000} KB limit: draw it simpler, with fewer points"]
    try:
        root = safe_xml.fromstring(svg.strip(), forbid_dtd=True)
    except DefusedXmlException:
        return ["a DOCTYPE or entity declaration is not allowed"]
    except safe_xml.ParseError as error:
        return [f"not valid XML: {error}"]
    if root.tag != f"{{{SVG_NS}}}svg":
        return [f'the root must be <svg> in the SVG namespace (xmlns="{SVG_NS}"), not <{_local(root.tag)}>']
    problems: list[str] = []
    if not _view_box_ok(root.get("viewBox")):
        problems.append(
            'the root <svg> needs a viewBox of four numbers with a width and height above 0, e.g. viewBox="0 0 24 24"'
        )
    elements = list(root.iter())
    if len(elements) > MAX_ELEMENTS:
        problems.append(f"{len(elements)} elements is over the {MAX_ELEMENTS} elements limit")
    by_id = {e.get("id"): e for e in elements if e.get("id")}
    ids = set(by_id)
    for element in elements:
        name = _local(element.tag)
        if element.tag != f"{{{SVG_NS}}}{name}" or name not in ALLOWED_TAGS:
            problems.append(_tag_problem(name))
        for key, value in element.attrib.items():
            problems += _attribute_problems(key, value, ids)
        if name == "use":
            problems += _reuse_problems(element, by_id)
    problems = list(dict.fromkeys(problems))
    if any("is not in the palette" in p for p in problems):
        palette = ", ".join(f"{name} {value}" for name, value in tokens.default_tokens().colors.items())
        problems.append(f"use currentColor (the block colours it) or a palette colour: {palette}")
    if any(p.endswith("is not allowed") and p.startswith("<") for p in problems):
        problems.append(f"the elements allowed: {', '.join(sorted(ALLOWED_TAGS))}")
    return problems


def _reuse_problems(use: Any, by_id: dict[Any, Any]) -> list[str]:
    """A ``<use>`` may not point at an element that holds another ``<use>`` (or is one): reuse that nests doubles at every
    level and could make a page too heavy to draw."""
    href = use.get("href") or use.get(f"{{{_XLINK}}}href") or ""
    target = by_id.get(href.strip()[1:])
    if target is not None and any(_local(e.tag) == "use" for e in target.iter()):
        return [f"{href}: a <use> may not point at an element that holds another <use>"]
    return []


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _view_box_ok(value: str | None) -> bool:
    parts = re.split(r"[\s,]+", (value or "").strip())
    try:
        numbers = [float(p) for p in parts]
    except ValueError:
        return False
    return len(numbers) == 4 and numbers[2] > 0 and numbers[3] > 0


def _tag_problem(name: str) -> str:
    if name == "image":
        return "<image> is not allowed: an embedded raster is not a vector (the vectorize tool traces a picture into paths)"
    return f"<{name}> is not allowed"


def _attribute_problems(key: str, value: str, ids: set[str | None]) -> list[str]:
    if key.startswith("{"):
        namespace, local = key[1:].split("}", 1)
        if (namespace, local) == (_XLINK, "href"):
            key = "href"
        elif namespace == _XML and local in ("space", "lang"):
            return []
        else:
            return [f"the attribute {local} of a foreign namespace is not allowed"]
    problems: list[str] = []
    lowered = key.lower()
    if lowered.startswith("on"):
        problems.append(f"event attribute {key} is not allowed")
    if lowered == "style":
        problems.append("a style attribute is not allowed: use the fill and stroke attributes")
    if lowered == "href" and not (value.strip().startswith("#") and value.strip()[1:] in ids):
        problems.append(f'href="{value[:60]}" is not allowed: only #id of an element of this file')
    for scheme in ("javascript:", "data:"):
        if scheme in value.lower().replace(" ", ""):
            problems.append(f"{key} contains {scheme}, which is not allowed")
    for _, target in _URL.findall(value):
        if not target.startswith("#"):
            problems.append(f"url({target[:60]}) leaves the file: only url(#id) is allowed")
        elif target[1:] not in ids:
            problems.append(f"url({target}): no element has id {target[1:]}")
    if lowered in _COLOUR_ATTRIBUTES and not _palette_colour(value):
        problems.append(f'{key}="{value}" is not in the palette')
    return problems


def _palette_colour(value: str) -> bool:
    """Whether ``value`` is a keyword that takes no colour of its own, a reference, or a colour of the palette."""
    low = value.strip().lower()
    if low in _COLOUR_WORDS or low.startswith("url("):
        return True
    return bool(_HEX.fullmatch(low)) and low.upper() in {c.upper() for c in tokens.default_tokens().colors.values()}


# -- the icon subset --------------------------------------------------------------------------------


@cache
def _icons(icons_dir: Path) -> tuple[Icon, ...]:
    data = json.loads((icons_dir / "manifest.json").read_text(encoding="utf-8"))
    return tuple(
        Icon(
            set=entry["set"],
            name=entry["name"],
            tags=tuple(entry["tags"]),
            licence=data["sets"][entry["set"]]["licence"],
            path=icons_dir / entry["set"] / f"{entry['name']}.svg",
        )
        for entry in data["icons"]
    )


def search_icons(query: str, limit: int = 8, *, icons_dir: Path = ICONS_DIR) -> list[Icon]:
    """The icons whose name or tags match every word of ``query``, best first (a word of the name beats a tag).

    At most ``limit`` (never above ``MAX_HITS``); nothing for an empty query.
    """
    words = re.findall(r"[a-z0-9]+", query.lower())
    if not words or limit < 1:
        return []
    ranked = []
    for icon in _icons(icons_dir):
        name = icon.name.lower()
        parts = re.findall(r"[a-z0-9]+", name)
        score = 0
        for word in words:
            points = max(
                4 if word in parts else 0,
                3 if word in name else 0,
                2 if word in icon.tags else 0,
                1 if any(word in tag for tag in icon.tags) else 0,
            )
            if not points:
                break
            score += points
        else:
            ranked.append((-score, len(icon.name), icon.id, icon))
    ranked.sort(key=lambda row: row[:3])
    return [row[3] for row in ranked[: min(limit, MAX_HITS)]]


# -- saving -----------------------------------------------------------------------------------------


def save_vector(project: str | Path, svg: str, name: str, *, icons_dir: Path = ICONS_DIR) -> Saved:
    """Save ``svg`` (markup, or an icon id from ``search_icons``) as ``assets/media/vectors/<name>.svg`` in ``project``.

    The SVG goes through ``check_svg`` first; a record (``<name>.svg.json``) with its source and licence is written
    beside it. A name that exists is never overwritten.

    Raises:
        VectorError: the name, the icon id or the SVG is not acceptable; nothing was written.
    """
    _check_name(name)
    if svg.lstrip().startswith("<"):
        text, record = svg, {"source": "claude-written", "licence": INTERNAL_LICENCE}
    else:
        icon = next((i for i in _icons(icons_dir) if i.id == svg.strip()), None)
        if icon is None:
            raise VectorError(
                f"{svg.strip()[:60]!r} is neither SVG markup (it starts with <svg) nor an icon id; "
                "search_icons finds icons, and its id (set/name) goes here"
            )
        sets = json.loads((icons_dir / "manifest.json").read_text(encoding="utf-8"))["sets"]
        text = icon.path.read_text(encoding="utf-8")
        record = {
            "source": icon.set,
            "licence": icon.licence,
            "notice": sets[icon.set]["notice"],
            "icon": icon.id,
        }
    check_svg(text)
    return _write(Path(project), name, text, record)


def _check_name(name: str) -> None:
    if not _NAME.fullmatch(name):
        raise VectorError(f"name {name!r}: use letters, digits, - and _ only (up to 64), no folder or suffix")


def _write(project: Path, name: str, text: str, record: dict[str, Any]) -> Saved:
    target = project / VECTORS_DIR / f"{name}.svg"
    if target.exists():
        raise VectorError(
            f"{VECTORS_DIR}/{name}.svg exists already and is never overwritten: save it under another name"
        )
    data = (text.strip() + "\n").encode("utf-8")
    meta = {
        "credit": None,
        **record,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    # the record first: the svg is what makes the name taken
    atomic_write_bytes(
        target.with_name(target.name + ".json"),
        (json.dumps(meta, indent=2) + "\n").encode("utf-8"),
    )
    atomic_write_bytes(target, data)
    return Saved(
        f"{VECTORS_DIR}/{name}.svg",
        meta["source"],
        meta.get("licence"),
        meta["credit"],
        len(data),
    )


# -- vectorizing ------------------------------------------------------------------------------------


def vectorize(project: str | Path, image: str, name: str, *, colors: str = "mono") -> Saved:
    """Trace the picture ``image`` (a path in ``project``) into a vector saved as ``name``, with ``vtracer`` (free, local).

    ``colors`` is ``mono`` (one shape colour, ``currentColor``, which the block recolours) or ``palette`` (the
    picture's colours snapped to the nearest palette colour). The picture's credit and licence, if its record has
    them, go to the vector's record. The trace goes through ``check_svg`` like any other SVG.

    Raises:
        VectorError: vtracer is not installed, the picture is missing or unreadable, or the trace is refused.
    """
    _check_name(name)
    if colors not in COLOR_MODES:
        raise VectorError(
            f"colors {colors!r}: use mono (one colour the block can recolour) or palette (the colours of the "
            "picture snapped to the palette)"
        )
    tracer = _tracer()
    root = Path(project).resolve()
    source = (root / image).resolve()
    if not source.is_relative_to(root) or not source.is_file():
        raise VectorError(f"no picture {image} in the project {root}")
    traced = tracer.convert_raw_image_to_svg(_png(source, image), "png", COLOR_MODES[colors], *TRACE_ARGUMENTS)
    svg = _as_vector(traced, colors)
    try:
        check_svg(svg)
    except VectorError as error:
        raise VectorError(
            f"the trace of {image} is not usable: {error}\nA simpler or smaller picture traces to less."
        ) from error
    return _write(root, name, svg, _trace_record(source, image))


def _tracer() -> Any:
    try:
        return importlib.import_module("vtracer")
    except ImportError as error:
        raise VectorError(INSTALL_HINT) from error


def _png(source: Path, shown: str) -> bytes:
    """The picture as PNG bytes, flattened on white and scaled down to ``TRACE_MAX_EDGE``."""
    try:
        with Image.open(source) as opened:
            picture = opened.convert("RGBA")
    except (OSError, Image.DecompressionBombError) as error:
        raise VectorError(f"{shown} cannot be read as a picture: {error}") from error
    flat = Image.new("RGB", picture.size, "white")
    flat.paste(picture, mask=picture.getchannel("A"))
    flat.thumbnail((TRACE_MAX_EDGE, TRACE_MAX_EDGE), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    flat.save(buffer, format="PNG")
    return buffer.getvalue()


def _as_vector(traced: str, colors: str) -> str:
    """vtracer's SVG with a viewBox in place of its width and height, black as ``currentColor`` (mono), or each colour
    snapped to the nearest palette colour (palette)."""
    text = re.sub(r"<\?xml[^>]*\?>\s*", "", traced).strip()
    try:
        root = safe_xml.fromstring(text, forbid_dtd=True)
    except (DefusedXmlException, safe_xml.ParseError) as error:
        raise VectorError(f"the tracer gave an SVG that cannot be read: {error}") from error
    try:
        width, height = float(root.get("width", "")), float(root.get("height", ""))
    except ValueError as error:
        raise VectorError("the tracer gave an SVG without a size") from error
    body = text[text.index(">", text.index("<svg")) + 1 :]
    if colors == "mono":
        # a black and white trace has black shapes only
        body = _FILL_OR_STROKE.sub(r'\1="currentColor"', body)
    else:
        # the white of the picture (and of a transparent ground, flattened on white) is the paper: no shape for it
        body = re.sub(r'<path\b[^>]*\bfill="#FFFFFF"[^>]*/>\s*', "", body, flags=re.IGNORECASE)
        body = _FILL_OR_STROKE.sub(lambda m: f'{m[1]}="{_nearest(m[2])}"', body)
    return f'<svg xmlns="{SVG_NS}" viewBox="0 0 {width:g} {height:g}">{body}'


def _nearest(colour: str) -> str:
    """The palette colour closest to ``colour`` (a ground or backing colour, which is never drawn, is not a choice)."""
    t = tokens.default_tokens()
    rgb = [int(colour[i : i + 2], 16) for i in (1, 3, 5)]

    def distance(hex_value: str) -> int:
        other = [int(hex_value[i : i + 2], 16) for i in (1, 3, 5)]
        return sum((a - b) ** 2 for a, b in zip(rgb, other, strict=True))

    choices = [v for k, v in t.colors.items() if k not in t.decorative]
    return min(choices, key=distance).upper()


def _trace_record(source: Path, shown: str) -> dict[str, Any]:
    try:
        version = f"vtracer {metadata.version('vtracer')}"
    except metadata.PackageNotFoundError:
        version = "vtracer"
    try:
        theirs = json.loads(source.with_name(source.name + ".json").read_text("utf-8"))
    except (OSError, ValueError):
        theirs = {}
    theirs = theirs if isinstance(theirs, dict) else {}
    return {
        "source": "vectorized",
        "derived_from": shown,
        "tool": version,
        "licence": theirs.get("licence"),
        "credit": theirs.get("credit"),
    }
