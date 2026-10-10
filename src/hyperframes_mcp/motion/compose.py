"""The composer: a shot list, the narration's word timings and an assets folder in, a HyperFrames project out.

``plan`` resolves every time (a cue becomes seconds), works out each layer's slot, runs every rule of the shot list
(see ``docs/motion-shot-list.md``) and returns the timeline without writing anything; ``compose`` is
``plan`` plus the project: the blocks installed once each by ``registry.install_item`` (GSAP, ``tokens.css`` and the
fonts come with them), the assets the layers name, the audio, and a root ``index.html`` that mounts every layer.

How a shot list becomes a page:

- every layer is a mount of its block (``data-composition-src``) whose ``data-duration`` and ``durationSeconds`` are the
  same number, the layer's slot; the agent never sets it, so a fade is never cut and a block never runs long;
- every layer sits directly in the root, marked with its shot's class (``shot-<id>``): HyperFrames Studio draws each
  child of the root as a clip, so a wrapper around a shot would be one clip the whole video long. One stack orders
  them all: a later shot is in front, and within a shot the layer's track. A hard cut is the default. A dissolve is a
  crossfade of the layers of two shots (opacity only, on a root timeline registered like any other: the enter ease
  for the incoming shot, the exit ease for the outgoing one, the token dissolve length), never a tween on a picture
  that has a grade of its own;
- the same block may be mounted any number of times, at the same moment or not: every mount keeps its own timeline
  (shown by a render with two overlapping mounts of one block, see the doc), so a block file is installed once;
- the night ground (the root's background) is under everything; the voice, an optional music bed (with a duck under
  the voice made from the word timings) and the sound effects are HyperFrames audio elements.
"""

from __future__ import annotations

import html
import json
import math
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path, PurePosixPath
from typing import Any

from hyperframes_mcp.motion import preflight, stage, tokens
from hyperframes_mcp.motion import registry as registry_module
from hyperframes_mcp.motion.shotlist import (
    CREDIT_BLOCK,
    DURATION_VARIABLE,
    EPS,
    Cue,
    ShotList,
    ShotListError,
    Time,
    layer_label,
    layer_name,
    unsafe_asset_path,
    validate_layers,
)
from hyperframes_mcp.motion.timing import CueError, Word, resolve_cue
from hyperframes_mcp.motion.tokens import is_number
from hyperframes_mcp.util.atomic import atomic_write_bytes

# the variable that holds a clip (a video-shot's); its length is checked when ffprobe is there
CLIP_VARIABLE = "video"
# a variable with one of these ids is a path to a file in the assets folder: copied, and rewritten to the project path
ASSET_VARIABLES = frozenset({"image", CLIP_VARIABLE, "svg"})
# a clip's length in seconds (its video stream's), or ValueError saying why the file is not a clip
ClipLength = Callable[[Path], float]
MEDIA_DIR = "assets/media"
# Studio lanes for the audio elements, above any layer's row
AUDIO_TRACK = 100
# Studio rows, from the top: one kind of layer per group of rows (display only: stacking is the layer's track)
ROW_KINDS = ("Pictures", "Graphics", "Labels", "Finish")
# a component's row kind, the word its clip label starts with, and the variable the label shows (``_row_spec``)
COMPONENT_ROWS: dict[str, tuple[str, str, str | None]] = {
    "photo-shot": ("Pictures", "Photo", "image"),
    "video-shot": ("Pictures", "Clip", "video"),
    "act-title": ("Graphics", "Title", "title"),
    "quote-card": ("Graphics", "Quote", "quote"),
    "counter": ("Graphics", "Counter", "label"),
    "ledger": ("Graphics", "Ledger", "title"),
    "bar-grid": ("Graphics", "Chart", "title"),
    "compare-bars": ("Graphics", "Chart", "title"),
    "split-bar": ("Graphics", "Chart", "wholeLabel"),
    "map-route": ("Graphics", "Map", "region"),
    "newspaper-clipping": ("Graphics", "Clipping", "headline"),
    "graphic-stamp": ("Graphics", "Graphic", "svg"),
    "credit-line": ("Labels", "Credit", "text"),
    "date-stamp": ("Labels", "Date", "text"),
    "lower-third": ("Labels", "Name", "name"),
    "finish": ("Finish", "Finish", None),
}
# a clip label's words are cut to this many characters, the ellipsis included
LABEL_CHARS = 48
# the music duck: down just before the voice, up slowly after it; a pause shorter than both keeps it down
DUCK_ATTACK_S = 0.25
DUCK_RELEASE_S = 0.8


