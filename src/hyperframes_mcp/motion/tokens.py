"""The design tokens of the motion library: one JSON file, a closed schema, and what is made from it.

``assets/motion/tokens.json`` is the single source of the look ("night and paper"): colours, fonts, type
sizes, the safe zone, eases, durations, camera and finish limits. Components never hold a literal for any
of it: the installed ``tokens.css`` (:func:`stylesheet`) carries the values as CSS custom properties and the
component reads them from there. :func:`contrast_problems` is the check that every text pair stays readable.

The loader is strict on purpose: an unknown or missing key, a colour that is not ``#rrggbb`` and a number that
is not positive are all errors, and every error of a file is reported together with its path.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from hyperframes_mcp.util.paths import ASSETS_DIR

MOTION_DIR = ASSETS_DIR / "motion"
TOKENS_PATH = MOTION_DIR / "tokens.json"
FONTS_DIR = MOTION_DIR / "fonts"
# Where an installed project keeps the font files, relative to the project (and to tokens.css).
FONT_TARGET_DIR = "assets/fonts"

COLOR_NAMES = (
    "night",
    "night_raised",
    "paper",
    "paper_aged",
    "ink",
    "gold",
    "gold_dim",
    "oxblood",
    "verdigris",
    "slate",
    "loss",
    "gain",
    "scrim",
)
FONT_ROLES = ("display", "label", "mono")
GENERIC_FAMILIES = ("serif", "sans-serif", "monospace")

HEX_COLOUR = re.compile(r"^#[0-9A-Fa-f]{6}$")

# section -> key -> kind. "int" and "number" are positive; "ease" is a non-empty string.
_SCHEMA: dict[str, dict[str, str]] = {
    "canvas": {"width": "int", "height": "int", "fps": "int"},
    "sizes": {
        "title": "int",
        "subtitle": "int",
        "label": "int",
        "body": "int",
        "ledger": "int",
        "credit": "int",
        "min_text": "int",
        "min_credit": "int",
    },
    "safe": {"left": "int", "right": "int", "top": "int", "bottom": "int"},
    "ease": {k: "ease" for k in ("enter", "exit", "move", "emphasis")},
    "duration": {
        k: "number"
        for k in (
            "enter",
            "exit",
            "micro",
            "cinematic_min",
            "cinematic_max",
            "group_stagger_total_max",
            "word_stagger",
            "lower_third_hold_min",
            "lower_third_hold_max",
            "read_min",
            "read_per_word",
            "dissolve_min",
            "dissolve_max",
        )
    },
    "camera": {"max_pan_px_per_s": "number", "max_zoom_per_s": "number"},
    "finish": {"grain": "number", "vignette": "number"},
    "limits": {"max_simultaneous_moving": "int", "max_flashes_per_second": "int"},
}
_SECTIONS = (
    "canvas",
    "colors",
    "fonts",
    "sizes",
    "safe",
    "ease",
    "duration",
    "camera",
    "finish",
    "limits",
    "contrast",
)
_FONT_KEYS = {
    "family",
    "generic",
    "role",
    "weights",
    "files",
    "licence",
    "licence_file",
}
_FONT_FILE_KEYS = {"file", "weight", "style"}
_CONTRAST_KEYS = {"min_ratio", "text_pairs", "decorative"}
# Ranges whose lower bound may not exceed the upper one: section, low key, high key.
_ORDERED = (
    ("duration", "cinematic_min", "cinematic_max"),
    ("duration", "lower_third_hold_min", "lower_third_hold_max"),
    ("duration", "dissolve_min", "dissolve_max"),
)


class TokensError(ValueError):
    """The tokens file is not valid; the message lists every problem with its path."""


@dataclass(frozen=True)
class FontFace:
    """One font file and the ``@font-face`` descriptors it is declared with."""

    file: str
    weight: str
    style: str


@dataclass(frozen=True)
class Font:
    """One of the three type roles: its family, generic fallback, files and licence."""

    family: str
    generic: str
    files: tuple[FontFace, ...]
    licence: str
    licence_file: str

    @property
    def stack(self) -> str:
        """The CSS ``font-family`` value of the role: the family, then its generic fallback."""
        return f'"{self.family}", {self.generic}'

    @property
    def asset_files(self) -> tuple[str, ...]:
        """The names of the font files and of the licence text, in ``assets/motion/fonts``."""
        return (*(face.file for face in self.files), self.licence_file)


@dataclass(frozen=True)
class Tokens:
    """The validated tokens."""

    canvas: dict[str, int]
    colors: dict[str, str]
    fonts: dict[str, Font]
    sizes: dict[str, int]
    safe: dict[str, int]
    ease: dict[str, str]
    duration: dict[str, float]
    camera: dict[str, float]
    finish: dict[str, float]
    limits: dict[str, int]
    min_ratio: float
    text_pairs: tuple[tuple[str, str], ...]
    decorative: frozenset[str]


@dataclass(frozen=True)
class ContrastProblem:
    """A text pair under the minimum ratio."""

    fg: str
    bg: str
    ratio: float
    minimum: float

    def __str__(self) -> str:
        return f"{self.fg} on {self.bg} is {self.ratio:.2f}:1, below {self.minimum}:1"


# -- loading ------------------------------------------------------------------------------------


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _closed(raw: Any, keys: set[str], path: str, errors: list[str]) -> bool:
    """True when ``raw`` is an object with exactly ``keys``; otherwise records what is wrong."""
    if not isinstance(raw, dict):
        errors.append(f"{path}: must be an object")
        return False
    for key in sorted(set(raw) - keys):
        errors.append(f"{path}.{key}: unknown key")
    for key in sorted(keys - set(raw)):
        errors.append(f"{path}.{key}: missing")
    return set(raw) == keys


def _section(raw: dict[str, Any], name: str, keys: set[str], errors: list[str]) -> dict[str, Any] | None:
    """The values of a section without its ``_sources`` note, or None when it is absent or its shape is wrong.

    An absent section is reported once, by the caller, as a missing section.
    """
    if name not in raw:
        return None
    body = raw[name]
    if not _closed(body, keys | {"_sources"}, name, errors):
        return None
    note = body["_sources"]
    if not isinstance(note, str) or not note.strip():
        errors.append(f"{name}._sources: must say where the numbers came from")
    return {k: v for k, v in body.items() if k != "_sources"}


def _check_value(kind: str, value: Any, path: str, errors: list[str]) -> None:
    if kind == "ease":
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{path}: must be a non-empty GSAP ease name")
    elif kind == "int":
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            errors.append(f"{path}: must be a positive integer, got {value!r}")
    elif not is_number(value) or value <= 0:
        errors.append(f"{path}: must be a positive number, got {value!r}")


def _parse_font(raw: Any, path: str, errors: list[str]) -> Font | None:
    if not _closed(raw, _FONT_KEYS, path, errors):
        return None
    before = len(errors)
    for key in ("family", "role", "licence", "licence_file"):
        if not isinstance(raw[key], str) or not raw[key].strip():
            errors.append(f"{path}.{key}: must be a non-empty string")
    if raw["generic"] not in GENERIC_FAMILIES:
        errors.append(f"{path}.generic: must be one of {', '.join(GENERIC_FAMILIES)}")
    weights = raw["weights"]
    if (
        not isinstance(weights, list)
        or not weights
        or not all(isinstance(w, int) and not isinstance(w, bool) and 100 <= w <= 900 for w in weights)
    ):
        errors.append(f"{path}.weights: must be a list of weights from 100 to 900")
    faces: list[FontFace] = []
    files = raw["files"]
    if not isinstance(files, list) or not files:
        errors.append(f"{path}.files: must be a non-empty list")
    else:
        for i, face in enumerate(files):
            where = f"{path}.files[{i}]"
            if not _closed(face, _FONT_FILE_KEYS, where, errors):
                continue
            if not all(isinstance(face[k], str) and face[k] for k in _FONT_FILE_KEYS):
                errors.append(f"{where}: file, weight and style must be non-empty strings")
            elif "/" in face["file"] or ".." in face["file"]:
                errors.append(f"{where}.file: must be a bare file name")
            elif face["style"] not in ("normal", "italic"):
                errors.append(f"{where}.style: must be normal or italic")
            else:
                faces.append(FontFace(face["file"], face["weight"], face["style"]))
    if len(errors) > before:
        return None
    return Font(raw["family"], raw["generic"], tuple(faces), raw["licence"], raw["licence_file"])


def _parse_contrast(
    raw: dict[str, Any], colors: set[str], errors: list[str]
) -> tuple[float, tuple[tuple[str, str], ...], frozenset[str]]:
    body = _section(raw, "contrast", _CONTRAST_KEYS, errors)
    if body is None:
        return 4.5, (), frozenset()
    ratio = body["min_ratio"]
    if not is_number(ratio) or ratio < 1:
        errors.append("contrast.min_ratio: must be a number of at least 1")
        ratio = 4.5
    pairs: list[tuple[str, str]] = []
    if not isinstance(body["text_pairs"], list):
        errors.append("contrast.text_pairs: must be a list of [foreground, background]")
    else:
        for i, pair in enumerate(body["text_pairs"]):
            names = pair if isinstance(pair, list) and len(pair) == 2 else None
            if names is None or not all(isinstance(n, str) for n in names):
                errors.append(f"contrast.text_pairs[{i}]: must be [foreground, background]")
                continue
            for name in names:
                if name not in colors:
                    errors.append(f"contrast.text_pairs[{i}]: {name!r} is not a colour")
            pairs.append((names[0], names[1]))
    decorative = body["decorative"]
    if not isinstance(decorative, list) or not all(d in colors for d in decorative):
        errors.append("contrast.decorative: must be a list of colour names")
        decorative = []
    return float(ratio), tuple(pairs), frozenset(decorative)


def parse_tokens(raw: Any) -> Tokens:
    """Validate a decoded tokens document and return it as :class:`Tokens`.

    Raises :class:`TokensError` listing every problem found.
    """
    errors: list[str] = []
    if not isinstance(raw, dict):
        raise TokensError("tokens: the document must be a JSON object")
    for key in sorted(set(raw) - set(_SECTIONS)):
        errors.append(f"{key}: unknown section")
    for key in sorted(set(_SECTIONS) - set(raw)):
        errors.append(f"{key}: missing section")

    values: dict[str, dict[str, Any]] = {}
    for name, keys in _SCHEMA.items():
        body = _section(raw, name, set(keys), errors)
        if body is not None:
            for key, value in body.items():
                _check_value(keys[key], value, f"{name}.{key}", errors)
            values[name] = body

    colors: dict[str, str] = {}
    for key, value in (_section(raw, "colors", set(COLOR_NAMES), errors) or {}).items():
        if isinstance(value, str) and HEX_COLOUR.match(value):
            colors[key] = value
        else:
            errors.append(f"colors.{key}: {value!r} is not a #rrggbb colour")

    fonts: dict[str, Font] = {}
    for role, spec in (_section(raw, "fonts", set(FONT_ROLES), errors) or {}).items():
        font = _parse_font(spec, f"fonts.{role}", errors)
        if font is not None:
            fonts[role] = font

    min_ratio, text_pairs, decorative = _parse_contrast(raw, set(COLOR_NAMES), errors)

    if not errors:
        errors += _cross_checks(values)
    if errors:
        raise TokensError("invalid motion tokens:\n  " + "\n  ".join(errors))
    return Tokens(
        canvas=values["canvas"],
        colors=colors,
        fonts=fonts,
        sizes=values["sizes"],
        safe=values["safe"],
        ease=values["ease"],
        duration=values["duration"],
        camera=values["camera"],
        finish=values["finish"],
        limits=values["limits"],
        min_ratio=min_ratio,
        text_pairs=text_pairs,
        decorative=decorative,
    )


def _cross_checks(values: dict[str, dict[str, Any]]) -> list[str]:
    """Rules that tie two tokens together: type minimums, ordered ranges, fractions."""
    errors: list[str] = []
    sizes = values["sizes"]
    for key, size in sizes.items():
        if key in ("min_text", "min_credit"):
            continue
        floor_key = "min_credit" if key == "credit" else "min_text"
        if size < sizes[floor_key]:
            errors.append(f"sizes.{key}: {size} is below sizes.{floor_key} {sizes[floor_key]}")
    for section, low, high in _ORDERED:
        if values[section][low] > values[section][high]:
            errors.append(f"{section}.{low} is above {section}.{high}")
    for key, value in values["finish"].items():
        if value > 1:
            errors.append(f"finish.{key}: {value} is not a fraction (at most 1)")
    return errors


def load_tokens(path: Path = TOKENS_PATH) -> Tokens:
    """Read and validate a tokens file (the shipped one by default)."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as err:
        raise TokensError(f"{path}: not valid JSON: {err}") from err
    return parse_tokens(raw)


