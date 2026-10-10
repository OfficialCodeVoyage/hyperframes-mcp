"""Edits of a composed project in place, by layer id: another picture or clip in a layer's place, with a true credit,
and one more layer, a vector graphic.

The project's ``index.html`` is the page the HyperFrames desktop app edits, so an edit here changes only what it must:
the layer's file variable (``image`` or ``video``) and its timeline label, and the text of the credit line over it. The
new file is copied into ``assets/media/edits/``, never over another file. Every picture or clip from outside needs a
credit: the person gives it, or the file's record (``<file>.json``, written by the server's ``confirm_spend`` and ``fetch_media``) brings it, and the record is copied beside the file. A graphic is added after the last mount of the page,
as a mount the composer's own emitter writes, in front of every layer it shares time with.
"""

from __future__ import annotations

import dataclasses
import html
import json
import math
import re
import shutil
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hyperframes_mcp.motion import credit_fit, preflight, tokens, vectors
from hyperframes_mcp.motion import registry as registry_module
from hyperframes_mcp.motion.compose import (
    ASSET_VARIABLES,
    COMPONENT_ROWS,
    MEDIA_DIR,
    ClipLength,
    LayerTiming,
    clip_label,
    mount_html,
)
from hyperframes_mcp.motion.preflight import credits_of
from hyperframes_mcp.motion.shotlist import CREDIT_BLOCK, DURATION_VARIABLE, EPS, value_problem
from hyperframes_mcp.providers.video.settings import load_i2v_settings
from hyperframes_mcp.util.atomic import atomic_write_bytes

EDITS_DIR = f"{MEDIA_DIR}/edits"
# the block a vector graphic is shown with; its registry entry says which options and slot lengths it takes
GRAPHIC = "graphic-stamp"
# the credit of a picture or clip made by the picture and animate commands (the profile's engraved style)
AI_CREDIT = "Engraving-style illustration, AI-generated"
PICTURE_QUALITIES = ("medium", "high")
# what a layer that can be replaced takes (its variable is the COMPONENT_ROWS one)
MEDIA = {
    "photo-shot": ("picture", frozenset({".png", ".jpg", ".jpeg", ".webp"})),
    "video-shot": ("clip", frozenset({".mp4", ".mov", ".webm", ".m4v"})),
}
# a start tag (attribute values may hold ">") and one double-quoted attribute, as the app writes them
_TAG = re.compile(r"""<div\b(?:[^>"']|"[^"]*"|'[^']*')*>""")
_ATTRIBUTE = re.compile(r'([\w:-]+)="([^"]*)"')
_Z_INDEX = re.compile(r"z-index\s*:\s*(-?\d+)")
_LAYER_ID = re.compile(r"l-(.+)-(\d+)")
# the project's notes for the desktop app's Claude Code: the app starts it with ``--setting-sources user``, so a
# project CLAUDE.md never loads there; the person's SessionStart hook (~/.claude/hooks/video-project-notes.js) adds
# this file instead
NOTES_FILE = "VIDEO.md"


class EditError(ValueError):
    """The edit cannot be made; the project is unchanged."""


@dataclass(frozen=True)
class Replaced:
    layer: str
    component: str
    old: str
    new: str
    credit_layer: str
    credit: str
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Added:
    layer: str
    shot: str
    start: float
    end: float
    vector: str
    row: int  # its Studio row
    z_index: int
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class BlockAdded:
    layer: str
    component: str
    shot: str
    start: float
    end: float
    row: int  # its Studio row
    z_index: int
    values: dict[str, Any]
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Arranged:
    layer: str
    component: str
    was: dict[str, float]  # dx and dy in px of the canvas from where the block puts itself, and scale
    now: dict[str, float]


@dataclass(frozen=True)
class Mount:
    tag: str  # the start tag as the page holds it
    layer: LayerTiming
    label: str  # its timeline label as the page holds it

    @property
    def id(self) -> str:
        return f"l-{self.layer.shot}-{self.layer.index}"


def replace_media(
    project: str | Path,
    layer_id: str,
    source: str | Path,
    *,
    credit: str | None = None,
    credit_layer: str | None = None,
    clip_seconds: ClipLength | None = None,
) -> Replaced:
    """Put ``source`` in the place of the picture or clip of ``layer_id`` and set the credit line over it.

    ``credit`` defaults to the one in the file's record; ``credit_layer`` picks the credit line when more than one is on
    screen with the layer; ``clip_seconds`` measures a clip, and a clip shorter than the layer's slot is a warning. A
    file already in the project's edits folder (where ``picture`` and ``animate`` write) is used where it is.

    Raises:
        EditError: the layer, the file or the credit does not allow the edit; nothing was written.
    """
    page_path = Path(project).resolve() / "index.html"
    page, done = replace_media_in(
        project,
        page_path.read_text(encoding="utf-8"),
        layer_id,
        source,
        credit=credit,
        credit_layer=credit_layer,
        clip_seconds=clip_seconds,
    )
    atomic_write_bytes(page_path, page.encode("utf-8"))
    return done