@dataclass(frozen=True)
class ShotTiming:
    """A shot as composed: ``end`` includes the time it stays under the next shot's dissolve."""

    id: str
    start: float
    end: float
    dissolve_s: float


@dataclass(frozen=True)
class LayerTiming:
    """A layer as composed: its slot is ``end - start`` and ``values`` are the variables its mount carries."""

    shot: str
    index: int
    component: str
    start: float
    end: float
    track: int
    values: dict[str, Any]

    @property
    def slot(self) -> float:
        return self.values[DURATION_VARIABLE]

    @property
    def label(self) -> str:
        """``shot 's02', layer 2 (lower-third)``, the name every message uses."""
        return layer_name(self.shot, self.index, self.component)

    @property
    def short(self) -> str:
        """``layer 2 (lower-third)``: the name of a layer inside its own shot."""
        return f"layer {self.index} ({self.component})"

    @property
    def depth(self) -> tuple[int, int]:
        """Its place in its shot's stack: a higher track is in front, and on one track the later layer."""
        return self.track, self.index

    def overlap(self, other: LayerTiming) -> float:
        """Seconds the two slots share (negative when they do not meet)."""
        return min(self.end, other.end) - max(self.start, other.start)


@dataclass(frozen=True)
class Asset:
    """A file of the assets folder and where it goes in the project."""

    source: Path
    target: str


@dataclass(frozen=True)
class PlannedSfx:
    asset: Asset
    at: float
    gain_db: float
    # the file's length, when measured: effects that never sound at once share a row
    seconds: float | None = None


@dataclass
class Plan:
    """Everything ``compose`` writes, resolved and checked, with nothing written."""

    title: str
    shots: list[ShotTiming]
    layers: list[LayerTiming]
    total_s: float
    voice: Asset
    voice_duration: float | None
    music: Asset | None
    music_gain_db: float
    duck_lane: str | None
    sfx: list[PlannedSfx]
    assets: list[Asset]
    warnings: list[str] = field(default_factory=list)


@dataclass
class ComposeResult:
    """What ``compose`` did: paths are relative to the project."""

    project_dir: Path
    files: list[str]
    shots: list[ShotTiming]
    layers: list[LayerTiming]
    total_s: float
    warnings: list[str]


def _ms(value: float) -> float:
    return round(value, 3)


def _num(value: float) -> str:
    return repr(_ms(value))


# -- the plan -------------------------------------------------------------------------------------


class _Resolver:
    """Cues to seconds against one alignment; every failure is collected with the place it was written."""

    def __init__(self, alignment: list[Word], problems: list[str]) -> None:
        self.alignment = alignment
        self.problems = problems

    def time(self, value: Time, where: str) -> float | None:
        if not isinstance(value, Cue):
            return float(value)
        if not self.alignment:
            self.problems.append(
                f"{where}: the cue {value.cue!r} needs the narration's word timings (an alignment), and none was given"
            )
            return None
        try:
            return _ms(resolve_cue(self.alignment, value.cue, value.occurrence, value.offset))
        except CueError as error:
            self.problems.append(f"{where}: {error}")
            return None


def _asset(assets_dir: Path, relative: str, where: str, problems: list[str]) -> Asset | None:
    """The file ``relative`` names inside ``assets_dir`` and its place in the project, else a problem."""
    root = assets_dir.resolve()
    if why := unsafe_asset_path(relative):
        problems.append(f"{where}: {why}")
        return None
    source = (root / relative).resolve()
    if not source.is_relative_to(root):
        problems.append(f"{where}: {relative!r} leads outside the assets folder")
    elif not source.is_file():
        problems.append(f"{where}: {relative!r} is not a file in the assets folder {root}")
    else:
        return Asset(source, f"{MEDIA_DIR}/{relative}")
    return None


