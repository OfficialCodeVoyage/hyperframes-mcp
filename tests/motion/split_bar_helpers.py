"""What the tests of ``split-bar`` share: the block, a sample of its variables and builders for parts and markers."""

from __future__ import annotations

import json
from typing import Any

from tests.motion.bars_helpers import layout_of
from tests.motion.block_harness import block_path
from tests.motion.node_logic import call

NAME = "split-bar"
BLOCK = block_path(NAME)

PARTS = [
    {
        "label": "found in Germany",
        "display": "about a third",
        "value": 236,
        "tone": "gold",
        "at": 1.0,
        "seconds": 1.2,
    },
    {
        "label": "went abroad",
        "display": "about two thirds",
        "value": 503,
        "tone": "ember",
        "at": 2.6,
        "seconds": 1.6,
    },
]
MARKER = {
    "label": "the Marburg pile",
    "display": "about a quarter of the ledger",
    "from": 0,
    "to": 210,
    "at": 5.0,
}


def part(label="a part", display="some", value=100, tone="gold", at=0.0, seconds=1.0) -> dict[str, Any]:
    return {
        "label": label,
        "display": display,
        "value": value,
        "tone": tone,
        "at": at,
        "seconds": seconds,
    }


def marker(label="a range", display="some", from_=0, to=10, at=0.0) -> dict[str, Any]:
    return {"label": label, "display": display, "from": from_, "to": to, "at": at}


def variables(parts=None, markers=None, **over: Any) -> dict[str, Any]:
    """The variables of a layer: the two-part sample with the Marburg bracket, with ``over`` on top."""
    base = {
        "wholeLabel": "The Bundesbank gold ledger",
        "wholeValue": "about 791 tonnes",
        "parts": json.dumps(PARTS if parts is None else parts),
        "whole": 791,
        "markers": json.dumps([MARKER] if markers is None else markers),
        "note": "",
    }
    return base | over


def content(parts=None, markers=None, **over: Any) -> dict[str, Any]:
    """The checked content the block's ``readContent`` makes of the variables."""
    return call(BLOCK, "readContent", variables(parts, markers, **over))


def layout(parts=None, markers=None, **over: Any) -> dict[str, Any]:
    """The geometry the block's ``layoutFor`` makes for the variables, with the tokens' safe zone and type sizes."""
    return layout_of(BLOCK, variables(parts, markers, **over))