def replace_media_in(
    project: str | Path,
    page: str,
    layer_id: str,
    source: str | Path,
    *,
    credit: str | None = None,
    credit_layer: str | None = None,
    clip_seconds: ClipLength | None = None,
) -> tuple[str, Replaced]:
    """``replace_media`` on ``page`` (the text of ``index.html``): the new page and what changed. The new file is copied
    into the project, as there, but the page is not written, so the caller writes it where it must (through the app).
    Placing the same file again finds the copy already made.

    Raises:
        EditError: the layer, the file or the credit does not allow the edit; the page is unchanged.
    """
    project, source = Path(project).resolve(), Path(source).resolve()
    found = mounts(page)
    if layer_id not in found:
        raise EditError(f"no layer {layer_id!r} in index.html")
    picture = found[layer_id].layer
    if picture.component not in MEDIA:
        raise EditError(
            f"{layer_id} is a {picture.component}: only a photo-shot's picture or a video-shot's clip can be replaced"
        )
    kind, suffixes = MEDIA[picture.component]
    if source.suffix.lower() not in suffixes:
        raise EditError(
            f"{layer_id} is a {picture.component}, which takes a {kind} ({', '.join(sorted(suffixes))}), "
            f"not {source.name}"
        )
    if not source.is_file():
        raise EditError(f"no file {source}")
    record = _record_path(source)
    from_record = not credit
    credit = credit or (json.loads(record.read_text(encoding="utf-8")).get("credit") if record.is_file() else None)
    if not credit:
        raise EditError(f"say whose {kind} {source.name} is (credit); it shows on screen with it")
    credit_mount = found[_credit_layer(found, picture, credit_layer)]
    warnings = _short_clip(picture, source, clip_seconds) if kind == "clip" and clip_seconds is not None else []

    target = _place(project / EDITS_DIR, source)
    variable = COMPONENT_ROWS[picture.component][2]
    new = target.relative_to(project).as_posix()
    # a trim belongs to the old clip; the new one plays from its start
    values = {k: v for k, v in picture.values.items() if k != "trimStart"}
    page = rewrite_mount(page, found[layer_id], values | {variable: new})
    fit = _fit_credit(credit_mount, credit, picture, shorten=from_record)
    credit = fit.text
    credit_values = credit_mount.layer.values | {"text": credit}
    if fit.moved:
        credit_values |= {DURATION_VARIABLE: round(fit.end - fit.start, 3)}
    page = rewrite_mount(
        page,
        credit_mount,
        credit_values,
        duration=round(fit.end - fit.start, 3) if fit.moved else None,
        start=fit.start if fit.moved else None,
    )
    notes = fit.notes
    old = str(picture.values.get(variable, ""))
    if old == new:
        notes = notes + [
            (
                f"{target.name} was already in {layer_id}: to change only the credit, set_variable on "
                f"{credit_mount.id} (name text) does it"
            )
        ]
    warnings += slot_problems(layers_from_page(page), {credit_mount.id})
    return page, Replaced(
        layer_id,
        picture.component,
        old,
        new,
        credit_mount.id,
        credit,
        warnings,
        notes,
    )


@dataclass(frozen=True)
class _Fit:
    """Where a credit line is shown and with which text so that its block can draw it."""

    text: str
    start: float
    end: float
    moved: bool
    notes: list[str]


def _minimum_seconds(credit: LayerTiming, text: str) -> float | None:
    """What the credit-line block itself says it needs for ``text`` (entrance, the reading time of its words, exit), or
    None when that cannot be asked."""
    try:
        (result,) = preflight.minimum_slots(
            [(credit.component, credit.values | {"text": text})],
            registry_module.default_registry(),
        )
    except (preflight.PreflightUnavailable, registry_module.RegistryError, ValueError):
        return None
    return result.seconds


def _fit_credit(credit_mount: Mount, text: str, picture: LayerTiming, *, shorten: bool) -> _Fit:
    """The slot and text a credit needs when its layer is too short for ``text`` (a block given less than its minimum
    throws and shows nothing). A credit shows with its picture, never outside it, so it grows inside the picture's span:
    later first (it keeps its start), else by starting earlier (it keeps its end, the picture's; a credit that runs on
    past the picture may belong to what follows, so it is never pulled back inside). Failing that, and
    only for a credit that came from a file's record (never for words a person gave), the next shorter true form of
    the text is tried the same way. When nothing fits the layer is left as it is and the caller warns with the time it
    needs."""
    credit = credit_mount.layer
    keep = _Fit(text, credit.start, credit.end, False, [])
    needed = _minimum_seconds(credit, text)
    if needed is None or credit.slot + preflight.TOLERANCE >= needed:
        return keep
    layer = f"l-{picture.shot}-{picture.index}"
    tries = [text, *(credit_fit.shorter_credits(text) if shorten else [])]
    for candidate in tries:
        needed = _minimum_seconds(credit, candidate)
        if needed is None:
            continue
        wanted = math.ceil(needed * 100 - preflight.TOLERANCE) / 100
        if credit.start + wanted <= picture.end + EPS:
            start, end = credit.start, round(credit.start + wanted, 3)
            how = (
                f"{credit_mount.id} (credit-line) was lengthened from {credit.slot:g} s to {wanted:g} s so its "
                f"text can be read; it still ends inside {layer}"
            )
        elif credit.end <= picture.end + EPS and picture.end - wanted >= picture.start - EPS:
            start, end = round(max(picture.end - wanted, picture.start), 3), picture.end
            how = (
                f"{credit_mount.id} (credit-line) now starts at {start:g} s (it was {credit.start:g} s) and ends "
                f"with {layer} so its text can be read"
            )
        else:
            continue
        notes = [how]
        if candidate != text:
            notes.append(
                f"{credit_mount.id}'s credit was shortened to {candidate!r} so it fits the time {layer} leaves it; "
                "the file's record keeps the full text"
            )
        return _Fit(candidate, start, end, True, notes)
    return keep