def _speech_spans(alignment: list[Word]) -> list[tuple[float, float]]:
    """Where the voice is speaking: words joined across pauses shorter than the duck's attack plus release."""
    spans: list[list[float]] = []
    for word in alignment:
        if spans and word.start - spans[-1][1] < DUCK_ATTACK_S + DUCK_RELEASE_S:
            spans[-1][1] = max(spans[-1][1], word.end)
        else:
            spans.append([word.start, word.end])
    return [(a, b) for a, b in spans]


def duck_lane(alignment: list[Word], duck_db: float, music_length: float) -> str | None:
    """The ``data-automation`` of a music bed that ducks ``duck_db`` under the voice, or None when it never ducks.

    A volume lane in clip time (the bed starts with the video): the gain falls to ``duck_db`` over the attack before
    each stretch of speech, holds, and comes back over the release.
    """
    spans = _speech_spans(alignment)
    if not spans or duck_db >= 0:
        return None
    depth = round(10 ** (duck_db / 20), 4)
    points: dict[float, float] = {0.0: 1.0}
    for start, end in spans:
        for at, gain in (
            (max(0.0, start - DUCK_ATTACK_S), 1.0),
            (start, depth),
            (end, depth),
            (end + DUCK_RELEASE_S, 1.0),
        ):
            points[_ms(at)] = gain
    lane = [{"t": t, "v": v} for t, v in sorted(points.items()) if t <= music_length]
    return json.dumps(
        {"version": 1, "lanes": [{"target": "volume", "points": lane}]},
        separators=(",", ":"),
    )


def _shot_timings(
    shotlist: ShotList, resolver: _Resolver, problems: list[str], warnings: list[str]
) -> list[ShotTiming]:
    """Resolve every shot's start and end; a dissolve keeps the shot before it on screen until it has faded."""
    low = tokens.default_tokens().duration["dissolve_min"]
    high = tokens.default_tokens().duration["dissolve_max"]
    timings: list[ShotTiming] = []
    for shot in shotlist.shots:
        label = f"shot {shot.id!r}"
        start = resolver.time(shot.start, f"{label}, start")
        if start is None:
            continue
        if isinstance(shot.duration, Cue):
            # a cue as a duration is the moment the shot ends
            end = resolver.time(shot.duration, f"{label}, duration")
        else:
            end = _ms(start + shot.duration)
        if end is None:
            continue
        if end - start <= EPS:
            problems.append(
                f"{label}: it would last {end - start:.3f} s (from {start:.3f} to {end:.3f}); a shot needs a length"
            )
            continue
        seconds = shot.transition_in.seconds
        dissolve = shot.transition_in.type == "dissolve"
        if not dissolve and seconds:
            problems.append(f"{label}: a cut has no length (transition_in.seconds is {seconds:g})")
        if dissolve and not low - EPS <= seconds <= high + EPS:
            problems.append(f"{label}: a dissolve lasts {low:g} to {high:g} s (the tokens), not {seconds:g} s")
        if dissolve and end - start < seconds - EPS:
            problems.append(f"{label}: it is shorter than its own {seconds:g} s dissolve")
        timings.append(ShotTiming(shot.id, start, end, seconds if dissolve else 0.0))

    for before, after in pairwise(timings):
        if after.start < before.start + EPS:
            problems.append(
                f"shot {after.id!r} starts at {after.start:.3f} s, not after shot {before.id!r} ({before.start:.3f} s): "
                "list the shots in time order"
            )
    return _dissolve_overlaps(timings, problems, warnings)


def _dissolve_overlaps(timings: list[ShotTiming], problems: list[str], warnings: list[str]) -> list[ShotTiming]:
    """Shots may overlap only through a dissolve, which keeps the outgoing shot until the incoming one has faded in."""
    out = list(timings)
    for i in range(1, len(out)):
        before, after = out[i - 1], out[i]
        overlap = before.end - after.start
        if after.dissolve_s:
            if overlap < -EPS or overlap > after.dissolve_s + EPS:
                problems.append(
                    f"shot {after.id!r} dissolves in over {after.dissolve_s:g} s from {after.start:.3f} s, so shot "
                    f"{before.id!r} must end between {after.start:.3f} and {after.start + after.dissolve_s:.3f} s; "
                    f"it ends at {before.end:.3f} s"
                )
            else:
                out[i - 1] = ShotTiming(
                    before.id,
                    before.start,
                    _ms(after.start + after.dissolve_s),
                    before.dissolve_s,
                )
        elif overlap > EPS:
            problems.append(
                f"shot {after.id!r} starts at {after.start:.3f} s while shot {before.id!r} runs to {before.end:.3f} s: "
                "shots overlap only through a dissolve (transition_in)"
            )
        elif overlap < -EPS:
            warnings.append(
                f"{-overlap:.2f} s gap between shot {before.id!r} (ends {before.end:.3f} s) and shot {after.id!r} "
                f"(starts {after.start:.3f} s): the night ground shows"
            )
    return out


