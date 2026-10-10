"""The shot list: the JSON an agent writes to say what is on screen when, and the rules it must pass.

A shot list names library blocks and fills their declared variables; the agent never writes animation code. The
models here are closed (an unknown key is an error) and say what a *shape* is; ``validate_layers`` checks every
layer against the registry (the block exists, the variables are declared, the ones that carry text or numbers
are given). What needs times (slots, overlaps, the provenance of archival photos) is checked by
``src/hyperframes_mcp/motion/compose.py`` once the cues are resolved. Every message names the shot, the layer and the variable.

Why the strict variable rule: a block's default values are samples ("Jay Cooke", 8,198 bars), good for a gallery
and a test. A shot list that forgot a field would put an invented figure on screen in a published video, so a
string or number variable must be written out by the shot (an empty string is allowed where the block treats it as
absent); only a gallery or a test opts out, with ``"sample": true`` on the layer, and a shot list whose meta says
``"publish": true`` refuses that.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Discriminator,
    Field,
    Tag,
    ValidationError,
    model_validator,
)

from hyperframes_mcp.motion import registry as registry_module
from hyperframes_mcp.motion.tokens import HEX_COLOUR, is_number

DURATION_VARIABLE = "durationSeconds"
# the provenance rule: a layer of a block whose registry entry says needsCredit needs a layer of this block, with text,
# on screen with it
CREDIT_BLOCK = "credit-line"
# times closer than this (about a tenth of a frame) are the same time
EPS = 0.005
_SHOT_ID = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,39}$"
_TAGS = {"t_seconds", "t_cue"}
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


class ShotListError(ValueError):
    """The shot list is not valid; ``problems`` lists every reason, each naming the shot, layer and variable."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = list(problems)
        super().__init__("invalid shot list:\n  " + "\n  ".join(self.problems))


def _not_bool(value: Any) -> Any:
    if isinstance(value, bool):
        # pydantic turns only a ValueError into a validation error, so not the TypeError ruff prefers
        message = "must be a number, not true or false"
        raise ValueError(message)  # noqa: TRY004
    return value


def unsafe_asset_path(value: str) -> str | None:
    """Why ``value`` is not a path inside the assets folder (a URL, an absolute path, ``..``), else None."""
    if "://" in value or value.startswith("//") or _SCHEME.match(value):
        return f"{value!r} is a URL or a drive path, not a path inside the assets folder"
    return registry_module._unsafe_path(value)


def _asset_path(value: str) -> str:
    if why := unsafe_asset_path(value):
        raise ValueError(why)
    return value


AssetPath = Annotated[str, AfterValidator(_asset_path)]
Number = Annotated[float, BeforeValidator(_not_bool), Field(allow_inf_nan=False)]
Seconds = Annotated[Number, Field(ge=0)]


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Cue(_Closed):
    """A time named by the narration: the start of the ``occurrence``-th time ``cue`` is said, plus ``offset`` s."""

    cue: str = Field(min_length=1)
    occurrence: int = Field(default=1, ge=1)
    offset: Number = 0.0


def _time_kind(value: Any) -> str:
    return "t_cue" if isinstance(value, (dict, Cue)) else "t_seconds"


# a number of seconds or a cue; the tags keep an error to the branch the value was meant for
Time = Annotated[
    Annotated[Seconds, Tag("t_seconds")] | Annotated[Cue, Tag("t_cue")],
    Discriminator(_time_kind),
]


class Meta(_Closed):
    title: str = Field(min_length=1)
    language: Literal["en"] = "en"
    publish: bool = False


class Voice(_Closed):
    path: AssetPath


class Music(_Closed):
    path: AssetPath
    gain_db: Number
    duck_under_voice_db: Annotated[Number, Field(le=0)]


class Sfx(_Closed):
    path: AssetPath
    at: Time
    gain_db: Number


class Master(_Closed):
    lufs: Number = -14.0
    true_peak_db: Annotated[Number, Field(le=0)] = -1.0


class Audio(_Closed):
    voice: Voice
    music: Music | None = None
    sfx: list[Sfx] = Field(default_factory=list)
    master: Master = Field(default_factory=Master)


class Transition(_Closed):
    type: Literal["cut", "dissolve"]
    seconds: Seconds = 0.0


class Layer(_Closed):
    component: str = Field(min_length=1)
    at: Seconds = 0.0
    duration: Annotated[Number, Field(gt=0)] | None = None
    track: int | None = Field(default=None, ge=0)
    sample: bool = False
    variables: dict[str, Any] = Field(default_factory=dict)


class Shot(_Closed):
    id: str = Field(pattern=_SHOT_ID)
    start: Time
    duration: Time
    transition_in: Transition = Field(default_factory=lambda: Transition(type="cut"))
    layers: list[Layer] = Field(min_length=1)


class ShotList(_Closed):
    version: Literal[1]
    meta: Meta
    audio: Audio
    shots: list[Shot] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_ids(self) -> ShotList:
        seen: set[str] = set()
        for shot in self.shots:
            if shot.id in seen:
                raise ValueError(f"shot id {shot.id!r} is used twice")
            seen.add(shot.id)
        return self


# -- loading ------------------------------------------------------------------------------------


def _shot_label(raw: Any, index: int) -> str:
    shots = raw.get("shots") if isinstance(raw, dict) else None
    shot = shots[index] if isinstance(shots, list) and index < len(shots) else None
    shot_id = shot.get("id") if isinstance(shot, dict) else None
    return f"shot {shot_id!r}" if isinstance(shot_id, str) else f"shot {index + 1}"