def add_graphic(
    project: str | Path,
    vector: str,
    start: float,
    duration: float,
    *,
    position: str = "top-left",
    size: str = "medium",
    animation: str = "stamp",
    color: str | None = None,
    registry: registry_module.Registry | None = None,
) -> Added:
    """Add one ``graphic-stamp`` layer that shows ``vector`` (a name saved by ``save_vector``, or a path of the project to
    an SVG) from ``start`` for ``duration`` seconds.

    The layer joins the shot on screen at ``start`` with the next id of that shot (``l-<shot>-<n>``), takes a z-index above
    every layer it shares time with, and a Studio row of a graphic that is free then, else a new one. ``color`` None keeps
    the block's own. The block is installed into the project if it is not there, and the page is written once, as
    ``replace_media`` writes it.

    Raises:
        EditError: the vector, an option or a time does not allow the edit; nothing was written.
        vectors.VectorError: the file is no longer an SVG that ``vectors.check_svg`` accepts.
    """
    page_path = Path(project).resolve() / "index.html"
    page, done = add_graphic_in(
        project,
        page_path.read_text(encoding="utf-8"),
        vector,
        start,
        duration,
        position=position,
        size=size,
        animation=animation,
        color=color,
        registry=registry,
    )
    atomic_write_bytes(page_path, page.encode("utf-8"))
    return done


def add_graphic_in(
    project: str | Path,
    page: str,
    vector: str,
    start: float,
    duration: float,
    *,
    position: str = "top-left",
    size: str = "medium",
    animation: str = "stamp",
    color: str | None = None,
    registry: registry_module.Registry | None = None,
) -> tuple[str, Added]:
    """``add_graphic`` on ``page`` (the text of ``index.html``): the new page and what was added. The block is installed
    into the project as there, but the page is not written, so the caller writes it where it must (through the app).
    Installing again keeps the block that is there.
    """
    project = Path(project).resolve()
    reg = registry or registry_module.default_registry()
    declared = {v["id"]: v for v in reg.items[GRAPHIC].variables}
    chosen = {
        "position": position,
        "size": size,
        "color": color,
        "animation": animation,
    }
    for name, value in chosen.items():
        allowed = [o["value"] for o in declared[name]["options"]]
        if value is not None and value not in allowed:
            raise EditError(f"{name} {value!r}: use one of {', '.join(allowed)}")
    file = _vector_file(project, vector)
    found = mounts(page)
    if not found:
        raise EditError("no layers in index.html")
    total = _video_seconds(page)
    slot = declared[DURATION_VARIABLE]
    if start < 0:
        raise EditError(f"{start:g} s is before the start of the video")
    if total is not None and start >= total - EPS:
        raise EditError(f"{start:g} s is past the end: the video is {total:g} s long")
    if duration < slot["min"] - EPS:
        raise EditError(
            f"a graphic needs at least {slot['min']:g} s (the entrance, a hold and the fade out), not {duration:g} s"
        )
    if duration > slot["max"] + EPS:
        raise EditError(f"a graphic stays at most {slot['max']:g} s, not {duration:g} s")
    start, end = round(start, 3), round(start + duration, 3)

    shot = _shot_at(found, start)
    index = 1 + max(m.layer.index for m in found.values() if m.layer.shot == shot)
    sharing = [m for m in found.values() if m.layer.start < end and start < m.layer.end]
    z_index = 1 + max((_attributes(m)[0] for m in sharing), default=0)
    row = _free_row(found, sharing)
    rel = file.relative_to(project).as_posix()
    values: dict = {
        DURATION_VARIABLE: round(end - start, 3),
        "svg": rel,
        "position": position,
        "size": size,
        **({"color": color} if color else {}),
        "animation": animation,
    }
    layer = LayerTiming(shot, index, GRAPHIC, start, end, 0, values)

    warnings = _graphic_warnings(layer, file, total)
    warnings += _corner_warnings(f"l-{shot}-{index}", position, sharing)
    warnings += _fade_warning(page, shot, f"l-{shot}-{index}", end)
    report = registry_module.install_item(GRAPHIC, project, registry=reg)
    warnings += [
        f"kept {path.relative_to(project).as_posix()}: changed since the last install (install with force to replace it)"
        for path in report.kept
    ]
    page = _insert_after_last(page, list(found.values())[-1], mount_html(layer, row, z_index))
    return page, Added(f"l-{shot}-{index}", shot, start, end, rel, row, z_index, warnings)


# blocks that need a file or data a shot list composes, or have a tool of their own: not added by ``add_block``
_NOT_ADDED = {
    "photo-shot": "a picture or clip fills a layer with a file and needs a credit: replace_media puts a file in a layer "
    "that exists",
    "video-shot": "a picture or clip fills a layer with a file and needs a credit: replace_media puts a file in a layer "
    "that exists",
    "graphic-stamp": "a vector graphic is placed with add_graphic, which checks the vector",
    "finish": "the finish layer belongs to the composed video",
    "map-route": "a map needs the map data a shot list composes",
}


def addable_blocks(reg: registry_module.Registry) -> list[str]:
    """The blocks ``add_block`` can add: the library's blocks that take no file and have no tool of their own."""
    return sorted(
        name
        for name, item in reg.items.items()
        if item.type == "hyperframes:block"
        and name in COMPONENT_ROWS
        and name not in _NOT_ADDED
        and not any(v["id"] in ASSET_VARIABLES for v in item.variables)
    )