def _seconds(value: float) -> str:
    return f"{round(value, 2):g}"


def _check_clips(
    layers: list[LayerTiming],
    assets: list[Asset],
    reg: registry_module.Registry,
    clip_seconds: ClipLength,
    problems: list[str],
    warnings: list[str],
) -> None:
    """Every layer's clip (its ``video`` variable, once copied) must be readable and playable from ``trimStart``; a slot
    longer than what is left of it is a warning that names what ``hold`` does with the rest.
    """
    sources = {asset.target: asset.source for asset in assets}
    frame = 1 / tokens.default_tokens().canvas["fps"]  # a slot over its clip by less than this is no gap
    for layer in layers:
        source = sources.get(layer.values.get(CLIP_VARIABLE))
        if source is None:
            continue
        try:
            length = clip_seconds(source)
        except ValueError as error:
            problems.append(f"{layer.label}, variable {CLIP_VARIABLE!r}: the clip cannot be read: {error}")
            continue
        shown = {v["id"]: v["default"] for v in reg.items[layer.component].variables}
        shown |= layer.values
        trim, hold, slot = shown.get("trimStart", 0), shown.get("hold"), layer.slot
        if not is_number(trim):
            continue  # the variable check already refused it
        if trim >= length - EPS:
            problems.append(
                f"{layer.label}, variable 'trimStart': {_seconds(trim)} s is not before the end of the clip, which is "
                f"{length:.2f} s long"
            )
            continue
        left = length - trim
        if slot <= left + frame:
            continue
        if hold == "loop":
            times = round(slot / left)
            plays = f"{times} times in all"
            if abs(slot - times * left) > frame:
                times = math.ceil(slot / left)
                plays = f"{times} times in all (the last one is cut off after {_seconds(slot - (times - 1) * left)} s)"
            ending = f"so it plays again from trimStart, {plays}"
        else:
            ending = f"so its last frame stays on screen for the last {_seconds(slot - left)} s"
        warnings.append(
            f"{layer.label}: the slot is {_seconds(slot)} s but the clip is only {_seconds(left)} s long after "
            f"trimStart {_seconds(trim)} s; hold is {hold}, {ending}"
        )


def _layer_timings(
    shotlist: ShotList,
    shots: dict[str, ShotTiming],
    reg: registry_module.Registry,
    assets_dir: Path,
    problems: list[str],
    warnings: list[str],
) -> tuple[list[LayerTiming], list[Asset]]:
    """Each layer's slot and the variables its mount carries; the assets they name."""
    layers: list[LayerTiming] = []
    assets: list[Asset] = []
    for shot in shotlist.shots:
        timing = shots.get(shot.id)
        if timing is None:
            continue
        for index, layer in enumerate(shot.layers):
            where = layer_label(shot, index)
            item = reg.items.get(layer.component)
            start = _ms(timing.start + layer.at)
            if start >= timing.end - EPS:
                problems.append(
                    f"{where}: 'at' {layer.at:g} s is at the end of the shot, which lasts {timing.end - timing.start:.3f} s"
                )
                continue
            end = _ms(start + layer.duration) if layer.duration else timing.end
            if end > timing.end + EPS:
                warnings.append(f"{where} ends at {end:.3f} s, after its shot ({timing.end:.3f} s)")
            slot = _ms(end - start)
            declared = {v["id"]: v for v in item.variables} if item else {}
            duration_variable = declared.get(DURATION_VARIABLE, {})
            if duration_variable.get("min") is not None and slot < duration_variable["min"] - EPS:
                problems.append(
                    f"{where}: a slot of {slot:g} s is shorter than the {duration_variable['min']:g} s "
                    f"{layer.component} needs for its fades and reading time; give the layer more time"
                )
            if duration_variable.get("max") is not None and slot > duration_variable["max"] + EPS:
                warnings.append(
                    f"{where}: a slot of {slot:g} s is longer than the {duration_variable['max']:g} s "
                    f"{layer.component} declares as its maximum"
                )
            values: dict[str, Any] = {DURATION_VARIABLE: slot}
            for name in declared:
                if name in layer.variables:
                    values[name] = layer.variables[name]
            for name in sorted(ASSET_VARIABLES & set(values)):
                path = values[name]
                if isinstance(path, str) and path:
                    asset = _asset(assets_dir, path, f"{where}, variable {name!r}", problems)
                    if asset:
                        values[name] = asset.target
                        assets.append(asset)
            layers.append(
                LayerTiming(
                    shot=shot.id,
                    index=index + 1,
                    component=layer.component,
                    start=start,
                    end=end,
                    track=layer.track if layer.track is not None else index + 1,
                    values=values,
                )
            )
    return layers, assets


