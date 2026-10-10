"""The preflight of the composer: two checks that fail a bad shot list in seconds instead of after a render.

**Minimum slots.** A block whose shortest slot depends on its text exports it: ``minSlot(V, tok)`` between the
``// logic:begin`` and ``// logic:end`` lines of its script (``V`` the variables the page sees, ``tok(name)`` the number
of a token custom property). This module runs that same text in Node, in one process for the whole shot list, so the
formulas are written only in the blocks (``docs/motion-library.md``). A block that cannot have the
function says why in the registry (``slotNotChecked``) and the plan prints a note.

**Hidden layers.** A block with ``coversFrame`` in the registry paints an opaque ground over the whole frame and hides
every layer under it in the same shot (a higher track is in front, on one track the later layer of the list is). A
hidden layer is a warning; a picture (a block with ``needsCredit`` in the registry: a photograph or a clip) left with no
credit line in sight is an error.
"""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from hyperframes_mcp.motion import registry as registry_module
from hyperframes_mcp.motion import tokens
from hyperframes_mcp.motion.shotlist import CREDIT_BLOCK, DURATION_VARIABLE, EPS

if TYPE_CHECKING:
    from hyperframes_mcp.motion.compose import LayerTiming

LOGIC_BEGIN = "// logic:begin"
LOGIC_END = "// logic:end"
# the tolerance the blocks use when they compare seconds
TOLERANCE = 1e-6
# a layer hidden for less than this long is not worth a message
HIDDEN_MIN_S = 0.3
NODE_TIMEOUT_S = 60

Interval = tuple[float, float]

_PROPERTY = re.compile(r"(--[\w-]+)\s*:\s*([^;]+);")


class PreflightUnavailable(RuntimeError):
    """The minimum slots cannot be computed here (node is missing or failed); the message says why."""


@dataclass(frozen=True)
class SlotResult:
    """The shortest slot of one layer, or the block's own error for variables it would refuse (never both)."""

    seconds: float | None
    error: str | None = None


# -- reading a block --------------------------------------------------------------------------------


def _html(item: registry_module.Item, reg: registry_module.Registry) -> str:
    for entry in item.files:
        if entry.path.endswith(".html"):
            return (reg.root / entry.path).read_text(encoding="utf-8")
    return ""


def block_logic(item: registry_module.Item, reg: registry_module.Registry) -> str | None:
    """The logic region of a block when it defines ``minSlot``, else None."""
    text = _html(item, reg)
    begin, end = text.find(LOGIC_BEGIN), text.find(LOGIC_END)
    if begin < 0 or end < begin:
        return None
    logic = text[begin:end]
    return logic if "function minSlot(" in logic else None


def _properties(block_html: str) -> dict[str, str]:
    """The custom properties a block reads: the generated ``tokens.css`` plus those its own stylesheet declares."""
    values = dict(_PROPERTY.findall(tokens.css()))
    for style in re.findall(r"<style>(.*?)</style>", block_html, re.DOTALL):
        values.update(_PROPERTY.findall(style))
    return {name: value.strip() for name, value in values.items()}


# -- running minSlot in node ------------------------------------------------------------------------

_PROGRAM = """\
"use strict";
const input = JSON.parse(require("fs").readFileSync(0, "utf8"));
const blocks = {
%(blocks)s
};
const answers = input.requests.map(function (request) {
  const properties = input.properties[request.component];
  const tok = function (name) {
    if (properties[name] === undefined) throw new Error("tokens.css is not loaded: " + name + " is empty");
    return Number.parseFloat(properties[name]);
  };
  try {
    return { seconds: blocks[request.component](request.variables, tok) };
  } catch (error) {
    return { error: String(error && error.message ? error.message : error) };
  }
});
process.stdout.write(JSON.stringify(answers));
"""