@lru_cache(maxsize=1)
def default_tokens() -> Tokens:
    """The shipped tokens, loaded once."""
    return load_tokens()


# -- CSS ----------------------------------------------------------------------------------------


def _kebab(name: str) -> str:
    return name.replace("_", "-")


_FONT_VARIABLE = re.compile(rf"--font-({'|'.join(FONT_ROLES)})\b")


def font_roles_in(text: str) -> list[str]:
    """The type roles a component's text names through ``--font-<role>``, sorted."""
    return sorted(set(_FONT_VARIABLE.findall(text)))


def css(t: Tokens | None = None) -> str:
    """The ``:root`` block of custom properties a component reads its look from.

    Colours are ``--color-*``, type roles ``--font-*``, sizes and the safe zone ``--size-*`` and ``--safe-*`` in
    pixels, durations ``--dur-*`` in seconds without a unit and eases ``--ease-*`` as GSAP names: the last two are
    for the component's script, which reads them with ``getComputedStyle``, and so are the camera limits
    ``--camera-*`` (px per second, log scale per second) and the finish values ``--finish-*`` (0 to 1).
    """
    t = t or default_tokens()
    lines = [f"  --color-{_kebab(k)}: {v};" for k, v in t.colors.items()]
    lines += [f"  --font-{role}: {font.stack};" for role, font in t.fonts.items()]
    lines += [f"  --size-{_kebab(k)}: {v}px;" for k, v in t.sizes.items()]
    lines += [f"  --safe-{k}: {v}px;" for k, v in t.safe.items()]
    lines += [f"  --dur-{_kebab(k)}: {v:g};" for k, v in t.duration.items()]
    lines += [f"  --ease-{_kebab(k)}: {v};" for k, v in t.ease.items()]
    lines += [f"  --camera-{_kebab(k)}: {v:g};" for k, v in t.camera.items()]
    lines += [f"  --finish-{_kebab(k)}: {v:g};" for k, v in t.finish.items()]
    return ":root {\n" + "\n".join(lines) + "\n}\n"