def _provenance(layers: list[LayerTiming], reg: registry_module.Registry, problems: list[str]) -> None:
    """Every picture or clip is credited: a block whose registry entry says needsCredit needs a credit layer with text,
    in its shot, on screen while it is."""
    for layer in layers:
        item = reg.items.get(layer.component)
        if item and item.needs_credit and not preflight.credits_of(layer, layers):
            problems.append(
                f"{layer.label}: {layer.component} needs a {CREDIT_BLOCK} layer in the same shot, with its text "
                "filled in, on screen at the same time (where the picture or clip comes from)"
            )


def plan(
    shotlist: ShotList,
    alignment: list[Word],
    voice_duration: float | None,
    *,
    assets_dir: str | Path,
    registry: registry_module.Registry | None = None,
    clip_seconds: ClipLength | None = None,
    sound_seconds: ClipLength | None = None,
) -> Plan:
    """Resolve and check a shot list; nothing is written.

    ``alignment`` are the narration's word timings (empty when no cue is used and no music duck is wanted);
    ``voice_duration`` is the voice's length in seconds when known, which times must stay inside.
    ``clip_seconds`` gives the length of a ``video`` variable's file (ffprobe, see ``video_cli``) when known: a
    ``trimStart`` at or past its end is an error and a slot longer than the clip is a warning; without it a clip is
    not checked. ``sound_seconds`` gives the length of a sound effect's file when known (only for the Studio rows:
    effects that never sound at once share one); an effect it cannot measure keeps a row of its own.

    Raises:
        ShotListError: listing every rule the shot list breaks.
    """
    reg = registry or registry_module.default_registry()
    assets_root = Path(assets_dir)
    problems = validate_layers(shotlist, reg)
    variable_problems = list(problems)
    warnings: list[str] = []
    resolver = _Resolver(alignment, problems)

    timings = _shot_timings(shotlist, resolver, problems, warnings)
    by_id = {t.id: t for t in timings}
    layers, assets = _layer_timings(shotlist, by_id, reg, assets_root, problems, warnings)
    _provenance(layers, reg, problems)
    if clip_seconds is not None:
        _check_clips(layers, assets, reg, clip_seconds, problems, warnings)
    preflight.check_minimum_slots(layers, reg, problems, warnings, refused=variable_problems)
    preflight.check_hidden_layers(layers, reg, problems, warnings)

    if voice_duration is not None:
        for timing in timings:
            if timing.start > voice_duration + EPS:
                problems.append(
                    f"shot {timing.id!r} starts at {timing.start:.3f} s, after the voice ends at {voice_duration:.3f} s"
                )
    if timings and timings[0].start > EPS:
        warnings.insert(
            0,
            f"the first shot starts at {timings[0].start:.3f} s: the night ground shows before it",
        )

    audio = shotlist.audio
    voice = _asset(assets_root, audio.voice.path, "audio, voice", problems)
    music = None
    if audio.music:
        music = _asset(assets_root, audio.music.path, "audio, music", problems)
        if not alignment and audio.music.duck_under_voice_db < 0:
            warnings.append("the music is set to duck under the voice but no alignment was given: it is not ducked")
    sfx: list[PlannedSfx] = []
    for number, effect in enumerate(audio.sfx, 1):
        where = f"audio, sfx {number}"
        at = resolver.time(effect.at, f"{where}, at")
        asset = _asset(assets_root, effect.path, where, problems)
        if at is not None and voice_duration is not None and at > voice_duration + EPS:
            problems.append(f"{where}: at {at:.3f} s is after the voice ends at {voice_duration:.3f} s")
        if at is not None and asset:
            seconds = None
            if sound_seconds is not None:
                try:
                    seconds = sound_seconds(asset.source)
                except ValueError as error:
                    warnings.append(f"{where}: its length is unknown ({error}): it gets a Studio row of its own")
            sfx.append(PlannedSfx(asset, at, effect.gain_db, seconds))

    if problems:
        raise ShotListError(problems)

    total = max(t.end for t in timings)
    if voice_duration is not None and voice_duration > total + EPS:
        warnings.append(
            f"the voice runs {voice_duration - total:.2f} s past the last shot ({total:.3f} s): the night ground shows"
        )
        total = _ms(voice_duration)
    lane = duck_lane(alignment, audio.music.duck_under_voice_db, total) if audio.music else None
    every = [voice, *([music] if music else []), *(s.asset for s in sfx), *assets]
    unique = list({a.target: a for a in every}.values())
    return Plan(
        title=shotlist.meta.title,
        shots=timings,
        layers=layers,
        total_s=total,
        voice=voice,
        voice_duration=voice_duration,
        music=music,
        music_gain_db=audio.music.gain_db if audio.music else 0.0,
        duck_lane=lane,
        sfx=sfx,
        assets=unique,
        warnings=warnings,
    )