def add_block_in(
    project: str | Path,
    page: str,
    component: str,
    start: float,
    duration: float,
    values: dict[str, Any] | None = None,
    registry: registry_module.Registry | None = None,
    install: bool = True,
) -> tuple[str, BlockAdded]:
    """One more layer on ``page`` (the text of ``index.html``): the block ``component`` from ``start`` for ``duration``
    seconds, its variables ``values``, each checked as a shot list's are: every text and number variable is given (a
    block's default is a sample and never goes on screen), the others take the block's own defaults.

    The layer joins the shot on screen at ``start`` with the next id of that shot (``l-<shot>-<n>``), takes a z-index in front of
    every layer it shares time with and a Studio row of its kind that is free then, else a new one. The block is
    installed into the project if it is not there (not with ``install`` false, which only checks); the page is not
    written, so the caller writes it where it must (through the app). Warnings name what is wrong with the result without refusing it: a text too long for the time
    given, a layer the new one now hides.

    Raises:
        EditError: the block, a variable, a value or a time does not allow the edit; the page is unchanged.
    """
    project = Path(project).resolve()
    reg = registry or registry_module.default_registry()
    allowed = addable_blocks(reg)
    if component in _NOT_ADDED:
        raise EditError(f"{component}: {_NOT_ADDED[component]}")
    if component not in allowed:
        raise EditError(
            f"no block {component!r} in the library that add_block can add; these are: {', '.join(allowed)}"
        )
    declared = {v["id"]: v for v in reg.items[component].variables}
    given = dict(values or {})
    if DURATION_VARIABLE in given:
        raise EditError(f"{component}: pass the length as duration, not as {DURATION_VARIABLE}")
    for name, value in given.items():
        if name not in declared:
            takes = ", ".join(sorted(declared.keys() - {DURATION_VARIABLE}))
            raise EditError(f"{component} takes: {takes}, not {name!r}")
        if (why := value_problem(declared[name], value)) is not None:
            raise EditError(f"{component}, variable {name!r}: {why}")
    missing = [
        name
        for name, variable in declared.items()
        if name != DURATION_VARIABLE and variable["type"] in ("string", "number") and name not in given
    ]
    if missing:
        raise EditError(
            f"{component}: give {', '.join(missing)}. A block's default is a sample and never goes on screen: "
            "write the value (an empty string if it is not used)"
        )
    found = mounts(page)
    if not found:
        raise EditError("no layers in index.html")
    total = _video_seconds(page)
    slot = declared[DURATION_VARIABLE]
    if start < 0:
        raise EditError(f"{start:g} s is before the start of the video")
    if total is not None and start >= total - EPS:
        raise EditError(f"{start:g} s is past the end: the video is {total:g} s long")
    if duration < slot["min"] - EPS:
        raise EditError(
            f"a {component} needs at least {slot['min']:g} s (its entrance, a hold and its fade out), not {duration:g} s"
        )
    if duration > slot["max"] + EPS:
        raise EditError(f"a {component} stays at most {slot['max']:g} s, not {duration:g} s")
    start, end = round(start, 3), round(start + duration, 3)
    filled = {DURATION_VARIABLE: round(end - start, 3)} | {
        name: variable["default"]
        for name, variable in declared.items()
        if name != DURATION_VARIABLE and "default" in variable
    }
    filled |= given

    shot = _shot_at(found, start)
    index = 1 + max(m.layer.index for m in found.values() if m.layer.shot == shot)
    sharing = [m for m in found.values() if m.layer.start < end and start < m.layer.end]
    z_index = 1 + max((_attributes(m)[0] for m in sharing), default=0)
    row = _free_row(found, sharing, COMPONENT_ROWS[component][0])
    layer = LayerTiming(shot, index, component, start, end, 0, filled)
    layer_id = f"l-{shot}-{index}"

    warnings: list[str] = []
    if install:
        report = registry_module.install_item(component, project, registry=reg)
        warnings = [
            f"kept {path.relative_to(project).as_posix()}: changed since the last install (install with force to replace it)"
            for path in report.kept
        ]
    if total is not None and end > total + EPS:
        warnings.append(f"{layer_id}: it ends at {end:g} s, after the video, which is {total:g} s long")
    warnings += _fade_warning(page, shot, layer_id, end)
    page = _insert_after_last(page, list(found.values())[-1], mount_html(layer, row, z_index))
    before = [m.layer for m in found.values()]
    after = layers_from_page(page)
    warnings += slot_problems(after, {layer_id}, reg)
    warnings += _newly_hidden(before, after, reg)
    return page, BlockAdded(layer_id, component, shot, start, end, row, z_index, filled, warnings)


def _newly_hidden(
    before: Sequence[LayerTiming],
    after: Sequence[LayerTiming],
    reg: registry_module.Registry,
) -> list[str]:
    """What ``preflight.check_hidden_layers`` says of ``after`` that it did not say of ``before``, each sentence starting
    with the layer's id: the layers an edit made hidden behind a layer that covers the frame."""

    def said(layers: Sequence[LayerTiming]) -> list[str]:
        problems: list[str] = []
        warnings: list[str] = []
        preflight.check_hidden_layers(layers, reg, problems, warnings)
        return [_SLOT_LABEL.sub(lambda m: f"l-{m[1]}-{m[2]} ({m[3]})", message) for message in problems + warnings]

    already = set(said(before))
    return [message for message in said(after) if message not in already]


# the placement a layer's mount carries in its own style, over where its block puts itself (the CSS ``translate`` and
# ``scale`` properties, in px of the canvas and as a factor): moving or resizing a layer never edits its block
_TRANSLATE = re.compile(r"(-?[\d.]+)px\s+(-?[\d.]+)px")
SCALE_RANGE = (0.2, 2.0)