def font_face_css(t: Tokens | None = None, font_dir: str = FONT_TARGET_DIR) -> str:
    """The ``@font-face`` rules for every font file, with URLs under ``font_dir``."""
    t = t or default_tokens()
    rules = []
    for font in t.fonts.values():
        for face in font.files:
            rules.append(
                "@font-face {\n"
                f'  font-family: "{font.family}";\n'
                f'  src: url("{font_dir}/{face.file}") format("truetype");\n'
                f"  font-weight: {face.weight};\n"
                f"  font-style: {face.style};\n"
                "}\n"
            )
    return "\n".join(rules)


def stylesheet(t: Tokens | None = None) -> str:
    """The whole ``tokens.css``: the custom properties, then the font declarations."""
    return css(t) + "\n" + font_face_css(t)


# -- contrast -----------------------------------------------------------------------------------


def _luminance(hex_colour: str) -> float:
    def channel(value: int) -> float:
        c = value / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (int(hex_colour[i : i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(fg: str, bg: str) -> float:
    """The WCAG 2.x contrast ratio of two ``#rrggbb`` colours, from 1 to 21 (order does not matter)."""
    for value in (fg, bg):
        if not HEX_COLOUR.match(value):
            raise TokensError(f"{value!r} is not a #rrggbb colour")
    light, dark = sorted((_luminance(fg), _luminance(bg)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def contrast_problems(t: Tokens | None = None) -> list[ContrastProblem]:
    """The text pairs that fall under the minimum ratio; a pair with a decorative foreground is skipped."""
    t = t or default_tokens()
    problems = []
    for fg, bg in t.text_pairs:
        if fg in t.decorative:
            continue
        ratio = contrast(t.colors[fg], t.colors[bg])
        if ratio < t.min_ratio:
            problems.append(ContrastProblem(fg, bg, ratio, t.min_ratio))
    return problems