# -- the page -------------------------------------------------------------------------------------


def _attribute(value: str) -> str:
    return html.escape(value, quote=True)


def _volume(gain_db: float) -> str:
    return repr(round(10 ** (gain_db / 20), 4))


def _lanes(spans: list[tuple[float, float]]) -> list[int]:
    """Each span's lane, in the order given: taken in order of start, a span gets the first lane free by then (a span
    may start where another ends), so no lane holds two spans at once and there are as few lanes as the spans need.
    """
    ends: list[float] = []
    lanes = [0] * len(spans)
    for i in sorted(range(len(spans)), key=spans.__getitem__):
        start, end = spans[i]
        lane = next((n for n, last in enumerate(ends) if last <= start), len(ends))
        if lane == len(ends):
            ends.append(end)
        else:
            ends[lane] = end
        lanes[i] = lane
    return lanes


def _row_spec(component: str) -> tuple[str, str, str | None]:
    """The component's ``COMPONENT_ROWS`` entry; one not named there is a graphic labelled with its own name."""
    return COMPONENT_ROWS.get(component, ("Graphics", component, None))


def _rows(layers: list[LayerTiming]) -> dict[tuple[str, int], int]:
    """Each layer's Studio row, keyed by (shot, index): the kinds of ``ROW_KINDS`` from the top, each with as few rows
    as keep its clips apart in time (Studio's move and trim refuse two clips at once on a row).
    """
    by_kind: dict[str, list[LayerTiming]] = {kind: [] for kind in ROW_KINDS}
    for layer in layers:
        by_kind[_row_spec(layer.component)[0]].append(layer)
    rows: dict[tuple[str, int], int] = {}
    base = 0
    for kind in ROW_KINDS:
        lanes = _lanes([(layer.start, layer.end) for layer in by_kind[kind]])
        for layer, lane in zip(by_kind[kind], lanes, strict=True):
            rows[(layer.shot, layer.index)] = base + lane
        base += max(lanes, default=-1) + 1
    return rows


def clip_label(layer: LayerTiming) -> str:
    """The clip's name in Studio: ``s02 · Credit: Bank of England Archive``, the words cut to ``LABEL_CHARS``."""
    _, word, variable = _row_spec(layer.component)
    value = layer.values.get(variable) if variable else None
    if not isinstance(value, str) or not value.strip():
        return f"{layer.shot} · {word}"
    if variable in ASSET_VARIABLES:
        value = PurePosixPath(value).stem
    return f"{layer.shot} · {word}: {label_words(value)}"


