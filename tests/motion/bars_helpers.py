"""What the tests of ``split-bar`` and ``compare-bars`` share: the numbers of the tokens, the geometry the page gets, the
block's own ``minSlot`` run in Node, and the texts a run drew."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from tests.motion import node_logic
from tests.motion.block_harness import BlockRun, T, Tween
from tests.motion.block_runner import css_properties

D = T.duration
SAFE_W = T.canvas["width"] - T.safe["left"] - T.safe["right"]
SAFE_H = T.canvas["height"] - T.safe["top"] - T.safe["bottom"]


def js_round(x: float) -> int:
    """``Math.round``: halves go up (Python's ``round`` goes to the even number)."""
    return math.floor(x + 0.5)


def reading(words: int) -> float:
    """The reading time of the tokens: ``read_min`` plus ``read_per_word`` a word."""
    return D["read_min"] + D["read_per_word"] * words


def geometry() -> dict[str, Any]:
    """What the page hands the layout: the safe box and the three type sizes, from the tokens."""
    return {
        "safeW": SAFE_W,
        "safeH": SAFE_H,
        "safeLeft": T.safe["left"],
        "safeTop": T.safe["top"],
        "label": T.sizes["label"],
        "head": T.sizes["body"],
        "note": T.sizes["min_text"],
    }


def layout_of(block: Path, variables: dict[str, Any]) -> dict[str, Any]:
    """The geometry the block's ``layoutFor`` makes of its variables with the tokens' geometry, in one Node run, so an
    error of ``readContent`` or of ``layoutFor`` comes back as the same ``LogicError``."""
    return node_logic.run(
        block,
        f"emit(layoutFor(readContent({json.dumps(variables)}), {json.dumps(geometry())}));",
    )


def page_minimum(block: Path, variables: dict[str, Any]) -> float:
    """The block's own ``minSlot`` run in Node with the values of ``tokens.css``."""
    program = (
        f"const V = {json.dumps(variables)};"
        f"const props = {json.dumps(css_properties())};"
        "const tok = (name) => Number.parseFloat(props[name]);"
        "emit(minSlot(V, tok));"
    )
    return node_logic.run(block, program)


def texts(run: BlockRun) -> list[str]:
    """Every non-empty text the script set."""
    return [el["text"] for el in run.elements.values() if el["text"]]


def with_class(run: BlockRun, name: str) -> list[dict[str, Any]]:
    """The elements the script made whose first class is ``name``."""
    return [el for el in run.elements.values() if el["className"].split(" ")[0] == name]


def tweens_on(run: BlockRun, name: str) -> list[Tween]:
    """The tweens of the elements the script made whose first class is ``name``, in timeline order."""
    ids = {el["id"] for el in with_class(run, name)}
    return [t for t in run.tweens if t.targets[0] in ids]