def minimum_slots(requests: Sequence[tuple[str, dict[str, Any]]], reg: registry_module.Registry) -> list[SlotResult]:
    """The shortest slot of each ``(component, variables)``, by the block's own ``minSlot`` run in one node process.

    Raises:
        PreflightUnavailable: node is missing or failed.
        registry.RegistryError: a component is not in the registry.
        ValueError: a component's block has no ``minSlot``.
    """
    blocks: dict[str, str] = {}
    properties: dict[str, dict[str, str]] = {}
    defaults: dict[str, dict[str, Any]] = {}
    for component, _ in requests:
        if component in blocks:
            continue
        item = reg.get(component)
        logic = block_logic(item, reg)
        if logic is None:
            raise ValueError(f"{component} has no minSlot function")
        blocks[component] = f"{json.dumps(component)}: function (V, tok) {{\n{logic}\nreturn minSlot(V, tok);\n}}"
        properties[component] = _properties(_html(item, reg))
        defaults[component] = {v["id"]: v["default"] for v in item.variables}
    node = shutil.which("node")
    if node is None:
        raise PreflightUnavailable("node is not installed")
    payload = {
        # the page sees the declared defaults under what the layer sets (its readVars)
        "requests": [{"component": c, "variables": {**defaults[c], **v}} for c, v in requests],
        "properties": properties,
    }
    try:
        done = subprocess.run(
            [node, "-e", _PROGRAM % {"blocks": ",\n".join(blocks.values())}],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=NODE_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise PreflightUnavailable(f"node did not run: {error}") from error
    if done.returncode != 0:
        raise PreflightUnavailable(f"node failed: {done.stderr.strip()[:300]}")
    results = []
    for answer in json.loads(done.stdout):
        seconds = answer.get("seconds")
        if isinstance(seconds, (int, float)) and math.isfinite(seconds):
            results.append(SlotResult(float(seconds)))
        else:
            results.append(SlotResult(None, answer.get("error") or "the block returned no number"))
    return results


def check_minimum_slots(
    layers: Sequence[LayerTiming],
    reg: registry_module.Registry,
    problems: list[str],
    warnings: list[str],
    *,
    refused: Sequence[str] = (),
) -> None:
    """Add an error for every layer whose slot is shorter than its block's own minimum, and a note for every block
    whose minimum is not computed.

    ``refused`` are the problems the composer already found with the layers' components and variables (each starts
    with the layer's ``label``): those layers are not run, their variables are known to be wrong.
    """
    checked: list[LayerTiming] = []
    not_checked: dict[str, list[str]] = {}
    has_function: dict[str, bool] = {}
    for layer in layers:
        item = reg.items.get(layer.component)
        if item is None or item.type != "hyperframes:block":
            continue
        if layer.component not in has_function:
            has_function[layer.component] = block_logic(item, reg) is not None
        if has_function[layer.component]:
            if not any(problem.startswith(layer.label) for problem in refused):
                checked.append(layer)
        elif item.slot_not_checked:
            shots = not_checked.setdefault(layer.component, [])
            if layer.shot not in shots:
                shots.append(layer.shot)
    for component, shots in not_checked.items():
        where = ", ".join(repr(shot) for shot in shots)
        warnings.append(f"{component} (shot {where}): not pre-checked: {reg.items[component].slot_not_checked}")
    if not checked:
        return
    try:
        results = minimum_slots([(layer.component, layer.values) for layer in checked], reg)
    except PreflightUnavailable as error:
        names = ", ".join(sorted({layer.component for layer in checked}))
        warnings.append(f"{names}: not pre-checked: {error}; only the static minimum of each slot is checked")
        return
    for layer, result in zip(checked, results, strict=True):
        if result.seconds is None:
            problems.append(f"{layer.label}: {result.error} (the block would throw at render time)")
            continue
        if layer.slot + TOLERANCE >= result.seconds:
            continue
        declared = {v["id"]: v for v in reg.items[layer.component].variables}
        static = declared.get(DURATION_VARIABLE, {}).get("min")
        if static is not None and layer.slot < static - EPS:
            continue  # the composer's check of the static minimum already refused it
        needed = math.ceil(result.seconds * 100 - TOLERANCE) / 100
        problems.append(
            f"{layer.label}: a slot of {layer.slot:g} s is shorter than the {result.seconds:.2f} s "
            f"{layer.component} needs for its text (entrance, the reading time of its words, exit); "
            f"give the layer at least {needed:g} s or shorten the text"
        )


# -- hidden layers ----------------------------------------------------------------------------------


def credits_of(picture: LayerTiming, layers: Sequence[LayerTiming]) -> list[LayerTiming]:
    """The credit lines that satisfy the provenance rule for a picture (a photograph or a clip): in its shot, with
    text, on screen with it."""
    return [
        other
        for other in layers
        if other.shot == picture.shot
        and other.component == CREDIT_BLOCK
        and str(other.values.get("text", "")).strip()
        and picture.overlap(other) > EPS
    ]


def _above(upper: LayerTiming, lower: LayerTiming) -> bool:
    """Higher track is in front; on one track the later layer of the list is (the stack the page is written with)."""
    return upper.depth > lower.depth


def _union(intervals: Sequence[Interval]) -> list[Interval]:
    merged: list[Interval] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _clip(intervals: Sequence[Interval], low: float, high: float) -> list[Interval]:
    return [(max(a, low), min(b, high)) for a, b in intervals if min(b, high) > max(a, low)]


def _minus(low: float, high: float, covered: Sequence[Interval]) -> list[Interval]:
    """What is left of ``[low, high]`` once the (merged, sorted) ``covered`` intervals are taken out."""
    left: list[Interval] = []
    at = low
    for a, b in covered:
        if a > at:
            left.append((at, a))
        at = max(at, b)
    if at < high:
        left.append((at, high))
    return left


def _intersect(first: Sequence[Interval], second: Sequence[Interval]) -> list[Interval]:
    return [(max(a, c), min(b, d)) for a, b in first for c, d in second if min(b, d) > max(a, c)]


def _length(intervals: Sequence[Interval]) -> float:
    return sum(b - a for a, b in intervals)


def check_hidden_layers(
    layers: Sequence[LayerTiming],
    reg: registry_module.Registry,
    problems: list[str],
    warnings: list[str],
) -> None:
    """Warn about every layer hidden for at least ``HIDDEN_MIN_S`` behind a layer that covers the frame, and refuse a
    picture (a block that needs a credit) whose credit line is hidden for all but a fraction of the time it shares
    with the picture."""
    by_shot: dict[str, list[LayerTiming]] = {}
    for layer in layers:
        by_shot.setdefault(layer.shot, []).append(layer)
    for members in by_shot.values():
        opaque = [layer for layer in members if (item := reg.items.get(layer.component)) and item.covers_frame]
        over: dict[int, list[LayerTiming]] = {}
        hidden: dict[int, list[Interval]] = {}
        for layer in members:
            over[layer.index] = [
                other for other in opaque if other is not layer and _above(other, layer) and layer.overlap(other) > 0
            ]
            hidden[layer.index] = _clip(
                _union([(o.start, o.end) for o in over[layer.index]]),
                layer.start,
                layer.end,
            )

        for picture in (m for m in members if (item := reg.items.get(m.component)) and item.needs_credit):
            _check_credit_in_sight(picture, credits_of(picture, members), over, hidden, problems)

        for layer in members:
            seconds = _length(hidden[layer.index])
            if seconds + TOLERANCE < HIDDEN_MIN_S:
                continue
            names = ", ".join(o.short for o in over[layer.index])
            slot = layer.end - layer.start
            if seconds >= slot - EPS:
                warnings.append(
                    f"{layer.label} is hidden for all of its {slot:.2f} s and never shows: {names} paints the "
                    "whole frame above it (a higher track is in front); give it a higher track"
                )
            else:
                warnings.append(
                    f"{layer.label} is hidden for {seconds:.2f} s of its {slot:.2f} s behind {names}, which paints "
                    "the whole frame above it (a higher track is in front); give it a higher track or end it sooner"
                )


def _check_credit_in_sight(
    picture: LayerTiming,
    credits: Sequence[LayerTiming],
    over: dict[int, list[LayerTiming]],
    hidden: dict[int, list[Interval]],
    problems: list[str],
) -> None:
    """The credit line must be seen with the picture: hiding may take part of their shared time, not all of it.

    ``credits`` already share time with the picture (none: ``compose.plan`` reports that instead).
    """
    shown = _minus(picture.start, picture.end, hidden[picture.index])
    sights = [
        (
            _length(
                _intersect(
                    _minus(c.start, c.end, hidden[c.index]),
                    shown,
                )
            ),
            c,
            picture.overlap(c),
        )
        for c in credits
    ]
    if not sights or any(
        seen > EPS and seen >= min(together - EPS, HIDDEN_MIN_S - TOLERANCE) for seen, _, together in sights
    ):
        return
    seen, credit_layer, together = max(sights, key=lambda sight: sight[0])
    names = ", ".join(sorted({o.short for o in (*over[picture.index], *over[credit_layer.index])}))
    problems.append(
        f"{picture.label}: its credit line ({credit_layer.short}) is hidden: it is on screen with the picture "
        f"for only {seen:.2f} s of the {together:.2f} s their slots share, because {names} paints the whole frame "
        "above them; put the credit line on a higher track than that layer, or start that layer later"
    )