def label_words(value: str) -> str:
    """The words of a clip label: on one line, cut to ``LABEL_CHARS`` with an ellipsis."""
    words = " ".join(value.split())
    if len(words) > LABEL_CHARS:
        words = words[: LABEL_CHARS - 1].rstrip() + "…"
    return words


def _stack(plan_: Plan) -> dict[tuple[str, int], int]:
    """Each layer's z-index, keyed by (shot, index): one stack, a later shot in front of an earlier one and within a
    shot the higher track in front (the later layer when two share a track)."""
    order = {shot.id: n for n, shot in enumerate(plan_.shots)}
    stacked = sorted(plan_.layers, key=lambda layer: (order[layer.shot], layer.depth))
    return {(layer.shot, layer.index): z for z, layer in enumerate(stacked, 1)}


def mount_html(layer: LayerTiming, row: int, z: int) -> str:
    """The mount of ``layer`` on Studio row ``row`` at z-index ``z``: the one line that puts a block in the root, shared
    with the edits that add a layer to a page that exists."""
    canvas = tokens.default_tokens().canvas
    values = _attribute(json.dumps(layer.values))
    return (
        f'<div id="l-{layer.shot}-{layer.index}" class="clip shot-{layer.shot}" data-composition-id="{layer.component}" '
        f'data-composition-src="compositions/{layer.component}.html" data-start="{_num(layer.start)}" '
        f'data-duration="{_num(layer.slot)}" data-track-index="{row}" '
        f'data-timeline-label="{_attribute(clip_label(layer))}" data-width="{canvas["width"]}" '
        f'data-height="{canvas["height"]}" data-variable-values="{values}" style="z-index: {z}"></div>'
    )


def _audio_elements(plan_: Plan) -> list[str]:
    elements = [
        f'<audio id="voice" data-audio-group="voiceover" src="{plan_.voice.target}" data-start="0" '
        + (f'data-duration="{_num(plan_.voice_duration)}" ' if plan_.voice_duration else "")
        + f'data-track-index="{AUDIO_TRACK}" data-timeline-label="Voice" data-volume="1"></audio>'
    ]
    if plan_.music:
        automation = f' data-automation="{_attribute(plan_.duck_lane)}"' if plan_.duck_lane else ""
        elements.append(
            f'<audio id="music" data-audio-group="music" src="{plan_.music.target}" data-start="0" '
            f'data-duration="{_num(plan_.total_s)}" data-track-index="{AUDIO_TRACK + 1}" '
            f'data-timeline-label="Music" data-volume="{_volume(plan_.music_gain_db)}"{automation}></audio>'
        )
    # an effect whose length is unknown never ends, so it keeps a row of its own
    lanes = _lanes([(e.at, math.inf if e.seconds is None else e.at + e.seconds) for e in plan_.sfx])
    for number, (effect, lane) in enumerate(zip(plan_.sfx, lanes, strict=True), 1):
        label = _attribute(f"SFX: {PurePosixPath(effect.asset.target).stem}")
        elements.append(
            f'<audio id="sfx-{number}" data-audio-group="sfx" src="{effect.asset.target}" '
            f'data-start="{_num(effect.at)}" data-track-index="{AUDIO_TRACK + 2 + lane}" '
            f'data-timeline-label="{label}" data-volume="{_volume(effect.gain_db)}"></audio>'
        )
    return elements


def _dissolve_tweens(shots: list[ShotTiming]) -> list[str]:
    """The crossfades: the incoming shot's layers up on the enter ease, the outgoing shot's down on the exit ease."""
    ease = tokens.default_tokens().ease
    lines: list[str] = []
    for i, shot in enumerate(shots):
        if not shot.dissolve_s:
            continue
        timing = f"duration: {_num(shot.dissolve_s)}"
        lines.append(
            f'tl.fromTo(".shot-{shot.id}", {{ opacity: 0 }}, {{ opacity: 1, {timing}, ease: {json.dumps(ease["enter"])} }}, {_num(shot.start)});'
        )
        if i > 0:
            lines.append(
                f'tl.fromTo(".shot-{shots[i - 1].id}", {{ opacity: 1 }}, {{ opacity: 0, {timing}, ease: {json.dumps(ease["exit"])}, '
                f"immediateRender: false }}, {_num(shot.start)});"
            )
    return lines