def arrange_layer_in(
    page: str,
    layer_id: str,
    *,
    dx: float | None = None,
    dy: float | None = None,
    scale: float | None = None,
    reset: bool = False,
) -> tuple[str, Arranged]:
    """``page`` with the layer ``layer_id`` moved by ``dx`` and ``dy`` (px of the canvas, right and down, from where its
    block puts itself) and scaled by ``scale`` around the frame's centre; the parts not given stay as they are and
    ``reset`` puts the layer back where its block puts it. Nothing knows where a block draws its words, so the caller
    looks at the frame afterwards.

    Raises:
        EditError: no such layer, a picture or clip (it fills the frame), nothing to change, or a number out of range.
    """
    found = mounts(page)
    if layer_id not in found:
        raise EditError(f"no layer {layer_id!r} in index.html")
    mount = found[layer_id]
    if mount.layer.component in MEDIA:
        raise EditError(
            f"{layer_id} fills the frame: its crop and move are the block's own from and to variables "
            "(set_variable), not a position"
        )
    if not reset and dx is None and dy is None and scale is None:
        raise EditError("say what to change: dx, dy, scale, or reset")
    canvas = tokens.default_tokens().canvas
    for name, value, limit in (
        ("dx", dx, canvas["width"]),
        ("dy", dy, canvas["height"]),
    ):
        if value is not None and abs(value) > limit:
            raise EditError(f"{name} is at most {limit} px either way, not {value:g}")
    low, high = SCALE_RANGE
    if scale is not None and not low <= scale <= high:
        raise EditError(f"scale is between {low:g} and {high:g}, not {scale:g}")
    attrs = {k: html.unescape(v) for k, v in _ATTRIBUTE.findall(mount.tag)}
    if "style" not in attrs:
        raise EditError(f"{layer_id} has no style attribute to place it with")
    parts = [tuple(piece.strip() for piece in part.split(":", 1)) for part in attrs["style"].split(";") if ":" in part]
    style = dict(parts)
    translate = _TRANSLATE.fullmatch(style.get("translate", ""))
    try:
        scale_was = float(style.get("scale", 1))
    except ValueError:
        scale_was = None
    if not reset and ((style.get("translate") and not translate) or scale_was is None):
        raise EditError(
            f"{layer_id} is placed as translate: {style.get('translate', 'none')}, scale: {style.get('scale', 'none')}, "
            "which this tool does not read (it writes pixels and a plain number): leave it as the person placed it, "
            "or ask them to reset it in the app"
        )
    was = {
        "dx": float(translate[1]) if translate else 0,
        "dy": float(translate[2]) if translate else 0,
        "scale": 1 if scale_was is None else scale_was,
    }
    now = {"dx": 0, "dy": 0, "scale": 1} if reset else dict(was)
    for name, value in (("dx", dx), ("dy", dy), ("scale", scale)):
        if value is not None:
            now[name] = value
    placed = dict(style)
    style.pop("translate", None)
    style.pop("scale", None)
    if now["dx"] or now["dy"]:
        style["translate"] = f"{now['dx']:g}px {now['dy']:g}px"
    if now["scale"] != 1:
        style["scale"] = f"{now['scale']:g}"
    if style == placed:
        raise EditError(
            f"{layer_id} is already there (dx {was['dx']:g}, dy {was['dy']:g}, scale {was['scale']:g}): "
            "nothing to change"
        )
    written = "; ".join(f"{name}: {value}" for name, value in style.items())
    return set_attributes(page, mount.tag, {"style": written}), Arranged(
        layer_id,
        mount.layer.component,
        {k: float(v) if k != "scale" else v for k, v in was.items()},
        now,
    )


def _vector_file(project: Path, vector: str) -> Path:
    """The SVG ``vector`` names: a bare name is one saved by ``save_vector``, anything else a path of the project."""
    named = vector if "/" in vector or "." in vector else f"{vectors.VECTORS_DIR}/{vector}.svg"
    file = (project / named).resolve()
    if not file.is_relative_to(project):
        raise EditError(f"{vector} is outside the project; save_vector puts a vector in {vectors.VECTORS_DIR}")
    if not file.is_file():
        raise EditError(f"no vector {vector} in the project; save_vector saves one under {vectors.VECTORS_DIR}")
    if file.suffix.lower() != ".svg":
        raise EditError(f"{vector} must be an .svg file")
    try:
        vectors.check_svg(file.read_text(encoding="utf-8"))
    except UnicodeDecodeError as error:
        raise EditError(f"{vector} cannot be read as text: {error}") from error
    return file


def _video_seconds(page: str) -> float | None:
    """The length of the video: the root's ``data-duration``."""
    root = re.search(r'<div\b[^>]*\bid="root"[^>]*>', page)
    length = re.search(r'data-duration="([\d.]+)"', root.group(0)) if root else None
    return float(length.group(1)) if length else None


def _shot_at(found: dict[str, Mount], start: float) -> str:
    """The shot on screen at ``start``: the last to have started by then, else the first."""
    first: dict[str, float] = {}
    for mount in found.values():
        shot = mount.layer.shot
        first[shot] = min(first.get(shot, mount.layer.start), mount.layer.start)
    started = [shot for shot, at in first.items() if at <= start + EPS]
    return max(started, key=first.__getitem__) if started else min(first, key=first.__getitem__)


_FADE_OUT = re.compile(
    r'tl\.fromTo\("\.shot-(?P<shot>[^"]+)", \{ opacity: 1 \}, \{ opacity: 0, duration: (?P<seconds>[\d.]+),'
    r"[^\n]*?\}, (?P<at>[\d.]+)\);"
)


