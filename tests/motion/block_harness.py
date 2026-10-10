"""Run a motion block's own script against a stub document, to test the timing logic without a browser.

``run_block`` executes the inline scripts of ``assets/motion/blocks/NAME.html`` in node (``block_harness.js``) with a
stub ``document``, the token custom properties and the block's own as ``getComputedStyle`` values, the given
variables as the mount's overrides, and a recording ``gsap`` whose timeline keeps every ``fromTo``. The result lists
the tweens (target, from, to, start, duration, ease) and the elements the script made or touched, or the error it
threw. No layout and no real GSAP are involved: what is tested is what the script decides (the slot check, the
positions, the line breaks), not how a browser draws it; the frames of a render cover that.

The module also holds what the tests of ``lower-third``, ``act-title`` and ``quote-card`` share: the token timings,
reading the block's file, the static checks of the contract and the motion rules every run must keep.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest

from hyperframes_mcp.motion import registry, tokens

HARNESS = Path(__file__).with_name("block_harness.js")
NODE = shutil.which("node")

T = tokens.default_tokens()
ENTER, EXIT = T.duration["enter"], T.duration["exit"]
GROUP_MAX = T.duration["group_stagger_total_max"]
LINE_GAP = GROUP_MAX / 2

_CUSTOM_PROPERTY = re.compile(r"(--[\w-]+)\s*:\s*([^;]+);")


@dataclass(frozen=True)
class Tween:
    """One ``fromTo`` (or ``set``) the script put on the timeline."""

    kind: str
    targets: tuple[int, ...]
    frm: dict[str, Any]
    to: dict[str, Any]
    start: float
    duration: float
    ease: str | None

    @property
    def end(self) -> float:
        return self.start + self.duration


@dataclass
class BlockRun:
    """What one execution of a block's script did."""

    error: str | None
    registered: list[str]
    paused: list[bool]
    tweens: list[Tween]
    elements: dict[int, dict[str, Any]] = field(default_factory=dict)

    def element(self, selector: str) -> int:
        """Id of the element the script reached with ``document.querySelector(selector)``."""
        (found,) = [i for i, el in self.elements.items() if el["selector"] == selector]
        return found

    def tweens_of(self, element_id: int) -> list[Tween]:
        return [t for t in self.tweens if element_id in t.targets]

    def tween(self, selector: str) -> Tween:
        """The one tween of the element reached with ``selector``."""
        (only,) = self.tweens_of(self.element(selector))
        return only

    def children_with_class(self, name: str) -> list[int]:
        """Ids of the created elements whose ``className`` is ``name``, in creation order."""
        return [i for i, el in self.elements.items() if el["className"] == name]

    def text_of(self, element_id: int) -> str:
        return self.elements[element_id]["text"]

    @property
    def end(self) -> float:
        return max(t.end for t in self.tweens)


# -- reading a block ---------------------------------------------------------------------------------


def block_path(name: str) -> Path:
    return tokens.MOTION_DIR / "blocks" / f"{name}.html"


def html(name: str) -> str:
    return block_path(name).read_text(encoding="utf-8")


def code_of(text: str) -> str:
    """The file without its leading licence comment."""
    return text.split("-->", 1)[1]


def rule_of(name: str, selector: str) -> str:
    """The declarations of the first CSS rule of the block whose selector ends with ``selector``."""
    return re.search(re.escape(selector) + r"\s*\{([^}]*)\}", html(name)).group(1)


def declared_variables(name: str) -> dict[str, dict[str, Any]]:
    """The variables of the block by id (the registry holds the list; a library test ties it to the HTML)."""
    return {v["id"]: v for v in registry.get(name).variables}


def defaults(name: str) -> dict[str, Any]:
    return {key: v["default"] for key, v in declared_variables(name).items()}


def _css_values(name: str) -> dict[str, str]:
    """Token custom properties plus the ones the block's own style declares (they win)."""
    values = dict(_CUSTOM_PROPERTY.findall(tokens.css(T)))
    for css in re.findall(r"<style>(.*?)</style>", html(name), re.DOTALL):
        values.update(_CUSTOM_PROPERTY.findall(css))
    return {k: v.strip() for k, v in values.items()}


def blend(fg: str, bg: str, alpha: float) -> str:
    """``fg`` at ``alpha`` over ``bg``, both ``#RRGGBB``."""
    return "#" + "".join(
        f"{round(int(fg[i : i + 2], 16) * alpha + int(bg[i : i + 2], 16) * (1 - alpha)):02X}" for i in (1, 3, 5)
    )


def entrance_end(count: int, step: float) -> float:
    """When the last thing finishes arriving in act-title and quote-card: two lines in, ``count`` staggered items
    ``step`` apart (squeezed into the group total), then one more line gap and an enter.
    """
    squeezed = min(step, GROUP_MAX / (count - 1)) if count > 1 else 0
    return 2 * LINE_GAP + (count - 1) * squeezed + LINE_GAP + ENTER


# -- running a block ---------------------------------------------------------------------------------