def index_html(plan_: Plan) -> str:
    """The root composition on the night ground: every layer mount, the audio, the dissolves."""
    t = tokens.default_tokens()
    width, height, fps = t.canvas["width"], t.canvas["height"], t.canvas["fps"]
    ground = t.colors["night"]
    rows, stack = _rows(plan_.layers), _stack(plan_)
    mounts = "\n".join(
        f"      {mount_html(layer, rows[layer.shot, layer.index], stack[layer.shot, layer.index])}"
        for layer in plan_.layers
    )
    audio = "\n".join(f"      {element}" for element in _audio_elements(plan_))
    tweens = "\n".join(f"      {line}" for line in _dissolve_tweens(plan_.shots))
    return f"""<!doctype html>
<html lang="en" data-resolution="landscape">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width={width}, height={height}" />
    <title>{html.escape(plan_.title)}</title>
    <script src="vendor/gsap.min.js"></script>
    <style>
      * {{ margin: 0; padding: 0; box-sizing: border-box; }}
      html, body {{ margin: 0; width: {width}px; height: {height}px; overflow: hidden; background: {ground}; }}
      #root {{ position: relative; width: {width}px; height: {height}px; overflow: hidden; background: {ground}; }}
      .clip {{ position: absolute; left: 0; top: 0; width: {width}px; height: {height}px; }}
    </style>
  </head>
  <body>
    <div id="root" data-composition-id="main" data-start="0" data-duration="{_num(plan_.total_s)}" data-width="{width}" data-height="{height}" data-fps="{fps}">
{mounts}
{audio}
    </div>
    <script>
      window.__timelines = window.__timelines || {{}};
      const tl = gsap.timeline({{ paused: true }});
{tweens}
      window.__timelines["main"] = tl;
      tl.seek(0);
    </script>
  </body>
</html>
"""


# -- writing --------------------------------------------------------------------------------------


def compose(
    shotlist: ShotList,
    alignment: list[Word],
    voice_duration: float | None,
    project_dir: str | Path,
    *,
    assets_dir: str | Path,
    registry: registry_module.Registry | None = None,
    clip_seconds: ClipLength | None = None,
    sound_seconds: ClipLength | None = None,
) -> ComposeResult:
    """Write the HyperFrames project of ``shotlist`` into ``project_dir`` and return what it holds.

    Raises:
        ShotListError: the shot list breaks a rule (nothing is written).
        registry.RegistryError: a block cannot be installed.
    """
    reg = registry or registry_module.default_registry()
    plan_ = plan(
        shotlist,
        alignment,
        voice_duration,
        assets_dir=assets_dir,
        registry=reg,
        clip_seconds=clip_seconds,
        sound_seconds=sound_seconds,
    )
    project = Path(project_dir)
    project.mkdir(parents=True, exist_ok=True)
    base = project.resolve()
    written: set[str] = set()
    warnings = list(plan_.warnings)

    for name in sorted({layer.component for layer in plan_.layers}):
        report = registry_module.install_item(name, project, registry=reg)
        for path in (*report.written, *report.unchanged):
            written.add(path.relative_to(base).as_posix())
        for path in report.kept:
            warnings.append(
                f"kept {path.relative_to(base).as_posix()}: changed since the last install (install with force to replace it)"
            )
    for asset in plan_.assets:
        target = project / asset.target
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(asset.source, target)
        written.add(asset.target)
    atomic_write_bytes(project / "index.html", index_html(plan_).encode("utf-8"))
    written.add("index.html")
    stage.write_meta(project, "motion-shotlist", plan_.title)
    written.add("meta.json")
    if (project / registry_module.LOCK_NAME).is_file():
        written.add(registry_module.LOCK_NAME)

    return ComposeResult(
        project_dir=project,
        files=sorted(written),
        shots=plan_.shots,
        layers=plan_.layers,
        total_s=plan_.total_s,
        warnings=warnings,
    )