def _fade_warning(page: str, shot: str, layer_id: str, end: float) -> list[str]:
    """A warning when ``end`` is after the dissolve into the shot after ``shot`` has taken the shot down: the composer
    fades the whole shot, so a layer that goes on past the end of that fade is gone when it is over, whatever its own
    length says. (The shot's own layers run to the end of the fade, so ending there is normal.)"""
    for found in _FADE_OUT.finditer(page):
        if found["shot"] != shot:
            continue
        at, seconds = float(found["at"]), float(found["seconds"])
        if end > at + seconds + EPS:
            return [
                (
                    f"{layer_id}: it ends at {end:g} s, but the next shot dissolves in from {at:g} s and takes {shot} "
                    f"down with it by {at + seconds:g} s, so it is gone from then; end it by {at + seconds:g} s or add "
                    "it in the next shot"
                )
            ]
    return []


def _attributes(mount: Mount) -> tuple[int, int]:
    """The z-index and the Studio row (``data-track-index``) the page holds for a mount."""
    attrs = {k: html.unescape(v) for k, v in _ATTRIBUTE.findall(mount.tag)}
    z = _Z_INDEX.search(attrs.get("style", ""))
    return (int(z[1]) if z else 0), int(attrs.get("data-track-index", 0))


def _free_row(found: dict[str, Mount], sharing: list[Mount], kind: str = "Graphics") -> int:
    """The Studio row of a layer of ``kind`` ("Graphics" or "Labels") that nothing shares time with, else one past the
    last row of the page."""
    row = {mount.id: _attributes(mount)[1] for mount in found.values()}
    taken = {row[m.id] for m in sharing}
    rows = sorted({row[m.id] for m in found.values() if COMPONENT_ROWS.get(m.layer.component, ("",))[0] == kind})
    return next((r for r in rows if r not in taken), max(row.values()) + 1)


def _graphic_warnings(layer: LayerTiming, file: Path, total: float | None) -> list[str]:
    layer_id = f"l-{layer.shot}-{layer.index}"
    warnings = []
    if total is not None and layer.end > total + EPS:
        warnings.append(f"{layer_id}: it ends at {layer.end:g} s, after the video, which is {total:g} s long")
    try:
        credit = json.loads(_record_path(file).read_text(encoding="utf-8")).get("credit")
    except (OSError, ValueError, AttributeError):
        credit = None
    if credit:
        warnings.append(
            f"{layer_id}: the vector's record asks for a credit on screen, {credit!r}: add a credit-line layer "
            f"in shot {layer.shot} while it shows"
        )
    return warnings


# the layers that sit in a corner of the frame, and the corner each takes: a credit line on the side its ``align`` names
# (left unless it says right), a date stamp where its ``position`` says (bottom left unless it says otherwise)
_CORNER_OF = {
    CREDIT_BLOCK: lambda values: f"bottom-{values.get('align', 'left')}",
    "date-stamp": lambda values: values.get("position", "bottom-left"),
}


def _corner_warnings(layer_id: str, position: str, sharing: list[Mount]) -> list[str]:
    """A warning for each layer that shows in the corner ``position`` while the graphic does: it would be covered or
    would cover the layer (a credit line and a date stamp carry words that must stay readable)."""
    return [
        f"{layer_id}: {position} is where {m.id} ({m.layer.component}) shows from {m.layer.start:g} to "
        f"{m.layer.end:g} s: use another corner or another time"
        for m in sharing
        if (corner := _CORNER_OF.get(m.layer.component)) is not None and corner(m.layer.values) == position
    ]


def _insert_after_last(page: str, last: Mount, mount: str) -> str:
    """``page`` with ``mount`` on a line of its own after the page's last mount, indented like it."""
    at = page.index(last.tag) + len(last.tag)
    closing = re.compile(r"\s*</div>").match(page, at)
    if closing is None:
        raise EditError(f"cannot see where the mount of {last.id} ends in the page; add the graphic in the app")
    line = page[page.rfind("\n", 0, at) + 1 : at]
    indent = line[: len(line) - len(line.lstrip())]
    return page[: closing.end()] + "\n" + indent + mount + page[closing.end() :]


@dataclass(frozen=True)
class VariableSet:
    layer: str
    component: str
    name: str
    old: Any
    new: Any
    warnings: list[str] = field(default_factory=list)


def set_variable_in(
    page: str,
    layer_id: str,
    name: str,
    value: Any,
    registry: registry_module.Registry | None = None,
) -> tuple[str, VariableSet]:
    """``page`` with the variable ``name`` of ``layer_id``'s block set to ``value``, and what changed. The value is
    checked against the block's variable (its type, length, range or options in the registry), as a shot list's is.
    The layer's length and a picture's or clip's file are not set here: they are the slot (``edit_timeline``) and
    ``replace_media`` (which sets the credit with it). A picture is never left without a credit line on screen with it.

    Raises:
        EditError: no such layer, variable or value; the page is unchanged.
    """
    found = mounts(page)
    if layer_id not in found:
        raise EditError(f"no layer {layer_id!r} in index.html")
    layer = found[layer_id].layer
    reg = registry or registry_module.default_registry()
    item = reg.items.get(layer.component)
    where = f"{layer_id} ({layer.component}), variable {name!r}"
    if item is None or item.type != "hyperframes:block":
        raise EditError(f"{layer_id} is not a block of the library: its variables are not known")
    declared = {variable["id"]: variable for variable in item.variables}
    if name == DURATION_VARIABLE:
        raise EditError(f"{where}: the length is the layer's slot on the timeline; trim it with edit_timeline")
    if layer.component in MEDIA and name == COMPONENT_ROWS[layer.component][2]:
        raise EditError(f"{where}: put another file there with replace_media, which sets its credit too")
    if name not in declared:
        takes = ", ".join(sorted(declared.keys() - {DURATION_VARIABLE}))
        raise EditError(f"{where}: {layer.component} takes: {takes}")
    if (why := value_problem(declared[name], value)) is not None:
        raise EditError(f"{where}: {why}")
    values = layer.values | {name: value}
    layers = [m.layer for m in found.values()]
    changed = [dataclasses.replace(layer, values=values) if m is layer else m for m in layers]
    lost = _bare_pictures(changed) - _bare_pictures(layers)
    if lost:
        raise EditError(
            f"{where}: {', '.join(sorted(lost))} would have no credit line on screen with it; a picture keeps a true "
            "credit (text saying whose it is): set the credit instead of taking it away"
        )
    return rewrite_mount(page, found[layer_id], values), VariableSet(
        layer_id,
        layer.component,
        name,
        layer.values.get(name),
        value,
        slot_problems(changed, {layer_id}, reg),
    )