def _layer_label(raw: Any, shot: int, index: int) -> str:
    try:
        component = raw["shots"][shot]["layers"][index]["component"]
    except (KeyError, IndexError, TypeError):
        component = None
    named = f" ({component})" if isinstance(component, str) else ""
    return f"layer {index + 1}{named}"


def _where(raw: Any, loc: tuple[Any, ...]) -> str:
    """``shot 's02', layer 1 (lower-third), variables`` for a pydantic error location."""
    loc = tuple(part for part in loc if part not in _TAGS)
    parts: list[str] = []
    shot: int | None = None
    i = 0
    while i < len(loc):
        following = loc[i + 1] if i + 1 < len(loc) else None
        if loc[i] == "shots" and isinstance(following, int):
            shot = following
            parts.append(_shot_label(raw, shot))
            i += 2
        elif loc[i] == "layers" and shot is not None and isinstance(following, int):
            parts.append(_layer_label(raw, shot, following))
            i += 2
        else:
            parts.append(str(loc[i]))
            i += 1
    return ", ".join(parts)


def parse_shotlist(raw: Any) -> ShotList:
    """The shot list in ``raw`` (decoded JSON), or :class:`ShotListError` naming every shape problem."""
    try:
        return ShotList.model_validate(raw)
    except ValidationError as error:
        problems = []
        for item in error.errors(include_url=False):
            where = _where(raw, tuple(item["loc"]))
            problems.append(f"{where}: {item['msg']}" if where else item["msg"])
        raise ShotListError(problems) from error


def load_shotlist(path: str | Path) -> ShotList:
    """Read and parse a shot list file."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ShotListError([f"{path}: cannot read the shot list: {error}"]) from error
    return parse_shotlist(raw)


# -- the registry rules ---------------------------------------------------------------------------


def layer_name(shot_id: str, number: int, component: str) -> str:
    """``shot 's02', layer 2 (lower-third)``: how every message names a layer (``number`` counts from 1)."""
    return f"shot {shot_id!r}, layer {number} ({component})"


def layer_label(shot: Shot, index: int) -> str:
    """The name of the layer at ``index`` (from 0) of ``shot``."""
    return layer_name(shot.id, index + 1, shot.layers[index].component)


def value_problem(variable: dict[str, Any], value: Any) -> str | None:
    """Why ``value`` is not acceptable for the declared ``variable``, else None."""
    kind = variable["type"]
    if kind == "string":
        if not isinstance(value, str):
            return f"must be text, not {value!r}"
        limit = variable.get("maxLength")
        if limit is not None and len(value) > limit:
            return f"is {len(value)} characters, the block takes at most {limit}"
    elif kind == "number":
        if not is_number(value):
            return f"must be a number, not {value!r}"
        low, high = variable.get("min"), variable.get("max")
        if low is not None and value < low:
            return f"is {value:g}, the block's minimum is {low:g}"
        if high is not None and value > high:
            return f"is {value:g}, the block's maximum is {high:g}"
    elif kind == "boolean":
        if not isinstance(value, bool):
            return f"must be true or false, not {value!r}"
    elif kind == "enum":
        options = [option["value"] for option in variable["options"]]
        if value not in options:
            return f"must be one of {', '.join(map(str, options))}, not {value!r}"
    elif kind == "color" and not (isinstance(value, str) and HEX_COLOUR.match(value)):
        return f"must be a colour like #rrggbb, not {value!r}"
    return None


def validate_layers(shotlist: ShotList, reg: registry_module.Registry | None = None) -> list[str]:
    """Every problem with the layers' components and variables, against the registry (empty when there is none)."""
    reg = reg or registry_module.default_registry()
    problems: list[str] = []
    for shot in shotlist.shots:
        for index, layer in enumerate(shot.layers):
            where = layer_label(shot, index)
            item = reg.items.get(layer.component)
            if item is None:
                available = ", ".join(sorted(reg.items)) or "none"
                problems.append(f"{where}: no component {layer.component!r} in the registry; available: {available}")
                continue
            if item.type != "hyperframes:block":
                problems.append(
                    f"{where}: {layer.component!r} is a component, not a block: it has nothing to put on screen"
                )
                continue
            if layer.sample and shotlist.meta.publish:
                problems.append(
                    f'{where}: "sample": true is for galleries and tests; a shot list with "publish": true refuses it'
                )
            declared = {variable["id"]: variable for variable in item.variables}
            for name in layer.variables:
                if name == DURATION_VARIABLE:
                    problems.append(
                        f"{where}, variable {name!r}: the composer sets it from the layer's slot; "
                        "set the layer's 'at' and 'duration' instead"
                    )
                elif name not in declared:
                    problems.append(f"{where}, variable {name!r}: {layer.component} declares {', '.join(declared)}")
            for name, variable in declared.items():
                if name == DURATION_VARIABLE:
                    continue
                if name in layer.variables:
                    why = value_problem(variable, layer.variables[name])
                    if why:
                        problems.append(f"{where}, variable {name!r}: {why}")
                elif variable["type"] in ("string", "number") and not layer.sample:
                    problems.append(
                        f"{where}, variable {name!r} ({variable['type']}): not given. A block's default is a "
                        "sample and never goes on screen: write the value (an empty string if it is not used)"
                    )
    return problems