def run_block(name: str, variables: dict[str, Any] | None = None) -> BlockRun:
    """Execute the script of block ``name`` with ``variables`` as the mount's overrides (the declared defaults otherwise).

    The test is skipped when node is not installed.
    """
    if NODE is None:
        pytest.skip("node is not installed")
    done = subprocess.run(
        [NODE, str(HARNESS), str(block_path(name))],
        input=json.dumps({"variables": variables or {}, "css": _css_values(name)}),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    raw = json.loads(done.stdout)
    return BlockRun(
        error=raw["error"],
        registered=raw["registered"],
        paused=raw["paused"],
        tweens=[
            Tween(
                kind=t["kind"],
                targets=tuple(t["targets"]),
                frm=t["from"],
                to=t["to"],
                start=t["start"],
                duration=t["duration"] or 0.0,
                ease=t["ease"],
            )
            for t in raw["tweens"]
        ],
        elements={el["id"]: el for el in raw["elements"]},
    )


# -- static checks of the contract, the same for every block ----------------------------------------


def check_static(name: str) -> None:
    """One paused timeline registered under the id, fromTo only, no literal colour, no markup, no shadow."""
    text = code_of(html(name))
    assert text.count("gsap.timeline(") == 1
    assert "paused: true" in text
    assert f'window.__timelines["{name}"] = tl;' in text
    assert ".fromTo(" in text
    assert not re.search(r"\.(?:to|from|set)\(", text)
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", text)
    assert not re.search(r"\b(?:rgba?|hsla?)\(", text)
    assert "innerHTML" not in text
    assert not re.search(r"(?:box|text|drop)-shadow|filter:", text)


def check_mounted(name: str) -> None:
    """A mounted block's <html> is not in the page and its script sees a scoped document: tokens come from its own
    element, the declaration is read only when it is in the page, and the variables come from ``getVariables()``.
    """
    text = code_of(html(name))
    assert "getComputedStyle(rootEl)" in text
    assert "if (raw)" in text
    assert "getVariables" in text


def check_max_lengths(name: str, constants: dict[str, str]) -> None:
    """The ``maxLength`` the variables declare is the one the script enforces (``constants``: variable -> constant)."""
    declared, text = declared_variables(name), code_of(html(name))
    for variable, constant in constants.items():
        enforced = int(re.search(rf"{constant} = (\d+)", text).group(1))
        assert declared[variable]["maxLength"] == enforced, variable


# -- the motion rules every block keeps, checked on a run ------------------------------------------

# What a tween may animate (the contract's list; scaleX and scaleY are scale).
MOTION_KEYS = {
    "x",
    "y",
    "xPercent",
    "yPercent",
    "scale",
    "scaleX",
    "scaleY",
    "rotation",
    "opacity",
    "clipPath",
    "strokeDashoffset",
    "attr",
}
_TWEEN_SETTINGS = {"duration", "ease", "immediateRender"}
MAX_TRAVEL_PX = 24


def things_moving_at_once(run: BlockRun) -> int:
    """The most things in motion at one moment; the elements a block made as siblings (the words of a title, the
    lines of a quote) are one thing, a staggered group."""

    def thing(tween: Tween) -> int:
        target = tween.targets[0]
        parent = run.elements[target]["parent"]
        return parent if parent is not None else target

    tweens = [t for t in run.tweens if t.kind == "fromTo"]
    edges = sorted({t.start for t in tweens} | {t.end for t in tweens})
    busiest = 0
    for a, b in pairwise(edges):
        middle = (a + b) / 2
        active = {thing(t) for t in tweens if t.start <= middle < t.end}
        busiest = max(busiest, len(active))
    return busiest


def check_calm(run: BlockRun, composition_id: str, duration: float) -> None:
    """Assert the motion rules on one run: one paused timeline registered under the id, fromTo with both ends, only
    the tweenable properties, token eases, travel under 24 px, at most six things moving at once, the first tween at 0,
    and the last one a fade of the whole block that is faster than the entrance and ends exactly with the slot.
    """
    assert run.error is None, run.error
    assert run.registered == [composition_id]
    assert run.paused == [True]

    tweens = [x for x in run.tweens if x.kind == "fromTo"]
    assert tweens and not [x for x in run.tweens if x.kind != "fromTo"]
    assert min(x.start for x in tweens) == 0
    assert run.end == pytest.approx(duration, abs=1e-9)

    for x in tweens:
        assert set(x.frm) == set(x.to) - _TWEEN_SETTINGS, x
        assert set(x.frm) <= MOTION_KEYS, x
        assert x.ease in (T.ease["enter"], T.ease["exit"]), x
        assert abs(x.frm.get("y", 0)) <= MAX_TRAVEL_PX, x
        assert abs(x.frm.get("x", 0)) <= MAX_TRAVEL_PX, x
        assert x.duration > 0, x
    assert things_moving_at_once(run) <= T.limits["max_simultaneous_moving"]

    leave = max(tweens, key=lambda x: x.start)
    assert leave.frm == {"opacity": 1} and leave.to["opacity"] == 0
    assert leave.duration == EXIT < ENTER and leave.ease == T.ease["exit"]