def slot_problems(
    layers: Sequence[LayerTiming],
    only: set[str] | None = None,
    reg: registry_module.Registry | None = None,
) -> list[str]:
    """The layers (``only`` these ids, else all) whose slot is shorter than their block needs for its text, as one
    sentence each that starts with the layer's id: the block's own ``minSlot`` says it (a credit of twenty words needs
    far longer than one of six), and a block given less than that throws when the page is drawn, so nothing of it shows
    and a render fails. The composer refuses such a shot list; an edit made on the page lands here instead."""
    reg = reg or registry_module.default_registry()
    chosen = [
        layer
        for layer in layers
        if (only is None or f"l-{layer.shot}-{layer.index}" in only)
        and layer.component in reg.items
        and DURATION_VARIABLE in layer.values
    ]
    problems: list[str] = []
    preflight.check_minimum_slots(chosen, reg, problems, [])
    return [_SLOT_LABEL.sub(lambda m: f"l-{m[1]}-{m[2]} ({m[3]})", p) for p in problems]


_SLOT_LABEL = re.compile(r"shot '([^']*)', layer (\d+) \(([^)]*)\)")


def _bare_pictures(layers: list[LayerTiming]) -> set[str]:
    """The ids of the pictures and clips with no credit line on screen with them."""
    return {
        f"l-{layer.shot}-{layer.index}"
        for layer in layers
        if layer.component in MEDIA and not credits_of(layer, layers)
    }


def mounts(page: str) -> dict[str, Mount]:
    """Every layer mount of the page by id, in page order: its start tag as written, and the layer it mounts.

    The page is the truth: the app rewrites it (it adds ``data-hf-id`` and its agent renames the ``shot-<id>`` class),
    so a layer is known by its id ``l-<shot>-<n>`` and its ``data-composition-id``, never by its class. A layer's
    ``track`` is its rank in the page's stack, the z-index and then the place in the page, as the browser paints, so
    ``LayerTiming.depth`` orders the layers as the page stacks them (a higher one is in front).
    """
    found: dict[str, Mount] = {}
    z_index: dict[str, int] = {}
    for match in _TAG.finditer(page):
        attrs = {k: html.unescape(v) for k, v in _ATTRIBUTE.findall(match.group(0))}
        name = _LAYER_ID.fullmatch(attrs.get("id", ""))
        if name is None or "data-composition-id" not in attrs:
            continue
        start = float(attrs.get("data-start", 0))
        layer = LayerTiming(
            shot=name.group(1),
            index=int(name.group(2)),
            component=attrs["data-composition-id"],
            start=start,
            end=start + float(attrs.get("data-duration", 0)),
            track=0,
            values=json.loads(attrs.get("data-variable-values") or "{}"),
        )
        found[attrs["id"]] = Mount(match.group(0), layer, attrs.get("data-timeline-label") or clip_label(layer))
        z = _Z_INDEX.search(attrs.get("style", ""))
        z_index[attrs["id"]] = int(z[1]) if z else 0
    # sorted is stable: layers with one z-index keep the page's order
    ranks = {layer_id: rank for rank, layer_id in enumerate(sorted(found, key=z_index.__getitem__), 1)}
    return {
        layer_id: dataclasses.replace(mount, layer=dataclasses.replace(mount.layer, track=ranks[layer_id]))
        for layer_id, mount in found.items()
    }


def find_tag(page: str, element_id: str) -> str | None:
    """The start tag of the ``<div>`` whose ``id`` is ``element_id``, as the page holds it; None when there is none."""
    for match in _TAG.finditer(page):
        attrs = {k: html.unescape(v) for k, v in _ATTRIBUTE.findall(match.group(0))}
        if attrs.get("id") == element_id:
            return match.group(0)
    return None


def layers_from_page(page: str) -> list[LayerTiming]:
    """The layers of the page in page order, each with the times and variables the page holds and its place in the
    page's stack as its track, so ``preflight.credits_of`` and ``preflight.check_hidden_layers`` judge a page the app
    has edited as the page is."""
    return [mount.layer for mount in mounts(page).values()]


def rewrite_mount(
    page: str,
    mount: Mount,
    values: dict,
    duration: float | None = None,
    start: float | None = None,
) -> str:
    """``page`` with the mount's variables set to ``values`` and its timeline label made from them, as compose does;
    ``duration`` (seconds) also sets how long the layer is on the timeline (its ``durationSeconds`` goes in ``values``)
    and ``start`` when it begins."""
    changes = {
        "data-variable-values": json.dumps(values),
        "data-timeline-label": clip_label(dataclasses.replace(mount.layer, values=values)),
    }
    if duration is not None:
        changes["data-duration"] = f"{duration:g}"
    if start is not None:
        changes["data-start"] = f"{start:g}"
    return set_attributes(page, mount.tag, changes)


def set_attributes(page: str, tag: str, changes: dict[str, str]) -> str:
    """``page`` with the attributes of its start tag ``tag`` that ``changes`` names set to the new values (the others,
    and attributes the tag does not have, are left as they are)."""
    new = _ATTRIBUTE.sub(
        lambda m: f'{m[1]}="{html.escape(changes[m[1]], quote=True)}"' if m[1] in changes else m[0],
        tag,
    )
    return page.replace(tag, new)


def _credit_layer(found: dict[str, Mount], picture: LayerTiming, chosen: str | None) -> str:
    """The credit line that the preflight's provenance rule accepts for ``picture`` (``chosen`` among several)."""
    layer_id = f"l-{picture.shot}-{picture.index}"
    credits = [f"l-{c.shot}-{c.index}" for c in credits_of(picture, [m.layer for m in found.values()])]
    if chosen is not None:
        if chosen not in credits:
            raise EditError(
                f"{chosen} is not a credit line on screen with {layer_id}; these are: {', '.join(credits) or 'none'}"
            )
        return chosen
    if len(credits) != 1:
        raise EditError(
            f"{layer_id} has {len(credits)} credit lines on screen with it ({', '.join(credits) or 'none'}); "
            + ("name one (credit_layer)" if credits else "add one in the app first")
        )
    return credits[0]


def _short_clip(picture: LayerTiming, source: Path, clip_seconds: ClipLength) -> list[str]:
    try:
        length = clip_seconds(source)
    except ValueError as error:
        raise EditError(f"the clip {source.name} cannot be read: {error}") from error
    slot = picture.end - picture.start
    if slot <= length + 1 / tokens.default_tokens().canvas["fps"]:
        return []
    then = "it plays again from its start" if picture.values.get("hold") == "loop" else "its last frame stays on screen"
    return [
        (
            f"l-{picture.shot}-{picture.index}: the slot is {slot:.2f} s but the clip is only {length:.2f} s long; "
            f"{then} for the last {slot - length:.2f} s"
        )
    ]


def _record_path(file: Path) -> Path:
    return file.with_name(file.name + ".json")


def _place(edits: Path, source: Path) -> Path:
    """Where the layer's new file is: ``source`` itself when it is in ``edits`` already, else a copy there (with its
    record) under a name no other file has; a copy of the same bytes made before is that copy."""
    if source.parent == edits:
        return source
    target, number = edits / source.name, 1
    while target.exists():
        if target.read_bytes() == source.read_bytes():
            return target
        number += 1
        target = edits / f"{source.stem}-{number}{source.suffix}"
    edits.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    if _record_path(source).is_file():
        shutil.copyfile(_record_path(source), _record_path(target))
    return target


def project_notes(project: Path, *, profile_name: str = "default") -> str:
    """The notes of a composed project (``NOTES_FILE``): what the desktop app's Claude Code (a session in the project
    folder, without any repository's rules) needs to edit it with the server's tools. The limits named are those of
    ``profile_name``, and so are the house rules (``edit.yaml``)."""
    from hyperframes_mcp.motion.edit_config import load_edit_config
    from hyperframes_mcp.motion.spend import load_spend_limits

    cap = load_i2v_settings().limits
    limits = load_spend_limits(profile_name)
    rules = load_edit_config(profile_name).house_rules
    house = "\n\n## This profile's house rules\n" + "\n".join(f"- {rule}" for rule in rules) if rules else ""
    return f"""# This video project

A composed video project: `video.project.json` names its profile and topic. The person edits it in the HyperFrames
desktop app, and you are the app's Claude Code.

## Use the hyperframes-edit tools
The hyperframes-edit MCP server's tools (`mcp__hyperframes-edit__...`) work on this project. Call `project_info` first (it also names the
server's version: the git sha it started from, when, and its pid), then:
- `layer_table`: what is on screen when, the credit lines (each under the layer it belongs to), whether a picture is
  AI-made or archival, and what the narrator says over it. Use it instead of reading `index.html` yourself.
- `quote_picture` or `quote_clip`, then `confirm_spend`: a new picture or clip. Tell the person what you will make and
  its price before you confirm. The server refuses a call above ${limits.per_call_usd:g}, or one that takes this video
  past ${limits.per_project_usd:g}; clips are also capped at {cap.max_clips} clips and ${cap.max_spend_usd:g}.
- `search_media`, then `fetch_media`: a photo from the free archives (nothing is paid; CC0 and public domain, CC BY only
  when you pass `allow_by`). Tell the person what you found before you fetch.
- `replace_media`: put a picture or clip in its layer; it sets the credit line too. A file made by `confirm_spend`
  brings its own credit ("{AI_CREDIT}"), and so does one fetched by `fetch_media` (its record), so no `credit` is needed.
- `style_info`: the profile's picture style and settings, with its house rules (below).

## The layers
- Each picture, clip, title and label is a layer, `<div id="l-<shot>-<n>" ...>` in `index.html`, named in the timeline
  `<shot> · <kind>: <words>`. Keep the ids.
- A photo or a clip always has a true credit line on screen with it (a `credit-line` layer). How long it shows is
  the profile's house rule.
- A date plate or caption that covers the subject is moved, never cut short: its words carry meaning.
- Never use the app's own music, sound or picture finders for anything in the video: they bring no licence record
  (`search_media` and `fetch_media` do).{house}

## Without the tools
If the hyperframes-edit tools are not in your tool list (search for "hyperframes-edit" first), say so in your answer and
ask the person to quit the HyperFrames app completely (Cmd+Q) and reopen the project: the app's chat loads MCP servers
only when it starts, so after a change it may hold none or an old one. Do not edit `index.html` by hand instead.

## Keys
The tools read OPENAI_API_KEY and FAL_KEY themselves. Never open `.env`, and never print or ask for a key.

## Rendering
The person renders; you only assemble.
"""
